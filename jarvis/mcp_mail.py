"""MCP-Server `mail`: E-Mail bei jedem Anbieter über IMAP (lesen) und SMTP (senden).

Funktioniert mit GMX, Web.de, T-Online, Outlook/Hotmail, iCloud, Yahoo, Posteo,
mailbox.org, IONOS, Strato, eigenen Servern … – und auch mit Gmail per App-Passwort.

Konten:
- ein Konto über Umgebungsvariablen (IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD, …)
- beliebig viele weitere in data/mail_accounts.json (siehe README)
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
import smtplib
import ssl
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from html import unescape
from pathlib import Path

from .mcp_common import JarvisMCP

DATA = Path(os.getenv("JARVIS_DATA", Path(__file__).resolve().parent.parent / "data"))
ALLOW_SEND = os.getenv("JARVIS_ALLOW_SEND_EMAIL", "false").lower() in {"1", "true", "yes", "ja"}
MAX_BODY = 15_000

mcp = JarvisMCP("mail")


# ---------------------------------------------------------------- Konten
@dataclass
class Account:
    name: str
    imap_host: str
    username: str
    password: str
    imap_port: int = 993
    imap_ssl: bool = True          # False = STARTTLS auf Port 143
    smtp_host: str = ""
    smtp_port: int = 465           # 465 = SSL, 587 = STARTTLS
    smtp_username: str = ""
    smtp_password: str = ""
    from_address: str = ""
    from_name: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        acc = cls(**known)
        acc.imap_port = int(acc.imap_port)
        acc.smtp_port = int(acc.smtp_port)
        acc.imap_ssl = acc.imap_ssl not in (False, "false", "0", 0)
        return acc


def _env_account() -> Account | None:
    host = os.getenv("IMAP_HOST", "")
    if not host:
        return None
    user = os.getenv("IMAP_USERNAME", "")
    return Account.from_dict({
        "name": os.getenv("MAIL_ACCOUNT_NAME", "privat"),
        "imap_host": host,
        "imap_port": os.getenv("IMAP_PORT", "993"),
        "imap_ssl": os.getenv("IMAP_SSL", "true").lower() != "false",
        "username": user,
        "password": os.getenv("IMAP_PASSWORD", ""),
        "smtp_host": os.getenv("SMTP_HOST", ""),
        "smtp_port": os.getenv("SMTP_PORT", "465"),
        "smtp_username": os.getenv("SMTP_USERNAME", "") or user,
        "smtp_password": os.getenv("SMTP_PASSWORD", "") or os.getenv("IMAP_PASSWORD", ""),
        "from_address": os.getenv("MAIL_FROM", "") or user,
        "from_name": os.getenv("MAIL_FROM_NAME", ""),
    })


def accounts() -> dict[str, Account]:
    out: dict[str, Account] = {}
    env = _env_account()
    if env:
        out[env.name] = env
    path = Path(os.getenv("MAIL_ACCOUNTS_FILE", DATA / "mail_accounts.json"))
    if path.exists():
        for d in json.loads(path.read_text()):
            d.setdefault("smtp_username", d.get("username", ""))
            d.setdefault("smtp_password", d.get("password", ""))
            d.setdefault("from_address", d.get("username", ""))
            acc = Account.from_dict(d)
            out[acc.name] = acc
    return out


def configured() -> bool:
    return bool(accounts())


def account(name: str = "") -> Account:
    accs = accounts()
    if not accs:
        raise RuntimeError("Kein Mailkonto eingerichtet (IMAP_HOST … in .env oder data/mail_accounts.json)")
    if not name:
        return next(iter(accs.values()))
    for key, acc in accs.items():
        if key.lower() == name.lower() or acc.username.lower() == name.lower():
            return acc
    raise ValueError(f"Konto '{name}' unbekannt. Vorhanden: {', '.join(accs)}")


@contextmanager
def imap(acc: Account):
    ctx = ssl.create_default_context()
    if acc.imap_ssl:
        conn = imaplib.IMAP4_SSL(acc.imap_host, acc.imap_port, ssl_context=ctx, timeout=30)
    else:
        conn = imaplib.IMAP4(acc.imap_host, acc.imap_port, timeout=30)
        if os.getenv("IMAP_INSECURE_PLAIN", "") != "1":  # nur für lokale Tests
            conn.starttls(ssl_context=ctx)
    try:
        conn.login(acc.username, acc.password)
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------- Helfer
def decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", html)
    return re.sub(r"\n\s*\n+", "\n\n", unescape(re.sub(r"<[^>]+>", "", html))).strip()


def message_body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:  # noqa: BLE001
        text = part.get_payload(decode=True).decode("utf-8", errors="replace")
    return html_to_text(text) if part.get_content_type() == "text/html" else text.strip()


def attachment_names(msg: EmailMessage) -> list[str]:
    return [decode(p.get_filename()) for p in msg.iter_attachments() if p.get_filename()]


def imap_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def imap_date(d: datetime) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{d.day:02d}-{months[d.month - 1]}-{d.year}"


def build_criteria(unread: bool = False, from_: str = "", subject: str = "", text: str = "",
                   since_days: int = 0, flagged: bool = False) -> tuple[list[str], dict]:
    """IMAP-SEARCH-Kriterien. Nicht-ASCII-Werte werden stattdessen clientseitig gefiltert
    (viele Server können kein CHARSET UTF-8)."""
    crit: list[str] = []
    local: dict = {}
    if unread:
        crit.append("UNSEEN")
    if flagged:
        crit.append("FLAGGED")
    if since_days:
        crit += ["SINCE", imap_date(datetime.now() - timedelta(days=since_days))]
    for key, val in (("FROM", from_), ("SUBJECT", subject), ("TEXT", text)):
        if not val:
            continue
        if val.isascii():
            crit += [key, imap_quote(val)]
        else:
            local[key.lower()] = val.lower()
    return crit or ["ALL"], local


_FOLDER_RE = re.compile(r'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)')


def parse_folder(line: bytes) -> tuple[str, str]:
    m = _FOLDER_RE.match(line.decode(errors="replace"))
    if not m:
        return "", ""
    name = m.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"')
    return name, m.group("flags")


def folders(conn) -> list[tuple[str, str]]:
    typ, data = conn.list()
    return [f for f in (parse_folder(l) for l in data if isinstance(l, bytes)) if f[0]]


SPECIAL_NAMES = {
    "\\Trash": ["Trash", "Papierkorb", "Gelöscht", "Deleted", "Deleted Items", "Deleted Messages", "INBOX.Trash"],
    "\\Drafts": ["Drafts", "Entwürfe", "Entwurf", "INBOX.Drafts"],
    "\\Sent": ["Sent", "Gesendet", "Gesendete Objekte", "Sent Items", "Sent Messages", "INBOX.Sent"],
    "\\Archive": ["Archive", "Archiv", "INBOX.Archive"],
    "\\Junk": ["Junk", "Spam", "INBOX.Spam"],
}


def special_folder(conn, use: str) -> str | None:
    fl = folders(conn)
    for name, flags in fl:
        if use.lower() in flags.lower():
            return name
    lookup = {n.lower(): n for n, _ in fl}
    for cand in SPECIAL_NAMES.get(use, []):
        if cand.lower() in lookup:
            return lookup[cand.lower()]
    return None


def select(conn, folder: str, readonly: bool = True) -> None:
    typ, data = conn.select(imap_quote(folder), readonly=readonly)
    if typ != "OK":
        raise ValueError(f"Ordner '{folder}' nicht gefunden")


def summarize(uid: str, raw_header: bytes, flags: str, folder: str, acc: str) -> dict:
    msg = email.message_from_bytes(raw_header, policy=default_policy)
    return {
        "uid": uid,
        "account": acc,
        "folder": folder,
        "from": decode(msg.get("From")),
        "to": decode(msg.get("To")),
        "subject": decode(msg.get("Subject")),
        "date": msg.get("Date", ""),
        "unread": "\\Seen" not in flags,
        "flagged": "\\Flagged" in flags,
    }


def _fetch_headers(conn, uids: list[bytes]) -> list[tuple[str, bytes, str]]:
    if not uids:
        return []
    typ, data = conn.uid("FETCH", b",".join(uids), "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
    out = []
    for item in data:
        if not isinstance(item, tuple):
            continue
        meta = item[0].decode(errors="replace")
        uid = re.search(r"UID (\d+)", meta)
        flags = re.search(r"FLAGS \(([^)]*)\)", meta)
        if uid:
            out.append((uid.group(1), item[1], flags.group(1) if flags else ""))
    return out


def _fetch_message(conn, uid: str) -> tuple[EmailMessage, str]:
    typ, data = conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
    for item in data:
        if isinstance(item, tuple):
            flags = re.search(r"FLAGS \(([^)]*)\)", item[0].decode(errors="replace"))
            return email.message_from_bytes(item[1], policy=default_policy), flags.group(1) if flags else ""
    raise ValueError(f"Mail {uid} nicht gefunden")


# ---------------------------------------------------------------- Tools
@mcp.tool()
def mail_accounts() -> list[dict]:
    """Listet die eingerichteten Mailkonten."""
    return [{"name": a.name, "address": a.from_address or a.username, "can_send": bool(a.smtp_host)}
            for a in accounts().values()]


@mcp.tool()
def mail_list_folders(account_name: str = "") -> list[dict]:
    """Listet die Ordner eines Kontos (mit Sonderfunktion wie \\Sent, \\Trash)."""
    acc = account(account_name)
    with imap(acc) as conn:
        return [{"name": n, "flags": f} for n, f in folders(conn)]


@mcp.tool()
def mail_search(folder: str = "INBOX", unread: bool = False, from_: str = "", subject: str = "",
                text: str = "", since_days: int = 0, flagged: bool = False, max_results: int = 20,
                account_name: str = "") -> list[dict]:
    """Sucht Mails (neueste zuerst). Beispiele: unread=True, since_days=1 → ungelesen seit gestern;
    from_='chef@firma.de'; subject='Rechnung'; text sucht im ganzen Text.
    Ohne account_name wird das erste Konto genutzt; für alle Konten pro Konto aufrufen."""
    acc = account(account_name)
    crit, local = build_criteria(unread, from_, subject, text, since_days, flagged)
    with imap(acc) as conn:
        select(conn, folder)
        typ, data = conn.uid("SEARCH", *crit)
        uids = data[0].split() if data and data[0] else []
        uids = uids[-500:] if local else uids[-max_results:]
        items = [summarize(u, h, f, folder, acc.name) for u, h, f in _fetch_headers(conn, uids)]
    if local:
        def ok(i: dict) -> bool:
            return all(v in (i["from"] if k == "from" else i["subject"] if k == "subject"
                             else i["from"] + " " + i["subject"]).lower() for k, v in local.items())
        items = [i for i in items if ok(i)]
    items.sort(key=lambda i: int(i["uid"]), reverse=True)
    return items[:max_results]


@mcp.tool()
def mail_read(uid: str, folder: str = "INBOX", mark_read: bool = False, account_name: str = "") -> dict:
    """Liest eine Mail vollständig. mark_read=True markiert sie als gelesen."""
    acc = account(account_name)
    with imap(acc) as conn:
        select(conn, folder, readonly=not mark_read)
        msg, flags = _fetch_message(conn, uid)
        if mark_read:
            conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    body = message_body(msg)
    return {
        "uid": uid, "account": acc.name, "folder": folder,
        "from": decode(msg.get("From")), "to": decode(msg.get("To")), "cc": decode(msg.get("Cc")),
        "subject": decode(msg.get("Subject")), "date": msg.get("Date", ""),
        "message_id": msg.get("Message-ID", ""),
        "body": body[:MAX_BODY] + ("\n[… gekürzt]" if len(body) > MAX_BODY else ""),
        "attachments": attachment_names(msg),
    }


@mcp.tool()
def mail_mark(uid: str, read: bool | None = None, flagged: bool | None = None, folder: str = "INBOX",
              account_name: str = "") -> dict:
    """Markiert als gelesen/ungelesen (read) und/oder markiert/entmarkiert (flagged)."""
    acc = account(account_name)
    with imap(acc) as conn:
        select(conn, folder, readonly=False)
        if read is not None:
            conn.uid("STORE", uid, "+FLAGS" if read else "-FLAGS", "(\\Seen)")
        if flagged is not None:
            conn.uid("STORE", uid, "+FLAGS" if flagged else "-FLAGS", "(\\Flagged)")
    return {"uid": uid, "status": "ok"}


def _move(conn, uid: str, target: str) -> None:
    typ, _ = conn.uid("MOVE", uid, imap_quote(target))
    if typ != "OK":  # Server ohne MOVE-Erweiterung
        typ, _ = conn.uid("COPY", uid, imap_quote(target))
        if typ != "OK":
            raise RuntimeError(f"Verschieben nach '{target}' fehlgeschlagen")
        conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        conn.expunge()


@mcp.tool()
def mail_move(uid: str, target_folder: str = "", folder: str = "INBOX", account_name: str = "") -> dict:
    """Verschiebt eine Mail. Ohne target_folder = ins Archiv (legt 'Archiv' an, falls nötig)."""
    acc = account(account_name)
    with imap(acc) as conn:
        target = target_folder or special_folder(conn, "\\Archive")
        if not target:
            target = "Archiv"
            conn.create(imap_quote(target))
        select(conn, folder, readonly=False)
        _move(conn, uid, target)
    return {"uid": uid, "moved_to": target}


@mcp.tool()
def mail_trash(uid: str, folder: str = "INBOX", account_name: str = "") -> dict:
    """Verschiebt eine Mail in den Papierkorb."""
    acc = account(account_name)
    with imap(acc) as conn:
        trash = special_folder(conn, "\\Trash")
        if not trash:
            raise RuntimeError("Kein Papierkorb-Ordner gefunden")
        select(conn, folder, readonly=False)
        _move(conn, uid, trash)
    return {"uid": uid, "moved_to": trash}


def compose(acc: Account, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
            reply_to_uid: str = "", folder: str = "INBOX") -> EmailMessage:
    msg = EmailMessage()
    if reply_to_uid:
        with imap(acc) as conn:
            select(conn, folder)
            orig, _ = _fetch_message(conn, reply_to_uid)
        subject = subject or decode(orig.get("Subject"))
        if not subject.lower().startswith(("re:", "aw:")):
            subject = "Re: " + subject
        to = to or decode(orig.get("Reply-To")) or decode(orig.get("From"))
        if orig.get("Message-ID"):
            msg["In-Reply-To"] = orig["Message-ID"]
            msg["References"] = (orig.get("References", "") + " " + orig["Message-ID"]).strip()
    if not to:
        raise ValueError("Empfänger fehlt")
    addr = acc.from_address or acc.username
    msg["From"] = formataddr((acc.from_name, addr)) if acc.from_name else addr
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=parseaddr(addr)[1].split("@")[-1] or None)
    msg.set_content(body)
    return msg


def _append(acc: Account, use: str, msg: EmailMessage, flags: str) -> str | None:
    with imap(acc) as conn:
        target = special_folder(conn, use)
        if target:
            conn.append(imap_quote(target), flags, imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        return target


@mcp.tool()
def mail_create_draft(to: str = "", subject: str = "", body: str = "", cc: str = "", bcc: str = "",
                      reply_to_uid: str = "", folder: str = "INBOX", account_name: str = "") -> dict:
    """Legt einen Entwurf im Entwürfe-Ordner an. Für Antworten reply_to_uid (+folder) angeben."""
    acc = account(account_name)
    msg = compose(acc, to, subject, body, cc, bcc, reply_to_uid, folder)
    target = _append(acc, "\\Drafts", msg, "(\\Draft \\Seen)")
    if not target:
        raise RuntimeError("Kein Entwürfe-Ordner gefunden")
    return {"status": "Entwurf gespeichert", "folder": target, "to": msg["To"], "subject": msg["Subject"]}


def smtp_send(acc: Account, msg: EmailMessage) -> None:
    if not acc.smtp_host:
        raise RuntimeError(f"Für Konto '{acc.name}' ist kein SMTP-Server eingerichtet")
    ctx = ssl.create_default_context()
    if acc.smtp_port == 465:
        server = smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=30)
        if os.getenv("IMAP_INSECURE_PLAIN", "") != "1":
            server.starttls(context=ctx)
    with server:
        if acc.smtp_username:
            server.login(acc.smtp_username, acc.smtp_password)
        server.send_message(msg)


@mcp.tool(enabled=ALLOW_SEND)
def mail_send(to: str = "", subject: str = "", body: str = "", cc: str = "", bcc: str = "",
              reply_to_uid: str = "", folder: str = "INBOX", account_name: str = "") -> dict:
    """Sendet eine Mail SOFORT per SMTP und legt sie unter 'Gesendet' ab.
    Nur nach ausdrücklicher Bestätigung durch den Nutzer verwenden."""
    acc = account(account_name)
    msg = compose(acc, to, subject, body, cc, bcc, reply_to_uid, folder)
    smtp_send(acc, msg)
    try:
        _append(acc, "\\Sent", msg, "(\\Seen)")
    except Exception:  # noqa: BLE001 – viele Anbieter legen selbst ab
        pass
    return {"status": "gesendet", "to": msg["To"], "subject": msg["Subject"]}


if __name__ == "__main__":
    mcp.run()
