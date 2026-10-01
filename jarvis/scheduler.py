"""Routinen: Morgen-Briefing und regelmäßiger Posteingangs-Check mit Push aufs Handy.

Achtung: jede Routine verbraucht Nachrichten deines Abo-Kontingents. Intervalle
daher lieber großzügig wählen (z.B. 60 Minuten).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from .brain import Brain
from .config import Config

log = logging.getLogger("jarvis.scheduler")
Notifier = Callable[[str], Awaitable[None]]

NOTHING = "NICHTS"

BRIEFING_PROMPT = """Erstelle mein Morgen-Briefing:
1. Heutige und morgige Termine (mit Uhrzeit, Ort, ggf. Vorbereitung).
2. Wichtige ungelesene Mails der letzten 24h aus allen Mailkonten – kurz, mit Handlungsbedarf.
3. Offene Punkte aus meinen Notizen (todo.md), falls vorhanden.
Halte es kompakt und gut auf dem Handy lesbar."""

INBOX_PROMPT = f"""Prüfe alle meine Mailkonten auf neue ungelesene Mails seit {{since}}
(Gmail: 'in:inbox is:unread after:{{epoch}}'; IMAP: mail_search mit unread=True, since_days=1, pro Konto).
Melde dich NUR bei wirklich Wichtigem (persönliche Mails von echten Menschen, Fristen, Rechnungen, Termine, Sicherheitswarnungen).
Newsletter, Werbung und Benachrichtigungen ignorierst du.
Wenn es nichts Wichtiges gibt, antworte exakt mit: {NOTHING}
Sonst: kurze Zusammenfassung pro Mail + Vorschlag, was ich tun sollte. Nichts senden, nichts löschen.
Ist etwas wirklich dringend (Frist heute, Notfall, wichtige Person wartet), ruf mich zusätzlich mit phone_ring an."""


def parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        h, m = value.strip().split(":")
        return int(h), int(m)
    except ValueError:
        return None


def in_quiet_hours(now: datetime, spec: str) -> bool:
    try:
        start, end = (int(x) for x in spec.split("-"))
    except ValueError:
        return False
    h = now.hour
    return (start <= h or h < end) if start > end else (start <= h < end)


def next_daily(now: datetime, hh: int, mm: int) -> datetime:
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


async def ntfy_notifier(url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(url, content=text.encode(), headers={"Title": "Jarvis"})


class Scheduler:
    def __init__(self, cfg: Config, brain: Brain, notifiers: list[Notifier]):
        self.cfg = cfg
        self.brain = brain
        self.notifiers = list(notifiers)
        if cfg.ntfy_url:
            self.notifiers.append(lambda t: ntfy_notifier(cfg.ntfy_url, t))
        # Jarvis-App als Push-Kanal, sobald sie einmal verbunden war
        from .phone import PhoneStore

        phone = PhoneStore(cfg.data_dir)

        async def phone_notifier(text: str) -> None:  # Name wird in alert() geprüft
            if phone.app_registered():
                title, _, body = text.partition("\n")
                phone.queue("notify", {"title": title.strip()[:80] or "Jarvis", "text": (body or text).strip()[:3000]})

        self.notifiers.append(phone_notifier)
        self.tz = ZoneInfo(cfg.timezone)
        self._tasks: list[asyncio.Task] = []
        self._last_inbox = datetime.now(self.tz)

    async def notify(self, text: str) -> None:
        for n in self.notifiers:
            try:
                await n(text)
            except Exception:
                log.exception("Benachrichtigung fehlgeschlagen")

    async def run_routine(self, name: str, prompt: str) -> str:
        conv = f"routine:{name}"
        self.brain.reset(conv)  # jede Routine frisch, damit der Kontext klein bleibt
        reply = await self.brain.ask(prompt, conv, channel=f"routine-{name}")
        return reply.text

    async def briefing(self) -> None:
        text = await self.run_routine("briefing", BRIEFING_PROMPT)
        await self.notify("☀️ Guten Morgen!\n\n" + text)

    async def inbox_check(self) -> None:
        since = self._last_inbox
        self._last_inbox = datetime.now(self.tz)
        prompt = INBOX_PROMPT.format(since=since.strftime("%d.%m. %H:%M"), epoch=int(since.timestamp()))
        text = await self.run_routine("inbox", prompt)
        if text.strip().strip(".").upper() != NOTHING:
            await self.notify("📬 " + text)

    # ------------------------------------------------------- Daueraufträge
    async def alert(self, text: str, mode: str) -> None:
        """Meldung eines Auftrags: 'ring' lässt das Handy klingeln (wenn die App verbunden ist)."""
        from .phone import PhoneStore

        phone = PhoneStore(self.cfg.data_dir)
        if mode == "ring" and phone.app_registered():
            phone.queue("ring", {"text": text[:600]})
            # zusätzlich über die anderen Kanäle (Telegram …), aber ohne doppelte App-Nachricht
            for n in self.notifiers:
                if getattr(n, "__name__", "") != "phone_notifier":
                    try:
                        await n(text)
                    except Exception:
                        log.exception("Benachrichtigung fehlgeschlagen")
        else:
            await self.notify(text)

    async def run_task(self, t: dict) -> None:
        from .tasks import TaskStore, html_text, page_fingerprint

        store = TaskStore(self.cfg.data_dir, self.cfg.timezone)
        name = t["name"]
        extra: dict = {}
        page = ""
        if t.get("watch_url"):
            try:
                async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={
                        "User-Agent": "Mozilla/5.0 (Jarvis-Waechter)"}) as client:
                    r = await client.get(t["watch_url"])
                page = html_text(r.text)[:200_000]
            except Exception as e:  # noqa: BLE001
                store.finish_run(t["id"], f"Seite nicht erreichbar: {e}", False)
                return
            fp = page_fingerprint(page)
            changed = fp != t.get("last_hash")
            extra["last_hash"] = fp
            word = t.get("watch_contains", "")
            if word:
                hit = word.lower() in page.lower()
                if not hit:
                    store.finish_run(t["id"], f"„{word}“ noch nicht gefunden", False, **extra)
                    return
                if not t.get("once") and not changed and t.get("last_hash"):
                    store.finish_run(t["id"], "unverändert (bereits gemeldet)", False, **extra)
                    return
                if not t.get("instruction"):  # reiner Suchbegriff → ganz ohne Claude melden
                    i = page.lower().index(word.lower())
                    snippet = page[max(0, i - 150): i + 250]
                    msg = f"🔔 {name}: Auf {t['watch_url']} steht jetzt „{word}“.\n…{snippet}…"
                    await self.alert(msg, "ring" if t.get("alert") == "ring" else "notify")
                    store.finish_run(t["id"], msg, True, **extra)
                    return
            elif t.get("last_hash") and not changed:
                store.finish_run(t["id"], "unverändert", False, **extra)
                return
            elif not t.get("last_hash") and not t.get("instruction"):
                store.finish_run(t["id"], "Stand gespeichert", False, **extra)
                return

        mode = t.get("alert", "auto")
        how = {"ring": "Wenn etwas zu melden ist, ruf mich mit phone_ring an (oder antworte mit der Meldung).",
               "notify": "Wenn etwas zu melden ist, antworte mit der Meldung (sie kommt als Benachrichtigung).",
               "auto": "Wenn etwas zu melden ist, antworte mit der Meldung; ist es dringend, ruf mich zusätzlich "
                       "mit phone_ring an."}.get(mode, "")
        prompt = (f"Automatischer Auftrag „{name}“ (id {t['id']}), von mir eingerichtet.\n"
                  f"Auftrag: {t.get('instruction') or 'Melde, dass der Suchbegriff gefunden wurde.'}\n")
        if page:
            prompt += f"\nAktueller Inhalt von {t['watch_url']} (gekürzt):\n{page[:12_000]}\n"
        prompt += f"\nIst die Bedingung nicht erfüllt bzw. gibt es nichts zu melden, antworte exakt: {NOTHING}\n{how}"
        reply = await self.run_routine(f"task-{t['id']}", prompt)
        triggered = reply.strip().strip(".").upper() != NOTHING
        if triggered:
            await self.alert(f"🔔 {name}: {reply}", "ring" if mode == "ring" else "notify")
        store.finish_run(t["id"], reply, triggered, **extra)

    async def _tasks_loop(self) -> None:
        from .tasks import TaskStore

        store = TaskStore(self.cfg.data_dir, self.cfg.timezone)
        while True:
            for t in store.due():
                try:
                    await self.run_task(t)
                except Exception:
                    log.exception("Auftrag %s fehlgeschlagen", t.get("name"))
                    store.finish_run(t["id"], "Fehler beim Ausführen", False)
            await asyncio.sleep(30)

    async def _daily_loop(self, hhmm: tuple[int, int]) -> None:
        while True:
            now = datetime.now(self.tz)
            await asyncio.sleep((next_daily(now, *hhmm) - now).total_seconds())
            try:
                await self.briefing()
            except Exception:
                log.exception("Briefing fehlgeschlagen")
            await asyncio.sleep(61)

    async def _interval_loop(self, minutes: int) -> None:
        while True:
            await asyncio.sleep(minutes * 60)
            if in_quiet_hours(datetime.now(self.tz), self.cfg.quiet_hours):
                continue
            try:
                await self.inbox_check()
            except Exception:
                log.exception("Inbox-Check fehlgeschlagen")

    def start(self) -> None:
        # Daueraufträge laufen immer (Meldungen landen mindestens in der App)
        self._tasks.append(asyncio.create_task(self._tasks_loop()))
        if not self.notifiers:
            log.info("Keine Push-Kanäle (Telegram/ntfy) – Routinen deaktiviert")
            return
        hhmm = parse_hhmm(self.cfg.briefing_time) if self.cfg.briefing_time else None
        if hhmm:
            self._tasks.append(asyncio.create_task(self._daily_loop(hhmm)))
            log.info("Morgen-Briefing täglich um %02d:%02d", *hhmm)
        if self.cfg.inbox_check_minutes > 0:
            self._tasks.append(asyncio.create_task(self._interval_loop(self.cfg.inbox_check_minutes)))
            log.info("Posteingang-Check alle %d Minuten", self.cfg.inbox_check_minutes)

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
