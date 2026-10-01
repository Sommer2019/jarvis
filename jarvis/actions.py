"""Datei-basierte Aufgaben-Warteschlange für Geräte (Handy-App, PC-Agent).

Jarvis' Tools (eigene MCP-Prozesse) legen Aufgaben ab, der Web-Server reicht sie
an die Geräte weiter, die Geräte melden das Ergebnis zurück. Deshalb liegt alles
in einer JSON-Datei mit Sperre statt im Speicher.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

try:  # Linux, macOS, Termux
    import fcntl
except ImportError:  # Windows
    fcntl = None

KEEP_DONE = 10 * 60  # erledigte Aufgaben (mit Ergebnis) so lange aufheben


class ActionQueue:
    def __init__(self, path: Path, types: dict[str, str], ttl: int = 600, ttl_by_type: dict[str, int] | None = None):
        self.path = Path(path)
        self.types = types
        self.ttl = ttl
        self.ttl_by_type = ttl_by_type or {}

    @contextmanager
    def _items(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(self.path.with_suffix(".lock"), "w")
        try:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                items = json.loads(self.path.read_text()) if self.path.exists() else []
            except json.JSONDecodeError:
                items = []
            now = time.time()
            items = [a for a in items if self._alive(a, now)]
            yield items
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False))
            os.replace(tmp, self.path)
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def _alive(self, a: dict, now: float) -> bool:
        if a.get("done"):
            return now - a.get("done_at", now) < KEEP_DONE
        return now - a["created"] < self.ttl_by_type.get(a["type"], self.ttl)

    def queue(self, action: str, params: dict, device: str | None = None) -> dict:
        if action not in self.types:
            raise ValueError(f"Unbekannte Aktion '{action}'. Erlaubt: {', '.join(self.types)}")
        item = {"id": uuid.uuid4().hex[:12], "type": action, "params": params, "created": time.time()}
        if device:
            item["device"] = device
        with self._items() as items:
            items.append(item)
        return item

    def pending(self, device: str | None = None) -> list[dict]:
        with self._items() as items:
            return [a for a in items if not a.get("done")
                    and (device is None or a.get("device") in (None, device))]

    def done(self, action_id: str, result=None, ok: bool = True) -> bool:
        with self._items() as items:
            for a in items:
                if a["id"] == action_id and not a.get("done"):
                    a.update(done=True, done_at=time.time(), ok=ok, result=result)
                    return True
        return False

    def get(self, action_id: str) -> dict | None:
        with self._items() as items:
            return next((dict(a) for a in items if a["id"] == action_id), None)

    def wait(self, action_id: str, timeout: float = 15.0, interval: float = 0.3) -> dict | None:
        """Wartet, bis das Gerät die Aufgabe erledigt hat. None = Zeitüberschreitung."""
        end = time.time() + timeout
        while time.time() < end:
            a = self.get(action_id)
            if a is None or a.get("done"):
                return a
            time.sleep(interval)
        return None
