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
from pathlib import Path

from .actions import ActionQueue

ACTION_TTL = 10 * 60  # nicht ausgeführte Aktionen verfallen nach 10 Minuten
# Nachrichten/Anrufe von Jarvis leben länger (Handy evtl. gerade offline)
ACTION_TTL_BY_TYPE = {"notify": 12 * 3600, "ring": 30 * 60, "file": 24 * 3600, "agent_call": 120, "agent_hangup": 120,
                      "calendar_add": 24 * 3600, "calendar_update": 24 * 3600, "calendar_delete": 24 * 3600}

ACTION_TYPES = {
    "call": "Telefonnummer wählen (number)",
    "sms": "SMS vorbereiten (number, text)",
    "whatsapp": "WhatsApp-Chat mit vorausgefülltem Text öffnen (number, text)",
    "navigate": "Navigation starten (destination)",
    "alarm": "Wecker stellen (hour, minute, label)",
    "timer": "Timer starten (seconds, label)",
    "open_url": "Link öffnen (url)",
    "calendar_add": "Termin im Handy-Kalender anlegen",
    "calendar_update": "Termin im Handy-Kalender ändern",
    "calendar_delete": "Termin im Handy-Kalender löschen",
    "notify": "Benachrichtigung von Jarvis aufs Handy (title, text)",
    "ring": "Jarvis „ruft an“ – Anruf-Bildschirm mit Nachricht (text)",
    "file": "Datei aufs Handy laden (file_id, name)",
    "agent_call": "Jarvis telefoniert selbst über die Zweit-SIM (call_id, number, name)",
    "agent_hangup": "Jarvis-Telefonat beenden (call_id)",
}


class PhoneStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.contacts_file = self.dir / "phone_contacts.json"
        self.actions_file = self.dir / "phone_actions.json"
        self.app_file = self.dir / "phone_app.json"
        self.location_file = self.dir / "phone_location.json"
        self.calendar_file = self.dir / "phone_calendar.json"

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

    # ----------------------------------------------------------- Standort
    def save_location(self, loc: dict) -> dict:
        clean = {
            "lat": round(float(loc["lat"]), 6),
            "lon": round(float(loc["lon"]), 6),
            "accuracy_m": int(float(loc.get("accuracy") or loc.get("accuracy_m") or 0)),
            "address": str(loc.get("address") or "")[:200],
            "time": int(loc.get("time") or time.time()),
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        self.location_file.write_text(json.dumps(clean, ensure_ascii=False))
        return clean

    def location(self) -> dict | None:
        if not self.location_file.exists():
            return None
        loc = json.loads(self.location_file.read_text())
        loc["age_minutes"] = int((time.time() - loc["time"]) / 60)
        return loc

    @staticmethod
    def describe(loc: dict) -> str:
        where = loc.get("address") or "unbekannte Adresse"
        age = loc.get("age_minutes", 0)
        when = "gerade eben" if age < 2 else f"vor {age} Min."
        return (f"{where} ({loc['lat']}, {loc['lon']}, ±{loc['accuracy_m']} m, {when}, "
                f"Karte: https://maps.google.com/?q={loc['lat']},{loc['lon']})")

    # ----------------------------------------------------------- Kalender
    def save_calendar(self, calendars: list[dict], events: list[dict]) -> int:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.calendar_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"updated": int(time.time()), "calendars": calendars, "events": events},
                                  ensure_ascii=False))
        os.replace(tmp, self.calendar_file)
        return len(events)

    def calendar(self) -> dict | None:
        if not self.calendar_file.exists():
            return None
        return json.loads(self.calendar_file.read_text())

    def resolve_calendar(self, name: str = "") -> dict:
        """Kalender nach Name/Konto finden; ohne Name: primärer beschreibbarer Kalender."""
        cal = self.calendar() or {}
        writable = [c for c in cal.get("calendars", []) if c.get("writable")]
        if not writable:
            raise RuntimeError("Kein beschreibbarer Handy-Kalender bekannt (App öffnen, Kalender teilen aktivieren)")
        if name:
            n = name.lower()
            for c in writable:
                if n in (c.get("name", "") + " " + c.get("account", "")).lower():
                    return c
            raise ValueError(f"Kalender '{name}' nicht gefunden. Vorhanden: "
                             + ", ".join(c.get("name", "?") for c in writable))
        return next((c for c in writable if c.get("primary")), writable[0])

    # ------------------------------------------------------------ actions
    @property
    def _queue(self) -> ActionQueue:
        return ActionQueue(self.actions_file, ACTION_TYPES, ACTION_TTL, ACTION_TTL_BY_TYPE)

    def queue(self, action: str, params: dict) -> dict:
        return self._queue.queue(action, params)

    def pending(self) -> list[dict]:
        return self._queue.pending()

    def done(self, action_id: str, result=None, ok: bool = True) -> bool:
        return self._queue.done(action_id, result, ok)
