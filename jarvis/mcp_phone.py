"""MCP-Server `phone`: Handy-Kontakte und Aktionen über die Jarvis-Android-App."""

from __future__ import annotations

import os
from pathlib import Path

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


# ---------------------------------------------------------- Handy-Kalender
TZ = os.getenv("JARVIS_TIMEZONE", "Europe/Berlin")


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(TZ))


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _allday_ms(d: str) -> int:
    """Ganztägige Termine speichert Android als UTC-Mitternacht."""
    return _ms(datetime.combine(date.fromisoformat(d[:10]), datetime.min.time(), tzinfo=timezone.utc))


def _fmt(ms: int, all_day: bool) -> str:
    if all_day:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()
    return datetime.fromtimestamp(ms / 1000, ZoneInfo(TZ)).isoformat(timespec="minutes")


def _no_calendar() -> dict:
    return {"hinweis": "Kein Handy-Kalender synchronisiert. In der Jarvis-App: Einstellungen → "
                       "„Handy-Kalender mit Jarvis teilen“ aktivieren und App einmal öffnen."}


@mcp.tool()
def phone_calendar_list() -> list[dict] | dict:
    """Kalender auf dem Handy (Google, Outlook, … – alles, was Android synchronisiert)."""
    cal = store.calendar()
    if not cal:
        return _no_calendar()
    return [{"name": c.get("name"), "account": c.get("account"), "writable": c.get("writable"),
             "primary": c.get("primary", False)} for c in cal.get("calendars", [])]


@mcp.tool()
def phone_calendar_events(start: str = "", end: str = "", query: str = "", calendar: str = "") -> dict:
    """Termine aus dem Handy-Kalender (ISO-Zeiten, z.B. '2026-10-01T00:00'); ohne Angabe: nächste 7 Tage.
    query filtert nach Titel/Ort, calendar nach Kalendername."""
    cal = store.calendar()
    if not cal:
        return _no_calendar()
    t0 = _parse(start) if start else datetime.now(ZoneInfo(TZ))
    t1 = _parse(end) if end else t0 + timedelta(days=7)
    a, b = _ms(t0), _ms(t1)
    names = {c["id"]: c.get("name", "") for c in cal.get("calendars", [])}
    out = []
    for e in cal.get("events", []):
        if e["end"] <= a or e["start"] >= b:
            continue
        cname = names.get(e.get("calendar_id"), "")
        if calendar and calendar.lower() not in cname.lower():
            continue
        if query and query.lower() not in (e.get("title", "") + " " + e.get("location", "")).lower():
            continue
        out.append({"event_id": e["event_id"], "title": e.get("title", "(ohne Titel)"),
                    "start": _fmt(e["start"], e.get("all_day")), "end": _fmt(e["end"], e.get("all_day")),
                    "all_day": bool(e.get("all_day")), "location": e.get("location", ""),
                    "description": (e.get("description") or "")[:500], "calendar": cname})
    out.sort(key=lambda x: x["start"])
    age = int((datetime.now().timestamp() - cal.get("updated", 0)) / 60)
    return {"events": out, "stand": f"vor {age} Min. synchronisiert" if age else "gerade synchronisiert"}


@mcp.tool()
def phone_calendar_add(title: str, start: str, end: str = "", all_day: bool = False, location: str = "",
                       description: str = "", calendar: str = "", reminder_minutes: int | None = None) -> dict:
    """Legt einen Termin im Handy-Kalender an (wird mit Google/Outlook synchronisiert).
    start/end ISO ('2026-10-02T14:00'); ohne end = 1 Stunde. Ganztägig: all_day=True, start='2026-10-02'.
    reminder_minutes=0 → Erinnerung zur Startzeit."""
    c = store.resolve_calendar(calendar)
    if all_day:
        s = _allday_ms(start)
        e = _allday_ms(end) if end else s + 86_400_000
    else:
        s = _ms(_parse(start))
        e = _ms(_parse(end)) if end else s + 3_600_000
    params = {"calendar_id": c["id"], "title": title, "start": s, "end": e, "all_day": all_day,
              "location": location, "description": description, "reminder_minutes": reminder_minutes,
              "timezone": "UTC" if all_day else TZ}
    return {"queued": store.queue("calendar_add", params), "calendar": c.get("name"), "note": _NOTE}


@mcp.tool()
def phone_calendar_update(event_id: int, title: str = "", start: str = "", end: str = "",
                          location: str = "", description: str = "") -> dict:
    """Ändert einen Termin im Handy-Kalender (event_id aus phone_calendar_events). Nur angegebene Felder."""
    params: dict = {"event_id": event_id}
    if title:
        params["title"] = title
    if start:
        params["start"] = _ms(_parse(start))
    if end:
        params["end"] = _ms(_parse(end))
    if location:
        params["location"] = location
    if description:
        params["description"] = description
    return {"queued": store.queue("calendar_update", params), "note": _NOTE}


@mcp.tool()
def phone_calendar_delete(event_id: int) -> dict:
    """Löscht einen Termin aus dem Handy-Kalender. Nur nach Bestätigung durch den Nutzer."""
    return {"queued": store.queue("calendar_delete", {"event_id": event_id}), "note": _NOTE}


@mcp.tool()
def phone_notify(text: str, title: str = "Jarvis") -> dict:
    """Schickt eine Benachrichtigung aufs Handy (auch wenn die App geschlossen ist,
    sofern dort „Im Hintergrund verbunden bleiben“ an ist). Für Infos ohne Eile."""
    return {"queued": store.queue("notify", {"title": title, "text": text}),
            "note": "Erscheint als Benachrichtigung auf dem Handy."}


