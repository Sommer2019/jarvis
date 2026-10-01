import importlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_TIMEZONE", "Europe/Berlin")
    from jarvis import mcp_phone
    mod = importlib.reload(mcp_phone)
    yield mod
    monkeypatch.undo()
    importlib.reload(mcp_phone)


def ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("Europe/Berlin")).timestamp() * 1000)


def seed(mod):
    mod.store.save_calendar(
        [{"id": 1, "name": "Feiertage", "account": "google", "writable": False},
         {"id": 2, "name": "robin@gmail.com", "account": "robin@gmail.com", "writable": True, "primary": True},
         {"id": 3, "name": "Arbeit", "account": "robin@firma.de", "writable": True}],
        [{"event_id": 10, "calendar_id": 2, "title": "Zahnarzt", "start": ms("2026-10-02T14:00"),
          "end": ms("2026-10-02T15:00"), "location": "Praxis"},
         {"event_id": 11, "calendar_id": 1, "title": "Tag der Deutschen Einheit", "all_day": True,
          "start": int(datetime(2026, 10, 3, tzinfo=ZoneInfo("UTC")).timestamp() * 1000),
          "end": int(datetime(2026, 10, 4, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)},
         {"event_id": 12, "calendar_id": 3, "title": "Standup", "start": ms("2026-10-20T09:00"),
          "end": ms("2026-10-20T09:15")}])


def test_no_calendar(phone):
    assert "hinweis" in phone.phone_calendar_events()


def test_list_and_events(phone):
    seed(phone)
    assert [c["name"] for c in phone.phone_calendar_list()] == ["Feiertage", "robin@gmail.com", "Arbeit"]
    r = phone.phone_calendar_events("2026-10-01T00:00", "2026-10-08T00:00")
    assert [(e["title"], e["start"]) for e in r["events"]] == [
        ("Zahnarzt", "2026-10-02T14:00+02:00"), ("Tag der Deutschen Einheit", "2026-10-03")]
    assert phone.phone_calendar_events("2026-10-01T00:00", "2026-10-31T00:00", query="stand")["events"][0]["calendar"] == "Arbeit"


def test_add_uses_primary_and_converts_times(phone):
    seed(phone)
    r = phone.phone_calendar_add("Friseur", "2026-10-05T10:00", reminder_minutes=0)
    p = r["queued"]["params"]
    assert r["calendar"] == "robin@gmail.com" and p["calendar_id"] == 2
    assert p["start"] == ms("2026-10-05T10:00") and p["end"] - p["start"] == 3_600_000
    a = phone.phone_calendar_add("Urlaub", "2026-10-10", all_day=True, calendar="arbeit")["queued"]["params"]
    assert a["calendar_id"] == 3 and a["timezone"] == "UTC" and a["end"] - a["start"] == 86_400_000


def test_add_rejects_readonly_calendar(phone):
    seed(phone)
    with pytest.raises(ValueError):
        phone.phone_calendar_add("x", "2026-10-05T10:00", calendar="Feiertage")


def test_calendar_upload_endpoint(tmp_path):
    from jarvis.config import Config
    from jarvis.server import create_app
    from tests.test_server import FakeBrain

    c = TestClient(create_app(Config(token="geheim", data_dir=tmp_path, workspace=tmp_path), FakeBrain()))
    r = c.post("/api/phone/calendar", headers={"Authorization": "Bearer geheim"}, json={
        "calendars": [{"id": 2, "name": "Privat", "writable": True}],
        "events": [{"event_id": 1, "calendar_id": 2, "title": "A", "start": 1, "end": 2}]})
    assert r.json() == {"saved": 1}
