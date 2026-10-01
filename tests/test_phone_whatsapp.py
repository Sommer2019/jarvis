import time

import pytest

from jarvis.phone import PhoneStore
from jarvis.whatsapp import extract_messages, normalize


def test_phone_store(tmp_path):
    s = PhoneStore(tmp_path)
    s.save_contacts([{"name": "Max Mustermann", "phones": ["+49 170 1234567"], "emails": ["max@x.de"]},
                     {"name": "Lena", "phones": ["0151 999"]}])
    assert [c["name"] for c in s.search_contacts("max")] == ["Max Mustermann"]
    assert [c["name"] for c in s.search_contacts("1234567")] == ["Max Mustermann"]
    assert [c["name"] for c in s.search_contacts("mustermann max")] == ["Max Mustermann"]
    with pytest.raises(ValueError):
        s.queue("selbstzerstörung", {})
    a = s.queue("alarm", {"hour": 7, "minute": 0})
    assert s.pending()[0]["id"] == a["id"]
    assert s.done(a["id"]) and s.pending() == []


def test_actions_expire(tmp_path, monkeypatch):
    s = PhoneStore(tmp_path)
    s.queue("call", {"number": "1"})
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    assert s.pending() == []


def test_whatsapp_extract():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "4917012345", "id": "a", "type": "text", "text": {"body": "Hallo"}},
        {"from": "4917012345", "id": "b", "type": "audio", "audio": {"id": "m1"}},
        {"from": "4917012345", "id": "c", "type": "interactive",
         "interactive": {"button_reply": {"title": "Ja"}}},
    ]}}]}]}
    msgs = extract_messages(payload)
    assert msgs[0]["text"] == "Hallo" and msgs[1]["media_id"] == "m1" and msgs[2]["text"] == "Ja"
    assert extract_messages({"entry": [{"changes": [{"value": {"statuses": []}}]}]}) == []
    assert normalize("+49 170 123-45") == "4917012345"


async def test_whatsapp_allowlist_and_window(tmp_path):
    from jarvis.brain import Reply
    from jarvis.config import Config
    from jarvis.whatsapp import WhatsApp

    class B:
        asked = []

        async def ask(self, text, conv, **kw):
            self.asked.append((text, conv))
            return Reply("Antwort")

    cfg = Config(data_dir=tmp_path, whatsapp_allowed=["+49 170 1"], whatsapp_phone_id="p", whatsapp_token="t")
    wa = WhatsApp(cfg, B())
    sent = []

    async def send_text(to, text):
        sent.append((to, text))

    async def mark_read(_):
        pass

    wa.send_text, wa.mark_read = send_text, mark_read
    await wa.handle({"from": "999", "id": "x", "type": "text", "text": "hack"})
    assert sent == [] and not wa.window_open("999")
    await wa.handle({"from": "491701", "id": "y", "type": "text", "text": "Hallo"})
    assert B.asked == [("Hallo", "whatsapp:491701")] and sent == [("491701", "Antwort")]
    assert wa.window_open("491701")
    await wa.notify("Briefing")
    assert sent[-1] == ("491701", "Briefing")


def test_location_store(tmp_path):
    s = PhoneStore(tmp_path)
    assert s.location() is None
    s.save_location({"lat": 52.5200081, "lon": 13.4049541, "accuracy": 8.7, "address": "Berlin"})
    loc = s.location()
    assert loc["lat"] == 52.520008 and loc["accuracy_m"] == 8 and loc["age_minutes"] == 0
    assert "Berlin" in PhoneStore.describe(loc) and "maps.google.com" in PhoneStore.describe(loc)


def test_notify_and_ring_live_longer(tmp_path, monkeypatch):
    s = PhoneStore(tmp_path)
    s.queue("call", {"number": "1"})
    s.queue("notify", {"title": "Jarvis", "text": "Paket ist da"})
    s.queue("ring", {"text": "Dringend!"})
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 20 * 60)   # nach 20 Min.
    assert sorted(a["type"] for a in s.pending()) == ["notify", "ring"]
    monkeypatch.setattr(time, "time", lambda: real() + 2 * 3600)  # nach 2 Std.
    assert [a["type"] for a in s.pending()] == ["notify"]


async def test_scheduler_pushes_to_phone(tmp_path):
    from jarvis.config import Config
    from jarvis.scheduler import Scheduler

    s = PhoneStore(tmp_path)
    sched = Scheduler(Config(data_dir=tmp_path, ntfy_url=""), brain=None, notifiers=[])
    await sched.notify("☀️ Guten Morgen!\n\nHeute 3 Termine")
    assert s.pending() == []          # App noch nie verbunden → kein Push
    s.register_app({"version": "1"})
    await sched.notify("☀️ Guten Morgen!\n\nHeute 3 Termine")
    (a,) = s.pending()
    assert a["type"] == "notify" and a["params"]["title"] == "☀️ Guten Morgen!" and a["params"]["text"] == "Heute 3 Termine"
