"""MCP-Server für CalDAV (Kalender) und CardDAV (Kontakte).

Funktioniert mit Nextcloud, iCloud, mailbox.org, Posteo, Fastmail, Radicale,
Baïkal, Synology usw. Die Tools erscheinen in Claude Code als `mcp__dav__<name>`.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timedelta
from functools import lru_cache
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

from .mcp_common import JarvisMCP

logging.getLogger("httpx").setLevel(logging.WARNING)

TZ = os.getenv("JARVIS_TIMEZONE", "Europe/Berlin")

CALDAV_URL = os.getenv("CALDAV_URL", "")
CALDAV_USER = os.getenv("CALDAV_USERNAME", "")
CALDAV_PASS = os.getenv("CALDAV_PASSWORD", "")
CARDDAV_URL = os.getenv("CARDDAV_URL", "")
CARDDAV_USER = os.getenv("CARDDAV_USERNAME", "") or CALDAV_USER
CARDDAV_PASS = os.getenv("CARDDAV_PASSWORD", "") or CALDAV_PASS

mcp = JarvisMCP("dav")


# ============================================================ CalDAV (Kalender)
def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(TZ))
    return dt


@lru_cache(maxsize=1)
def _principal():
    import caldav

    if not CALDAV_URL:
        raise RuntimeError("CALDAV_URL ist nicht gesetzt (siehe README → CalDAV)")
    client = caldav.DAVClient(url=CALDAV_URL, username=CALDAV_USER or None, password=CALDAV_PASS or None)
    return client.principal()


def _calendars():
    return _principal().calendars()


def _calendar(name: str = ""):
    cals = _calendars()
    if not cals:
        raise RuntimeError("Keine Kalender auf dem CalDAV-Server gefunden")
    default = os.getenv("CALDAV_DEFAULT_CALENDAR", "")
    wanted = (name or default).lower()
    if wanted:
        for c in cals:
            if (c.get_display_name() or "").lower() == wanted or str(c.url).rstrip("/").endswith(wanted):
                return c
        raise ValueError(f"Kalender '{name or default}' nicht gefunden. Vorhanden: "
                         + ", ".join(c.get_display_name() or str(c.url) for c in cals))
    return cals[0]


def _fmt(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(TZ))
        return value.astimezone(ZoneInfo(TZ)).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


def summarize_vevent(comp, calendar_name: str = "") -> dict:
    """icalendar-VEVENT → schlankes dict."""
    start = comp.get("dtstart")
    end = comp.get("dtend")
    s = start.dt if start else None
    return {
        "uid": str(comp.get("uid", "")),
        "summary": str(comp.get("summary", "(ohne Titel)")),
        "start": _fmt(s),
        "end": _fmt(end.dt if end else None),
        "all_day": isinstance(s, date) and not isinstance(s, datetime),
        "location": str(comp.get("location", "")),
        "description": str(comp.get("description", ""))[:1000],
        "calendar": calendar_name,
    }


def build_vevent(summary: str, start: str, end: str = "", description: str = "", location: str = "",
                 all_day: bool = False, reminder_minutes: int | None = None, uid: str = "") -> str:
    from icalendar import Alarm, Calendar, Event

    ev = Event()
    ev.add("uid", uid or f"{uuid.uuid4()}@jarvis")
    ev.add("dtstamp", datetime.now(ZoneInfo("UTC")))
    ev.add("summary", summary)
    if all_day:
        d0 = date.fromisoformat(start[:10])
        d1 = date.fromisoformat(end[:10]) if end else d0 + timedelta(days=1)
        ev.add("dtstart", d0)
        ev.add("dtend", d1)
    else:
        s = parse_time(start)
        e = parse_time(end) if end else s + timedelta(hours=1)
        ev.add("dtstart", s)
        ev.add("dtend", e)
    if description:
        ev.add("description", description)
    if location:
        ev.add("location", location)
    if reminder_minutes is not None:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", summary)
        alarm.add("trigger", timedelta(minutes=-reminder_minutes))
        ev.add_component(alarm)
    cal = Calendar()
    cal.add("prodid", "-//Jarvis//DE")
    cal.add("version", "2.0")
    cal.add_component(ev)
    return cal.to_ical().decode()


def _find_event(uid: str, calendar_name: str = ""):
    cals = [_calendar(calendar_name)] if calendar_name else _calendars()
    for c in cals:
        try:
            return c.event_by_uid(uid)
        except Exception:  # noqa: BLE001 – nicht in diesem Kalender
            continue
    raise ValueError(f"Termin mit UID {uid} nicht gefunden")


@mcp.tool(enabled=bool(CALDAV_URL))
def caldav_list_calendars() -> list[dict]:
    """Listet alle CalDAV-Kalender."""
    return [{"name": c.get_display_name(), "url": str(c.url)} for c in _calendars()]


@mcp.tool(enabled=bool(CALDAV_URL))
def caldav_list_events(start: str = "", end: str = "", calendar: str = "", query: str = "") -> list[dict]:
    """Termine im Zeitraum (ISO, z.B. '2026-10-01T00:00'); ohne Angabe: nächste 7 Tage.
    Ohne `calendar` werden alle Kalender durchsucht. `query` filtert nach Text im Titel/Ort."""
    t0 = parse_time(start) if start else datetime.now(ZoneInfo(TZ))
    t1 = parse_time(end) if end else t0 + timedelta(days=7)
    cals = [_calendar(calendar)] if calendar else _calendars()
    out = []
    for c in cals:
        name = c.get_display_name() or ""
        for obj in c.search(start=t0, end=t1, event=True, expand=True):
            for comp in obj.icalendar_instance.walk("VEVENT"):
                item = summarize_vevent(comp, name)
                if query and query.lower() not in (item["summary"] + " " + item["location"]).lower():
                    continue
                out.append(item)
    return sorted(out, key=lambda e: e["start"])


@mcp.tool(enabled=bool(CALDAV_URL))
def caldav_create_event(summary: str, start: str, end: str = "", description: str = "", location: str = "",
                        all_day: bool = False, reminder_minutes: int | None = None, calendar: str = "") -> dict:
    """Legt einen Termin an. start/end ISO ('2026-10-02T14:00'); ohne end = 1 Stunde.
    Ganztägig: all_day=True, start='2026-10-02'. reminder_minutes=0 → Alarm zur Startzeit."""
    ical = build_vevent(summary, start, end, description, location, all_day, reminder_minutes)
    cal = _calendar(calendar)
    ev = cal.save_event(ical)
    comp = ev.icalendar_instance.walk("VEVENT")[0]
    return {**summarize_vevent(comp, cal.get_display_name() or ""), "status": "angelegt"}


@mcp.tool(enabled=bool(CALDAV_URL))
def caldav_update_event(uid: str, summary: str = "", start: str = "", end: str = "", description: str = "",
                        location: str = "", calendar: str = "") -> dict:
    """Ändert einen Termin (nur angegebene Felder). Verschieben: start (und ggf. end) setzen –
    ohne end bleibt die Dauer erhalten."""
    ev = _find_event(uid, calendar)
    comp = ev.icalendar_component
    if start:
        old_s, old_e = comp.get("dtstart").dt, comp.get("dtend").dt if comp.get("dtend") else None
        new_s = parse_time(start)
        comp["dtstart"].dt = new_s
        if end:
            comp["dtend"].dt = parse_time(end)
        elif old_e is not None and isinstance(old_s, datetime):
            comp["dtend"].dt = new_s + (old_e - old_s)
    elif end:
        comp["dtend"].dt = parse_time(end)
    for key, val in (("summary", summary), ("description", description), ("location", location)):
        if val:
            if key in comp:
                del comp[key]
            comp.add(key, val)
    ev.save()
    return {**summarize_vevent(comp), "status": "geändert"}


@mcp.tool(enabled=bool(CALDAV_URL))
def caldav_delete_event(uid: str, calendar: str = "") -> dict:
    """Löscht einen Termin. Vorher beim Nutzer nachfragen."""
    _find_event(uid, calendar).delete()
    return {"uid": uid, "status": "gelöscht"}


# ========================================================= CardDAV (Kontakte)
NS = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav"}


class CardDAV:
    """Minimaler CardDAV-Client (RFC 6352) mit automatischer Adressbuch-Erkennung."""

    def __init__(self, url: str, username: str = "", password: str = ""):
        if not url:
            raise RuntimeError("CARDDAV_URL ist nicht gesetzt (siehe README → CardDAV)")
        self.url = url if url.endswith("/") else url + "/"
        auth = (username, password) if username else None
        self.http = httpx.Client(auth=auth, timeout=30, follow_redirects=True)
        self._books: list[str] | None = None

    def _xml(self, method: str, url: str, body: str, depth: str) -> ET.Element:
        r = self.http.request(method, url, content=body.encode(),
                              headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
        if r.status_code not in (200, 207):
            raise RuntimeError(f"CardDAV {method} {url}: HTTP {r.status_code}")
        return ET.fromstring(r.content)

    def _propfind(self, url: str, props: str, depth: str = "0") -> ET.Element:
        body = f'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:card="{NS["card"]}"><d:prop>{props}</d:prop></d:propfind>'
        return self._xml("PROPFIND", url, body, depth)

    def _href(self, root: ET.Element, path: str) -> str | None:
        el = root.find(f".//{path}/d:href", NS)
        return el.text if el is not None else None

    def addressbooks(self) -> list[str]:
        if self._books is not None:
            return self._books
        root = self._propfind(self.url, "<d:resourcetype/><d:current-user-principal/>")
        # 1) URL ist bereits ein Adressbuch
        if root.find(".//d:resourcetype/card:addressbook", NS) is not None:
            self._books = [self.url]
            return self._books
        # 2) Principal → addressbook-home-set → Adressbücher
        principal = self._href(root, "d:current-user-principal")
        if not principal:
            wk = self._propfind(urljoin(self.url, "/.well-known/carddav"), "<d:current-user-principal/>")
            principal = self._href(wk, "d:current-user-principal")
        home = None
        if principal:
            p = self._propfind(urljoin(self.url, principal), "<card:addressbook-home-set/>")
            home = self._href(p, "card:addressbook-home-set")
        home_url = urljoin(self.url, home) if home else self.url
        listing = self._propfind(home_url, "<d:resourcetype/><d:displayname/>", depth="1")
        books = []
        for resp in listing.findall("d:response", NS):
            if resp.find(".//d:resourcetype/card:addressbook", NS) is not None:
                books.append(urljoin(home_url, resp.find("d:href", NS).text))
        if not books:
            raise RuntimeError(f"Kein Adressbuch unter {self.url} gefunden")
        self._books = books
        return books

    def cards(self) -> list[tuple[str, str, str]]:
        """Alle vCards: (href, etag, vcard-text)."""
        body = ('<?xml version="1.0"?><card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
                "<d:prop><d:getetag/><card:address-data/></d:prop></card:addressbook-query>")
        out = []
        for book in self.addressbooks():
            root = self._xml("REPORT", book, body, "1")
            for resp in root.findall("d:response", NS):
                data = resp.find(".//card:address-data", NS)
                if data is None or not (data.text or "").strip():
                    continue
                etag = resp.find(".//d:getetag", NS)
                out.append((urljoin(book, resp.find("d:href", NS).text),
                            etag.text if etag is not None else "", data.text))
        return out

    def put(self, href: str, vcard: str, etag: str = "") -> None:
        headers = {"Content-Type": "text/vcard; charset=utf-8"}
        headers["If-Match" if etag else "If-None-Match"] = etag or "*"
        r = self.http.put(href, content=vcard.encode(), headers=headers)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"Speichern fehlgeschlagen: HTTP {r.status_code}")

    def delete(self, href: str, etag: str = "") -> None:
        r = self.http.delete(href, headers={"If-Match": etag} if etag else {})
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Löschen fehlgeschlagen: HTTP {r.status_code}")


@lru_cache(maxsize=1)
def carddav() -> CardDAV:
    return CardDAV(CARDDAV_URL, CARDDAV_USER, CARDDAV_PASS)


def parse_vcard(text: str) -> dict:
    import vobject

    v = vobject.readOne(text)

    def values(name: str) -> list[str]:
        return [str(c.value) for c in v.contents.get(name, [])]

    def typed(name: str) -> list[dict]:
        out = []
        for c in v.contents.get(name, []):
            types = c.params.get("TYPE", [])
            out.append({"value": str(c.value), "type": ",".join(types).lower()})
        return out

    org = v.contents.get("org", [])
    adr = []
    for a in v.contents.get("adr", []):
        val = a.value
        adr.append(", ".join(p for p in [val.street, f"{val.code} {val.city}".strip(), val.country] if p))
    return {
        "uid": values("uid")[0] if "uid" in v.contents else "",
        "name": values("fn")[0] if "fn" in v.contents else "",
        "emails": typed("email"),
        "phones": typed("tel"),
        "organization": " ".join(org[0].value) if org and isinstance(org[0].value, list) else (str(org[0].value) if org else ""),
        "addresses": adr,
        "birthday": values("bday")[0] if "bday" in v.contents else "",
        "note": values("note")[0][:500] if "note" in v.contents else "",
    }


def build_vcard(name: str, emails: list[str] | None = None, phones: list[str] | None = None,
                organization: str = "", note: str = "", birthday: str = "", uid: str = "") -> str:
    import vobject

    v = vobject.vCard()
    v.add("version").value = "3.0"
    v.add("uid").value = uid or str(uuid.uuid4())
    v.add("fn").value = name
    parts = name.rsplit(" ", 1)
    v.add("n").value = vobject.vcard.Name(family=parts[-1] if len(parts) > 1 else name,
                                          given=parts[0] if len(parts) > 1 else "")
    for e in emails or []:
        v.add("email").value = e
    for p in phones or []:
        v.add("tel").value = p
    if organization:
        v.add("org").value = [organization]
    if note:
        v.add("note").value = note
    if birthday:
        v.add("bday").value = birthday
    return v.serialize()


def _matches(c: dict, q: str) -> bool:
    q = q.lower().strip()
    digits = "".join(ch for ch in q if ch.isdigit())
    hay = " ".join([c["name"], c["organization"], c["note"]] + [e["value"] for e in c["emails"]]).lower()
    if q in hay:
        return True
    if len(digits) >= 4:
        return any(digits in "".join(ch for ch in p["value"] if ch.isdigit()) for p in c["phones"])
    return all(part in hay for part in q.split())


def _all_contacts() -> list[tuple[str, str, dict]]:
    out = []
    for href, etag, text in carddav().cards():
        try:
            out.append((href, etag, parse_vcard(text)))
        except Exception:  # noqa: BLE001 – kaputte vCard überspringen
            continue
    return out


@mcp.tool(enabled=bool(CARDDAV_URL))
def contacts_search(query: str, max_results: int = 10) -> list[dict]:
    """Sucht Kontakte nach Name, Firma, E-Mail oder Telefonnummer. Liefert E-Mails, Nummern,
    Adressen, Geburtstag. Nutze das, um z.B. die Mailadresse von 'Max' herauszufinden."""
    hits = [c for _, _, c in _all_contacts() if _matches(c, query)]
    return hits[:max_results]


@mcp.tool(enabled=bool(CARDDAV_URL))
def contacts_upcoming_birthdays(days: int = 14) -> list[dict]:
    """Geburtstage in den nächsten N Tagen."""
    today = datetime.now(ZoneInfo(TZ)).date()
    out = []
    for _, _, c in _all_contacts():
        b = c["birthday"].replace("--", "1900-")[:10]
        try:
            bd = date.fromisoformat(b if "-" in b else f"{b[:4]}-{b[4:6]}-{b[6:8]}")
        except ValueError:
            continue
        nxt = bd.replace(year=today.year)
        if nxt < today:
            nxt = nxt.replace(year=today.year + 1)
        if (nxt - today).days <= days:
            out.append({"name": c["name"], "date": nxt.isoformat(), "in_days": (nxt - today).days,
                        "turns": nxt.year - bd.year if bd.year > 1900 else None})
    return sorted(out, key=lambda x: x["in_days"])


@mcp.tool(enabled=bool(CARDDAV_URL))
def contacts_create(name: str, emails: list[str] | None = None, phones: list[str] | None = None,
                    organization: str = "", note: str = "", birthday: str = "") -> dict:
    """Legt einen neuen Kontakt an (birthday im Format JJJJ-MM-TT)."""
    uid = str(uuid.uuid4())
    href = urljoin(carddav().addressbooks()[0], f"{uid}.vcf")
    carddav().put(href, build_vcard(name, emails, phones, organization, note, birthday, uid))
    return {"uid": uid, "name": name, "status": "angelegt"}


@mcp.tool(enabled=bool(CARDDAV_URL))
def contacts_update(uid: str, add_emails: list[str] | None = None, add_phones: list[str] | None = None,
                    name: str = "", organization: str = "", note: str = "", birthday: str = "") -> dict:
    """Ergänzt/ändert einen bestehenden Kontakt (uid aus contacts_search)."""
    import vobject

    for href, etag, text in carddav().cards():
        v = vobject.readOne(text)
        if "uid" in v.contents and str(v.uid.value) == uid:
            break
    else:
        raise ValueError(f"Kontakt {uid} nicht gefunden")
    for e in add_emails or []:
        v.add("email").value = e
    for p in add_phones or []:
        v.add("tel").value = p
    for key, val in (("fn", name), ("note", note), ("bday", birthday)):
        if val:
            if key in v.contents:
                getattr(v, key).value = val
            else:
                v.add(key).value = val
    if organization:
        if "org" in v.contents:
            v.org.value = [organization]
        else:
            v.add("org").value = [organization]
    carddav().put(href, v.serialize(), etag)
    return {**parse_vcard(v.serialize()), "status": "geändert"}


@mcp.tool(enabled=bool(CARDDAV_URL))
def contacts_delete(uid: str) -> dict:
    """Löscht einen Kontakt. Nur nach ausdrücklicher Bestätigung."""
    for href, etag, c in _all_contacts():
        if c["uid"] == uid:
            carddav().delete(href, etag)
            return {"uid": uid, "status": "gelöscht"}
    raise ValueError(f"Kontakt {uid} nicht gefunden")


if __name__ == "__main__":
    mcp.run()
