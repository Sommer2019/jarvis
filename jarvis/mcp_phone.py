"""MCP-Server `phone`: Handy-Kontakte und Aktionen über die Jarvis-Android-App."""

from __future__ import annotations

import os
from pathlib import Path

from .mcp_common import JarvisMCP
from .phone import PhoneStore

store = PhoneStore(Path(os.getenv("JARVIS_DATA", Path(__file__).resolve().parent.parent / "data")))
mcp = JarvisMCP("phone")

_NOTE = ("Die Aktion wird von der Jarvis-App auf dem Handy ausgeführt – sofort, wenn die Anfrage "
         "aus der App kommt, sonst beim nächsten Öffnen der App (verfällt nach 10 Minuten). "
         "Anrufe/SMS/WhatsApp werden nur vorbereitet; der Nutzer tippt selbst auf Senden bzw. Anrufen.")


@mcp.tool()
def phone_contacts_search(query: str, max_results: int = 10) -> list[dict]:
    """Sucht in den Kontakten des Handys (Name, E-Mail oder Nummer)."""
    if not store.contacts_file.exists():
        return [{"hinweis": "Noch keine Handy-Kontakte synchronisiert (in der App: Einstellungen → Kontakte teilen)."}]
    return store.search_contacts(query, max_results)


@mcp.tool()
def phone_location() -> dict:
    """Letzter bekannter Standort des Handys (Adresse, Koordinaten, Alter in Minuten).
    Nur verwenden, wenn der Standort für die Aufgabe relevant ist."""
    loc = store.location()
    if not loc:
        return {"hinweis": "Kein Standort bekannt (in der App: Einstellungen → Standort teilen)."}
    return loc | {"beschreibung": store.describe(loc)}


@mcp.tool()
def phone_call(number: str) -> dict:
    """Öffnet die Telefon-App mit dieser Nummer."""
    return {"queued": store.queue("call", {"number": number}), "note": _NOTE}


@mcp.tool()
def phone_sms(number: str, text: str) -> dict:
    """Bereitet eine SMS an die Nummer vor."""
    return {"queued": store.queue("sms", {"number": number, "text": text}), "note": _NOTE}


@mcp.tool()
def phone_whatsapp(number: str, text: str = "") -> dict:
    """Öffnet WhatsApp mit einem Chat an diese Nummer (internationales Format, z.B. +49170…)
    und vorausgefülltem Text."""
    return {"queued": store.queue("whatsapp", {"number": number, "text": text}), "note": _NOTE}


@mcp.tool()
def phone_navigate(destination: str) -> dict:
    """Startet die Navigation (Google Maps) zu Adresse oder Ort."""
    return {"queued": store.queue("navigate", {"destination": destination}), "note": _NOTE}


@mcp.tool()
def phone_alarm(hour: int, minute: int, label: str = "") -> dict:
    """Stellt einen Wecker auf dem Handy."""
    return {"queued": store.queue("alarm", {"hour": hour, "minute": minute, "label": label}), "note": _NOTE}


@mcp.tool()
def phone_timer(seconds: int, label: str = "") -> dict:
    """Startet einen Timer auf dem Handy."""
    return {"queued": store.queue("timer", {"seconds": seconds, "label": label}), "note": _NOTE}


@mcp.tool()
def phone_open_url(url: str) -> dict:
    """Öffnet einen Link auf dem Handy."""
    return {"queued": store.queue("open_url", {"url": url}), "note": _NOTE}


if __name__ == "__main__":
    mcp.run()
