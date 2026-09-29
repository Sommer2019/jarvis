"""MCP-Server für Gmail und Google Kalender.

Wird von Claude Code als Unterprozess gestartet (siehe brain.py). Die Tools
erscheinen dort als `mcp__google__<name>`.
"""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import lru_cache
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

from .mcp_common import JarvisMCP

DATA = Path(os.getenv("JARVIS_DATA", Path(__file__).resolve().parent.parent / "data"))
TOKEN_FILE = Path(os.getenv("GOOGLE_TOKEN_FILE", DATA / "google_token.json"))
TZ = os.getenv("JARVIS_TIMEZONE", "Europe/Berlin")
ALLOW_SEND = os.getenv("JARVIS_ALLOW_SEND_EMAIL", "false").lower() in {"1", "true", "yes", "ja"}
MAX_BODY = 15_000

mcp = JarvisMCP("google")


# --------------------------------------------------------------------- helpers
@lru_cache(maxsize=1)
def _services():
    from googleapiclient.discovery import build

    from .google_auth import load_credentials

    creds = load_credentials(TOKEN_FILE)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return gmail, cal


def gmail():
    return _services()[0]


def calendar():
    return _services()[1]


def header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", "", html))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract_body(payload: dict) -> str:
    """Liefert den Text einer Mail (bevorzugt text/plain, sonst HTML → Text)."""
    plain, html = [], []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(_decode(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    if html:
        return html_to_text("\n".join(html))
    return ""


def attachments(payload: dict) -> list[str]:
    names = []

    def walk(part: dict) -> None:
        if part.get("filename"):
            names.append(part["filename"])
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return names


def summarize_message(msg: dict) -> dict:
    p = msg.get("payload", {})
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "from": header(p, "From"),
        "to": header(p, "To"),
        "subject": header(p, "Subject"),
        "date": header(p, "Date"),
        "snippet": unescape(msg.get("snippet", "")),
        "unread": "UNREAD" in msg.get("labelIds", []),
        "labels": msg.get("labelIds", []),
    }


def build_mime(to: str, subject: str, body: str, cc: str = "", bcc: str = "",
               in_reply_to: str = "", references: str = "") -> str:
    m = EmailMessage()
    m["To"] = to
    if cc:
        m["Cc"] = cc
    if bcc:
        m["Bcc"] = bcc
    m["Subject"] = subject
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = (references + " " + in_reply_to).strip()
    m.set_content(body)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


def _reply_fields(reply_to_message_id: str, subject: str, to: str) -> tuple[dict, str, str]:
    """Holt Thread/Header für eine Antwort."""
    orig = gmail().users().messages().get(
        userId="me", id=reply_to_message_id, format="metadata",
        metadataHeaders=["Message-ID", "References", "Subject", "From", "Reply-To"],
    ).execute()
    p = orig["payload"]
    subj = subject or header(p, "Subject")
    if not subj.lower().startswith("re:"):
        subj = "Re: " + subj
    to = to or header(p, "Reply-To") or header(p, "From")
    extra = {"in_reply_to": header(p, "Message-ID"), "references": header(p, "References")}
    return {"threadId": orig["threadId"], **extra}, subj, to


def _compose(to: str, subject: str, body: str, cc: str, bcc: str, reply_to_message_id: str) -> dict:
    thread = {}
    mime_extra = {}
    if reply_to_message_id:
        info, subject, to = _reply_fields(reply_to_message_id, subject, to)
        thread = {"threadId": info["threadId"]}
        mime_extra = {"in_reply_to": info["in_reply_to"], "references": info["references"]}
    if not to:
        raise ValueError("Empfänger fehlt")
    return {"raw": build_mime(to, subject, body, cc, bcc, **mime_extra), **thread}


def parse_time(value: str) -> datetime:
    """ISO-Zeit parsen; ohne Zeitzone wird die lokale Zeitzone angenommen."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(TZ))
    return dt


def event_time(value: str, all_day: bool) -> dict:
    if all_day:
        return {"date": value[:10]}
    return {"dateTime": parse_time(value).isoformat(), "timeZone": TZ}


def summarize_event(ev: dict) -> dict:
    start = ev.get("start", {})
    end = ev.get("end", {})
    return {
        "id": ev.get("id"),
        "summary": ev.get("summary", "(ohne Titel)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": "date" in start,
        "location": ev.get("location", ""),
        "description": (ev.get("description") or "")[:1000],
        "attendees": [a.get("email") for a in ev.get("attendees", [])],
        "link": ev.get("htmlLink"),
    }


# ------------------------------------------------------------------ gmail tools
@mcp.tool()
def gmail_search(query: str = "in:inbox is:unread", max_results: int = 15) -> list[dict]:
    """Sucht Mails mit Gmail-Suchsyntax, z.B. 'is:unread newer_than:1d', 'from:chef@firma.de',
    'subject:Rechnung', 'in:inbox'. Liefert Absender, Betreff, Datum, Vorschau und IDs."""
    svc = gmail()
    res = svc.users().messages().list(userId="me", q=query, maxResults=min(max_results, 50)).execute()
    out = []
    for m in res.get("messages", []):
        full = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        out.append(summarize_message(full))
    return out


@mcp.tool()
def gmail_read(message_id: str) -> dict:
    """Liest eine Mail vollständig (Text, Header, Anhangsnamen)."""
    msg = gmail().users().messages().get(userId="me", id=message_id, format="full").execute()
    info = summarize_message(msg)
    body = extract_body(msg["payload"])
    info["body"] = body[:MAX_BODY] + ("\n[… gekürzt]" if len(body) > MAX_BODY else "")
    info["cc"] = header(msg["payload"], "Cc")
    info["attachments"] = attachments(msg["payload"])
    return info


@mcp.tool()
def gmail_read_thread(thread_id: str) -> list[dict]:
    """Liest einen kompletten Mail-Verlauf."""
    th = gmail().users().threads().get(userId="me", id=thread_id, format="full").execute()
    out = []
    for msg in th.get("messages", []):
        info = summarize_message(msg)
        info["body"] = extract_body(msg["payload"])[:5000]
        out.append(info)
    return out


@mcp.tool()
def gmail_create_draft(to: str = "", subject: str = "", body: str = "", cc: str = "", bcc: str = "",
                       reply_to_message_id: str = "") -> dict:
    """Erstellt einen Entwurf. Für Antworten reply_to_message_id angeben (dann sind to/subject optional)."""
    msg = _compose(to, subject, body, cc, bcc, reply_to_message_id)
    d = gmail().users().drafts().create(userId="me", body={"message": msg}).execute()
    return {"draft_id": d["id"], "status": "Entwurf gespeichert"}


@mcp.tool()
def gmail_list_drafts(max_results: int = 10) -> list[dict]:
    """Listet vorhandene Entwürfe."""
    svc = gmail()
    res = svc.users().drafts().list(userId="me", maxResults=max_results).execute()
    out = []
    for d in res.get("drafts", []):
        full = svc.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
        info = summarize_message(full["message"])
        info["draft_id"] = d["id"]
        out.append(info)
    return out


@mcp.tool()
def gmail_modify(message_id: str, add_labels: list[str] | None = None,
                 remove_labels: list[str] | None = None) -> dict:
    """Ändert Labels. Archivieren = remove_labels=['INBOX']; als gelesen = remove_labels=['UNREAD'];
    markieren = add_labels=['STARRED']; wichtig = add_labels=['IMPORTANT']. Eigene Labels per Namen."""
    svc = gmail()
    names = {l["name"].lower(): l["id"] for l in svc.users().labels().list(userId="me").execute()["labels"]}

    def ids(labels):
        return [names.get(l.lower(), l) for l in (labels or [])]

    res = svc.users().messages().modify(
        userId="me", id=message_id,
        body={"addLabelIds": ids(add_labels), "removeLabelIds": ids(remove_labels)},
    ).execute()
    return {"id": res["id"], "labels": res.get("labelIds", [])}


@mcp.tool()
def gmail_trash(message_id: str) -> dict:
    """Verschiebt eine Mail in den Papierkorb (nach 30 Tagen endgültig gelöscht)."""
    gmail().users().messages().trash(userId="me", id=message_id).execute()
    return {"id": message_id, "status": "im Papierkorb"}


if ALLOW_SEND:
    @mcp.tool()
    def gmail_send(to: str = "", subject: str = "", body: str = "", cc: str = "", bcc: str = "",
                   reply_to_message_id: str = "") -> dict:
        """Sendet eine Mail SOFORT. Nur nach ausdrücklicher Bestätigung durch den Nutzer verwenden."""
        msg = _compose(to, subject, body, cc, bcc, reply_to_message_id)
        res = gmail().users().messages().send(userId="me", body=msg).execute()
        return {"id": res["id"], "status": "gesendet"}

    @mcp.tool()
    def gmail_send_draft(draft_id: str) -> dict:
        """Sendet einen vorhandenen Entwurf. Nur nach ausdrücklicher Bestätigung verwenden."""
        res = gmail().users().drafts().send(userId="me", body={"id": draft_id}).execute()
        return {"id": res["id"], "status": "gesendet"}


# --------------------------------------------------------------- calendar tools
@mcp.tool()
def calendar_list_calendars() -> list[dict]:
    """Listet alle Kalender (ID, Name, ob primär)."""
    res = calendar().calendarList().list().execute()
    return [{"id": c["id"], "name": c.get("summary"), "primary": c.get("primary", False)}
            for c in res.get("items", [])]


@mcp.tool()
def calendar_list_events(start: str = "", end: str = "", query: str = "",
                         calendar_id: str = "primary", max_results: int = 50) -> list[dict]:
    """Termine im Zeitraum (ISO-Format, z.B. '2026-10-01T00:00'). Ohne Angabe: die nächsten 7 Tage.
    query filtert nach Text."""
    now = datetime.now(ZoneInfo(TZ))
    t_min = parse_time(start) if start else now
    t_max = parse_time(end) if end else t_min + timedelta(days=7)
    params = dict(calendarId=calendar_id, timeMin=t_min.isoformat(), timeMax=t_max.isoformat(),
                  singleEvents=True, orderBy="startTime", maxResults=min(max_results, 250))
    if query:
        params["q"] = query
    res = calendar().events().list(**params).execute()
    return [summarize_event(e) for e in res.get("items", [])]


@mcp.tool()
def calendar_create_event(summary: str, start: str, end: str = "", description: str = "",
                          location: str = "", attendees: list[str] | None = None,
                          all_day: bool = False, reminder_minutes: int | None = None,
                          calendar_id: str = "primary") -> dict:
    """Erstellt einen Termin. start/end im ISO-Format ('2026-10-02T14:00'); ohne end = 1 Stunde.
    Ganztägig: all_day=True und start='2026-10-02'. Gäste werden NICHT automatisch eingeladen."""
    if not end:
        if all_day:
            end = (datetime.fromisoformat(start[:10]) + timedelta(days=1)).date().isoformat()
        else:
            end = (parse_time(start) + timedelta(hours=1)).isoformat()
    body = {
        "summary": summary,
        "start": event_time(start, all_day),
        "end": event_time(end, all_day),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    if reminder_minutes is not None:
        body["reminders"] = {"useDefault": False,
                             "overrides": [{"method": "popup", "minutes": reminder_minutes}]}
    ev = calendar().events().insert(calendarId=calendar_id, body=body, sendUpdates="none").execute()
    return summarize_event(ev)


@mcp.tool()
def calendar_update_event(event_id: str, summary: str = "", start: str = "", end: str = "",
                          description: str = "", location: str = "", all_day: bool = False,
                          calendar_id: str = "primary") -> dict:
    """Ändert einen Termin. Nur angegebene Felder werden geändert."""
    body: dict = {}
    if summary:
        body["summary"] = summary
    if start:
        body["start"] = event_time(start, all_day)
    if end:
        body["end"] = event_time(end, all_day)
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    ev = calendar().events().patch(calendarId=calendar_id, eventId=event_id, body=body,
                                   sendUpdates="none").execute()
    return summarize_event(ev)


@mcp.tool()
def calendar_delete_event(event_id: str, calendar_id: str = "primary") -> dict:
    """Löscht einen Termin. Vorher beim Nutzer nachfragen."""
    calendar().events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates="none").execute()
    return {"id": event_id, "status": "gelöscht"}


@mcp.tool()
def calendar_free_busy(start: str, end: str, calendar_ids: list[str] | None = None) -> dict:
    """Belegte Zeiten im Zeitraum – nützlich, um freie Slots zu finden."""
    body = {"timeMin": parse_time(start).isoformat(), "timeMax": parse_time(end).isoformat(),
            "timeZone": TZ, "items": [{"id": c} for c in (calendar_ids or ["primary"])]}
    res = calendar().freebusy().query(body=body).execute()
    return {cid: v.get("busy", []) for cid, v in res.get("calendars", {}).items()}


if __name__ == "__main__":
    mcp.run()