@mcp.tool()
def phone_ring(text: str) -> dict:
    """Jarvis „ruft an“: Das Handy klingelt mit Anruf-Bildschirm. Nimmt der Nutzer an, wird
    `text` vorgelesen und Jarvis hört zu. NUR für Dringendes oder wenn du eine Entscheidung
    brauchst (z.B. wichtige Mail, Termin gleich, Rückfrage) – nicht für Routine-Infos."""
    return {"queued": store.queue("ring", {"text": text}),
            "note": "Das Handy klingelt (verfällt nach 30 Min., falls offline)."}


# ------------------------------------------------- Jarvis telefoniert selbst
_CALLS = bool(os.getenv("CALL_AUDIO_IN") and os.getenv("CALL_AUDIO_OUT"))


def _calls():
    from .calls import CallStore

    return CallStore(store.dir)


@mcp.tool(enabled=_CALLS)
def phone_agent_call(number: str, goal: str, contact_name: str = "", max_minutes: int = 6) -> dict:
    """Jarvis ruft SELBST an und führt das Gespräch (über die Zweit-SIM im Handy, Stimme „Thorsten“).
    Vorher IMMER mit dem Nutzer Nummer, Ziel und erlaubte Zusagen bestätigen. `goal` ist der komplette
    Auftrag fürs Gespräch: worum es geht, welche Infos Jarvis weitergeben darf (z.B. Name, Rückrufnummer),
    was vereinbart werden soll und was nicht (z.B. „Termin Sa vormittag, notfalls Fr ab 16 Uhr; nichts bezahlen“).
    Jarvis stellt sich am Anfang automatisch als KI-Assistent vor. Notrufe/Sondernummern sind gesperrt.
    Das Ergebnis kommt als Benachrichtigung aufs Handy; Stand mit phone_agent_call_status."""
    call = _calls().create(number, goal, contact_name, max_minutes)
    store.queue("agent_call", {"call_id": call["id"], "number": call["number"], "name": contact_name})
    return {"call_id": call["id"], "status": "wird gewählt",
            "hinweis": "Läuft im Hintergrund. Dem Nutzer kurz sagen, dass du anrufst und dich mit dem Ergebnis meldest."}


@mcp.tool(enabled=_CALLS)
def phone_agent_call_status(call_id: str = "") -> dict:
    """Stand/Ergebnis eines Jarvis-Telefonats (ohne call_id: das letzte) inkl. Gesprächsprotokoll."""
    call = _calls().get(call_id) if call_id else _calls().latest()
    if not call:
        return {"hinweis": "Kein Anruf gefunden"}
    return {k: call.get(k) for k in ("id", "name", "number", "goal", "status", "result", "error", "transcript")}


@mcp.tool(enabled=_CALLS)
def phone_agent_hangup(call_id: str = "") -> dict:
    """Bricht ein laufendes Jarvis-Telefonat ab."""
    calls = _calls()
    call = calls.get(call_id) if call_id else calls.latest()
    if not call:
        return {"hinweis": "Kein Anruf gefunden"}
    calls.update(call["id"], abort=True)
    store.queue("agent_hangup", {"call_id": call["id"]})
    return {"ok": True, "call_id": call["id"]}


# ----------------------------------------------------------------- Dateien
def _files():
    from .files import FileStore

    ws = Path(os.getenv("JARVIS_WORKSPACE", Path(__file__).resolve().parent.parent / "workspace"))
    return FileStore(ws, store.dir)


@mcp.tool()
def jarvis_files() -> list[dict]:
    """Dateien bei Jarvis: vom Handy geteilt, vom PC geholt (pc_fetch_file) usw. – neueste zuerst.
    `workspace_path` kannst du mit dem Read-Tool lesen (auch PDFs/Bilder)."""
    return _files().list()[:30]


def _telegram_send(path: Path, caption: str) -> int:
    import httpx

    token, users = os.getenv("TELEGRAM_BOT_TOKEN", ""), [u for u in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if u.strip()]
    if not token or not users:
        return 0
    sent = 0
    for uid in users:
        with open(path, "rb") as f:
            r = httpx.post(f"https://api.telegram.org/bot{token}/sendDocument", timeout=120,
                           data={"chat_id": uid.strip(), "caption": caption[:1000]}, files={"document": (path.name, f)})
        sent += r.status_code == 200
    return sent


@mcp.tool()
def phone_send_file(file_id: str, via: str = "auto", caption: str = "") -> dict:
    """Schickt eine Jarvis-Datei aufs Handy. via: 'app' (landet in Downloads/Jarvis auf dem Handy),
    'telegram' (als Dokument im Telegram-Chat) oder 'auto' (App, wenn verbunden, sonst Telegram).
    Dateien vom PC vorher mit pc_fetch_file holen."""
    files = _files()
    meta = files.get(file_id)
    if not meta:
        raise ValueError(f"Datei {file_id} nicht gefunden (jarvis_files zeigt alle)")
    out: dict = {"file": meta["name"]}
    if via in ("app", "auto") and store.app_registered():
        out["app"] = store.queue("file", {"file_id": file_id, "name": meta["name"], "size_kb": meta["size_kb"]})["id"]
        out["note"] = "Die App lädt die Datei nach Downloads/Jarvis (sofort, wenn verbunden)."
    if via == "telegram" or (via == "auto" and "app" not in out):
        n = _telegram_send(files.path(meta), caption or meta["name"])
        out["telegram"] = f"an {n} Chat(s) gesendet" if n else "Telegram nicht eingerichtet"
    if "app" not in out and not out.get("telegram", "").startswith("an "):
        raise RuntimeError("Kein Weg aufs Handy: Jarvis-App nie verbunden und Telegram nicht eingerichtet")
    return out


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
