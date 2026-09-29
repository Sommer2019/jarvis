"""IMAP/SMTP – Hilfsfunktionen + Integrationstest gegen GreenMail (wenn GREENMAIL_JAR gesetzt)."""

import importlib
import os
import socket
import subprocess
import time

import pytest

from jarvis import mcp_mail


def test_criteria_ascii_and_umlaut():
    crit, local = mcp_mail.build_criteria(unread=True, from_="max@x.de", subject="München")
    assert crit == ["UNSEEN", "FROM", '"max@x.de"'] and local == {"subject": "münchen"}
    assert mcp_mail.build_criteria() == (["ALL"], {})


def test_parse_folder():
    assert mcp_mail.parse_folder(b'(\\HasNoChildren \\Trash) "/" "Gel&APY-scht"') == ("Gel&APY-scht", "\\HasNoChildren \\Trash")
    assert mcp_mail.parse_folder(b'(\\HasNoChildren) "." INBOX.Sent') == ("INBOX.Sent", "\\HasNoChildren")


def test_accounts_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAP_HOST", raising=False)
    f = tmp_path / "mail_accounts.json"
    f.write_text('[{"name": "gmx", "imap_host": "imap.gmx.net", "username": "a@gmx.de", "password": "p",'
                 ' "smtp_host": "mail.gmx.net", "smtp_port": 587}]')
    monkeypatch.setenv("MAIL_ACCOUNTS_FILE", str(f))
    acc = mcp_mail.account("gmx")
    assert acc.smtp_port == 587 and acc.smtp_username == "a@gmx.de" and acc.from_address == "a@gmx.de"


@pytest.fixture
def greenmail(monkeypatch):
    jar = os.getenv("GREENMAIL_JAR")
    if not jar:
        pytest.skip("GREENMAIL_JAR nicht gesetzt")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        off = s.getsockname()[1] % 1000 * 10 + 20000
    proc = subprocess.Popen(["java", f"-Dgreenmail.smtp.port={off}", f"-Dgreenmail.imap.port={off + 1}",
                             "-Dgreenmail.users=robin:geheim@test.local", "-Dgreenmail.hostname=127.0.0.1",
                             "-jar", jar], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", off + 1), 0.2).close()
            break
        except OSError:
            time.sleep(0.2)
    env = {"IMAP_INSECURE_PLAIN": "1", "IMAP_HOST": "127.0.0.1", "IMAP_PORT": str(off + 1), "IMAP_SSL": "false",
           "IMAP_USERNAME": "robin", "IMAP_PASSWORD": "geheim", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(off),
           "MAIL_FROM": "robin@test.local", "JARVIS_ALLOW_SEND_EMAIL": "true", "MAIL_ACCOUNTS_FILE": "/nonexistent"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    yield importlib.reload(mcp_mail)
    proc.terminate()
    importlib.reload(mcp_mail)


def test_greenmail_roundtrip(greenmail):
    m = greenmail
    acc = m.account()
    with m.imap(acc) as c:
        for f in ("Drafts", "Trash", "Sent"):
            c.create(f)
    msg = m.EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "Max <max@test.local>", "robin@test.local", "Grüße aus München"
    msg["Message-ID"] = m.make_msgid()
    msg.set_content("Hallo")
    m.smtp_send(acc, msg)
    time.sleep(0.5)
    hit = m.mail_search(unread=True, subject="München")
    assert [h["subject"] for h in hit] == ["Grüße aus München"]
    assert m.mail_read(hit[0]["uid"], mark_read=True)["body"] == "Hallo"
    assert m.mail_search(unread=True) == []
    assert m.mail_create_draft(reply_to_uid=hit[0]["uid"], body="Danke")["subject"] == "Re: Grüße aus München"
    assert m.mail_move(hit[0]["uid"])["moved_to"] == "Archiv"
    assert m.mail_search() == []
