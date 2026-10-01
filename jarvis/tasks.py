"""Daueraufträge, die Jarvis selbst anlegt („wenn … dann …“, „jeden Montag …“).

Arten:
- Zeitplan: alle N Minuten, täglich um HH:MM (optional nur an bestimmten Wochentagen)
  oder einmalig zu einem Zeitpunkt → Jarvis führt die Anweisung aus.
- Webseiten-Wächter (watch_url): Die Seite wird regelmäßig geladen – OHNE Claude.
  Claude wird nur gefragt, wenn sich die Seite geändert hat bzw. der Suchbegriff
  auftaucht. Ist nur ein Suchbegriff gesetzt, meldet sich Jarvis sogar ganz ohne Claude.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

MIN_PROMPT_MINUTES = 15  # Claude-Aufträge höchstens alle 15 Min. (Abo-Kontingent schonen)
MIN_WATCH_MINUTES = 2    # reines Laden einer Seite kostet nichts
WEEKDAYS = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6,
            "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_weekdays(spec: str) -> list[int]:
    """'mo-fr', 'sa,so', 'mo,mi,fr' → [0..6]"""
    out: set[int] = set()
    for part in re.split(r"[,\s]+", (spec or "").lower().strip()):
        if not part:
            continue
        if "-" in part:
            a, b = (WEEKDAYS[x[:3] if x[:3] in WEEKDAYS else x[:2]] for x in part.split("-", 1))
            out.update(range(a, b + 1) if a <= b else list(range(a, 7)) + list(range(0, b + 1)))
        else:
            out.add(WEEKDAYS[part[:3] if part[:3] in WEEKDAYS else part[:2]])
    return sorted(out)


def html_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    return re.sub(r"\s+", " ", text).strip()


class TaskStore:
    def __init__(self, data_dir: Path, tz: str = "Europe/Berlin"):
        self.path = Path(data_dir) / "tasks.json"
        self.tz = ZoneInfo(tz)

    # -------------------------------------------------------------- Datei
    @contextmanager
    def _tasks(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(self.path.with_suffix(".lock"), "w")
        try:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                tasks = json.loads(self.path.read_text()) if self.path.exists() else []
            except json.JSONDecodeError:
                tasks = []
            yield tasks
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=1))
            os.replace(tmp, self.path)
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def all(self) -> list[dict]:
        with self._tasks() as tasks:
            return [dict(t) for t in tasks]

    def get(self, task_id: str) -> dict | None:
        return next((t for t in self.all() if t["id"] == task_id), None)

    # ----------------------------------------------------------- Anlegen
    def create(self, name: str, instruction: str = "", every_minutes: int = 0, daily_at: str = "",
               weekdays: str = "", at: str = "", watch_url: str = "", watch_contains: str = "",
               once: bool | None = None, alert: str = "auto") -> dict:
        if not (every_minutes or daily_at or at or watch_url):
            raise ValueError("Zeitplan fehlt: every_minutes, daily_at, at oder watch_url angeben")
        if not instruction and not watch_contains:
            raise ValueError("instruction (was Jarvis tun/prüfen soll) fehlt")
        if alert not in ("auto", "ring", "notify"):
            raise ValueError("alert muss auto, ring oder notify sein")
        if daily_at and not re.fullmatch(r"\d{1,2}:\d{2}", daily_at):
            raise ValueError("daily_at im Format HH:MM")
        if watch_url and not watch_url.startswith(("http://", "https://")):
            watch_url = "https://" + watch_url
        if watch_url:
            every_minutes = max(every_minutes or 30, MIN_WATCH_MINUTES)
        elif every_minutes:
            every_minutes = max(every_minutes, MIN_PROMPT_MINUTES)
        at_ts = None
        if at:
            dt = datetime.fromisoformat(at)
            at_ts = (dt if dt.tzinfo else dt.replace(tzinfo=self.tz)).timestamp()
        task = {
            "id": uuid.uuid4().hex[:8], "name": name[:80], "instruction": instruction[:2000],
            "every_minutes": int(every_minutes or 0), "daily_at": daily_at,
            "weekdays": parse_weekdays(weekdays) if weekdays else [],
            "at": at_ts, "watch_url": watch_url, "watch_contains": watch_contains.strip(),
            # „wenn irgendwann …“ (Wächter, einmalige Termine) → nach dem Auslösen beenden
            "once": bool(once) if once is not None else bool(watch_url or at),
            "alert": alert, "paused": False, "created": time.time(),
            "last_run": None, "runs": 0, "triggered": 0, "last_result": "", "last_hash": "",
        }
        task["next_run"] = self.next_run(task, time.time(), first=True)
        with self._tasks() as tasks:
            if len(tasks) >= 50:
                raise RuntimeError("Maximal 50 Aufträge – bitte alte löschen (task_delete)")
            tasks.append(task)
        return task

    def update(self, task_id: str, **fields) -> dict:
        with self._tasks() as tasks:
            for t in tasks:
                if t["id"] == task_id:
                    t.update({k: v for k, v in fields.items() if v is not None})
                    if "paused" in fields and not fields["paused"]:
                        t["next_run"] = self.next_run(t, time.time(), first=True)
                    return dict(t)
        raise ValueError(f"Auftrag {task_id} nicht gefunden")

    def delete(self, task_id: str) -> bool:
        with self._tasks() as tasks:
            before = len(tasks)
            tasks[:] = [t for t in tasks if t["id"] != task_id]
            return len(tasks) < before

    # ---------------------------------------------------------- Zeitplan
    def next_run(self, t: dict, now: float, first: bool = False) -> float | None:
        if t.get("at"):
            return t["at"] if (first or not t.get("last_run")) else None
        if t.get("every_minutes"):
            return now + (60 if first else t["every_minutes"] * 60)
        if t.get("daily_at"):
            hh, mm = (int(x) for x in t["daily_at"].split(":"))
            base = datetime.fromtimestamp(now, self.tz)
            cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            for _ in range(8):
                if cand.timestamp() > now and (not t.get("weekdays") or cand.weekday() in t["weekdays"]):
                    return cand.timestamp()
                cand += timedelta(days=1)
        return None

    def due(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [t for t in self.all() if not t.get("paused") and t.get("next_run") and t["next_run"] <= now]

    def finish_run(self, task_id: str, result: str, triggered: bool, **extra) -> dict | None:
        """Nach einem Lauf: Statistik, nächster Termin; einmalige Aufträge nach Auslösen beenden."""
        now = time.time()
        with self._tasks() as tasks:
            for t in tasks:
                if t["id"] != task_id:
                    continue
                t.update(extra)
                t["last_run"] = now
                t["runs"] = t.get("runs", 0) + 1
                t["last_result"] = (result or "")[:500]
                if triggered:
                    t["triggered"] = t.get("triggered", 0) + 1
                t["next_run"] = self.next_run(t, now)
                if (triggered and t.get("once")) or t["next_run"] is None:
                    tasks.remove(t)
                    return None
                return dict(t)
        return None

    def describe(self, t: dict) -> str:
        if t.get("watch_url"):
            what = f"prüft {t['watch_url']} alle {t['every_minutes']} Min."
            if t.get("watch_contains"):
                what += f" auf „{t['watch_contains']}“"
        elif t.get("at"):
            what = "einmalig am " + datetime.fromtimestamp(t["at"], self.tz).strftime("%d.%m. %H:%M")
        elif t.get("daily_at"):
            days = "".join("MDMDFSS"[d] for d in t["weekdays"]) if t.get("weekdays") else "täglich"
            what = f"{days} um {t['daily_at']}"
        else:
            what = f"alle {t['every_minutes']} Min."
        nxt = datetime.fromtimestamp(t["next_run"], self.tz).strftime("%d.%m. %H:%M") if t.get("next_run") else "–"
        return f"{what}; nächster Lauf {nxt}" + (" (pausiert)" if t.get("paused") else "")


def page_fingerprint(text: str) -> str:
    # Ziffernfolgen (Uhrzeiten, Zähler) weglassen, damit nicht jede Sekunde „geändert“ ist
    stable = re.sub(r"\d+", "#", text)
    return hashlib.sha256(stable.encode()).hexdigest()[:16]
