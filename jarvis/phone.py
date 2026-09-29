"""Brücke zum Handy (Jarvis-Android-App).

- Die App lädt die Handy-Kontakte hoch → data/phone_contacts.json
- Jarvis legt Aktionen (anrufen, SMS, WhatsApp, Navigation, Wecker …) in eine
  Warteschlange → data/phone_actions.json; die App holt sie ab und führt sie aus.

Wird vom Web-Server und vom MCP-Server `phone` gemeinsam genutzt (Datei-basiert,
weil der MCP-Server ein eigener Prozess ist).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ACTION_TTL = 10 * 60  # nicht ausgeführte Aktionen verfallen nach 10 Minuten

ACTION_TYPES = {
    "call": "Telefonnummer wählen (number)",
    "sms": "SMS vorbereiten (number, text)",
    "whatsapp": "WhatsApp-Chat mit vorausgefülltem Text öffnen (number, text)",
    "navigate": "Navigation starten (destination)",
    "alarm": "Wecker stellen (hour, minute, label)",
    "timer": "Timer starten (seconds, label)",
    "open_url": "Link öffnen (url)",
}


class PhoneStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.contacts_file = self.dir / "phone_contacts.json"
        self.actions_file = self.dir / "phone_actions.json"
        self.app_file = self.dir / "phone_app.json"

    # ---------------------------------------------------------------- app
    def register_app(self, info: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.app_file.write_text(json.dumps({**info, "last_seen": int(time.time())}))

    def app_registered(self) -> bool:
        return self.app_file.exists()

    # ----------------------------------------------------------- contacts
    def save_contacts(self, contacts: list[dict]) -> int:
        clean = []
        for c in contacts:
            name = str(c.get("name", "")).strip()
            if not name:
                continue
            clean.append({
                "name": name,
                "phones": [str(p) for p in c.get("phones", [])][:10],
                "emails": [str(e) for e in c.get("emails", [])][:10],
            })
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.contacts_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"updated": int(time.time()), "contacts": clean}, ensure_ascii=False))
        os.replace(tmp, self.contacts_file)
        return len(clean)

    def contacts(self) -> list[dict]:
        if not self.contacts_file.exists():
            return []
        return json.loads(self.contacts_file.read_text()).get("contacts", [])

    def search_contacts(self, query: str, limit: int = 10) -> list[dict]:
        q = query.lower().strip()
        digits = "".join(ch for ch in q if ch.isdigit())
        out = []
        for c in self.contacts():
            hay = (c["name"] + " " + " ".join(c["emails"])).lower()
            hit = q in hay or all(p in hay for p in q.split())
            if not hit and len(digits) >= 4:
                hit = any(digits in "".join(ch for ch in p if ch.isdigit()) for p in c["phones"])
            if hit:
                out.append(c)
        return out[:limit]

    # ------------------------------------------------------------ actions
    @contextmanager
    def _actions(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        items = json.loads(self.actions_file.read_text()) if self.actions_file.exists() else []
        now = time.time()
        items = [a for a in items if now - a["created"] < ACTION_TTL and not a.get("done")]
        yield items
        tmp = self.actions_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False))
        os.replace(tmp, self.actions_file)

    def queue(self, action: str, params: dict) -> dict:
        if action not in ACTION_TYPES:
            raise ValueError(f"Unbekannte Aktion '{action}'. Erlaubt: {', '.join(ACTION_TYPES)}")
        item = {"id": uuid.uuid4().hex[:12], "type": action, "params": params, "created": time.time()}
        with self._actions() as items:
            items.append(item)
        return item

    def pending(self) -> list[dict]:
        with self._actions() as items:
            return list(items)

    def done(self, action_id: str) -> bool:
        with self._actions() as items:
            before = len(items)
            items[:] = [a for a in items if a["id"] != action_id]
            return len(items) < before
