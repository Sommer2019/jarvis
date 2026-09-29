"""CalDAV/CardDAV – Hilfsfunktionen + Integrationstest gegen einen echten Radicale-Server."""

import importlib
import socket
import subprocess
import sys
import time

import httpx
import pytest

from jarvis import mcp_dav


def test_vcard_roundtrip():
    text = mcp_dav.build_vcard("Max Mustermann", ["max@x.de"], ["+49 170 1"], "ACME", "Notiz", "1990-10-06", uid="u1")
    c = mcp_dav.parse_vcard(text)
    assert c["uid"] == "u1" and c["name"] == "Max Mustermann" and c["organization"] == "ACME"
    assert c["emails"][0]["value"] == "max@x.de" and c["birthday"] == "1990-10-06"


def test_vevent_build_and_summary():
    from icalendar import Calendar

    ical = mcp_dav.build_vevent("Zahnarzt", "2026-10-02T14:00", location="Praxis", reminder_minutes=0)
    ev = Calendar.from_ical(ical).walk("VEVENT")[0]
    s = mcp_dav.summarize_vevent(ev)
    assert s["start"] == "2026-10-02T14:00:00+02:00" and s["end"] == "2026-10-02T15:00:00+02:00"
    assert "VALARM" in ical
    allday = mcp_dav.summarize_vevent(Calendar.from_ical(mcp_dav.build_vevent("Urlaub", "2026-10-05", all_day=True)).walk("VEVENT")[0])
    assert allday["all_day"] and allday["end"] == "2026-10-06"


@pytest.fixture
def radicale(tmp_path, monkeypatch):
    pytest.importorskip("radicale")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    conf = tmp_path / "config"
    conf.write_text(f"[server]\nhosts = 127.0.0.1:{port}\n[auth]\ntype = none\n"
                    f"[storage]\nfilesystem_folder = {tmp_path / 'col'}\n")
    proc = subprocess.Popen([sys.executable, "-m", "radicale", "--config", str(conf)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/"
    for _ in range(50):
        try:
            httpx.get(base)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    auth = ("robin", "x")
    for path, kind, ns in (("cal", "C:calendar", "urn:ietf:params:xml:ns:caldav"),
                           ("book", "C:addressbook", "urn:ietf:params:xml:ns:carddav")):
        httpx.request("MKCOL", f"{base}robin/{path}/", auth=auth, content=(
            f'<?xml version="1.0"?><D:mkcol xmlns:D="DAV:" xmlns:C="{ns}"><D:set><D:prop>'
            f"<D:resourcetype><D:collection/><{kind}/></D:resourcetype>"
            f"<D:displayname>{path}</D:displayname></D:prop></D:set></D:mkcol>"))
    for k, v in {"CALDAV_URL": base, "CALDAV_USERNAME": "robin", "CALDAV_PASSWORD": "x", "CARDDAV_URL": base}.items():
        monkeypatch.setenv(k, v)
    mod = importlib.reload(mcp_dav)
    yield mod
    proc.terminate()
    importlib.reload(mcp_dav)


def test_radicale_calendar_and_contacts(radicale):
    d = radicale
    assert [c["name"] for c in d.caldav_list_calendars()] == ["cal"]
    ev = d.caldav_create_event("Zahnarzt", "2026-10-02T14:00")
    d.caldav_update_event(ev["uid"], start="2026-10-02T16:00")
    evs = d.caldav_list_events("2026-10-01T00:00", "2026-10-03T00:00")
    assert [(e["summary"], e["start"], e["end"]) for e in evs] == [
        ("Zahnarzt", "2026-10-02T16:00:00+02:00", "2026-10-02T17:00:00+02:00")]
    d.caldav_delete_event(ev["uid"])
    assert d.caldav_list_events("2026-10-01T00:00", "2026-10-03T00:00") == []

    c = d.contacts_create("Max Mustermann", emails=["max@x.de"], phones=["+49 170 1234567"])
    assert d.contacts_search("1234567")[0]["name"] == "Max Mustermann"
    d.contacts_update(c["uid"], add_emails=["max@work.de"])
    assert len(d.contacts_search("max")[0]["emails"]) == 2
    d.contacts_delete(c["uid"])
    assert d.contacts_search("max") == []
