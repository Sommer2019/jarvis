import http.server
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jarvis.brain import Reply
from jarvis.config import Config
from jarvis.phone import PhoneStore
from jarvis.scheduler import Scheduler
from jarvis.tasks import TaskStore, html_text, page_fingerprint, parse_weekdays

TZ = ZoneInfo("Europe/Berlin")


def test_weekdays_and_text():
    assert parse_weekdays("mo-fr") == [0, 1, 2, 3, 4]
    assert parse_weekdays("sa,so") == [5, 6]
    assert parse_weekdays("fr-mo") == [0, 4, 5, 6]
    assert html_text("<style>x</style><p>Hallo&nbsp;<b>Welt</b></p><script>1</script>") == "Hallo Welt"
    assert page_fingerprint("Stand 12:01 Preis") == page_fingerprint("Stand 12:02 Preis")


def test_schedules(tmp_path):
    s = TaskStore(tmp_path)
    with pytest.raises(ValueError, match="Zeitplan"):
        s.create("x", "y")
    t = s.create("Wochenstart", "Fasse meine Woche zusammen", daily_at="08:00", weekdays="mo")
    nxt = datetime.fromtimestamp(t["next_run"], TZ)
    assert nxt.weekday() == 0 and (nxt.hour, nxt.minute) == (8, 0) and not t["once"]
    w = s.create("Tickets", watch_url="example.com/tickets", watch_contains="verfügbar")
    assert w["watch_url"] == "https://example.com/tickets" and w["once"] and w["every_minutes"] == 30
    assert s.create("oft", "x", every_minutes=1)["every_minutes"] == 15  # Kontingent schonen
    one = s.create("einmal", "Erinnere mich", at="2030-01-01T09:00")
    assert one["once"] and s.finish_run(one["id"], "erledigt", True) is None  # danach weg
    assert {x["name"] for x in s.all()} == {"Wochenstart", "Tickets", "oft"}


class FakeBrain:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def reset(self, conv):
        pass

    async def ask(self, prompt, conv, **kw):
        self.prompts.append(prompt)
        return Reply(self.answer)


@pytest.fixture
def site():
    state = {"html": "<h1>Konzert</h1><p>Leider ausverkauft</p>"}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = state["html"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/", state
    srv.shutdown()


async def test_keyword_watch_rings_without_claude(tmp_path, site):
    url, state = site
    PhoneStore(tmp_path).register_app({"version": "1"})
    brain = FakeBrain("sollte nicht gefragt werden")
    sched = Scheduler(Config(data_dir=tmp_path), brain, [])
    store = TaskStore(tmp_path)
    t = store.create("Tickets", watch_url=url, watch_contains="Tickets verfügbar", alert="ring")
    await sched.run_task(store.get(t["id"]))
    assert PhoneStore(tmp_path).pending() == [] and store.get(t["id"])["runs"] == 1
    state["html"] = "<h1>Konzert</h1><p>Jetzt Tickets verfügbar!</p>"
    await sched.run_task(store.get(t["id"]))
    (ring,) = PhoneStore(tmp_path).pending()
    assert ring["type"] == "ring" and "Tickets verfügbar" in ring["params"]["text"]
    assert brain.prompts == [] and store.get(t["id"]) is None  # einmalig → erledigt


async def test_condition_watch_asks_claude_only_on_change(tmp_path, site):
    url, state = site
    brain = FakeBrain("NICHTS")
    sched = Scheduler(Config(data_dir=tmp_path), brain, [])
    store = TaskStore(tmp_path)
    t = store.create("Preis", "Melde, wenn der Preis unter 300 € liegt", watch_url=url)
    await sched.run_task(store.get(t["id"]))          # erster Stand → Claude prüft einmal
    await sched.run_task(store.get(t["id"]))          # unverändert → kein Claude
    assert len(brain.prompts) == 1 and "Leider ausverkauft" in brain.prompts[0]
    state["html"] = "<p>Jetzt nur 279 €</p>"
    brain.answer = "Der Preis liegt jetzt bei 279 €!"
    await sched.run_task(store.get(t["id"]))
    assert len(brain.prompts) == 2 and store.get(t["id"]) is None
    (note,) = PhoneStore(tmp_path).pending() if PhoneStore(tmp_path).app_registered() else [None]
    assert note is None  # App nie verbunden → keine App-Nachricht, andere Kanäle hätten gemeldet


async def test_scheduled_prompt_task_reports(tmp_path):
    sent = []

    async def notifier(text):
        sent.append(text)

    brain = FakeBrain("Diese Woche: 3 Termine, 2 Fristen.")
    sched = Scheduler(Config(data_dir=tmp_path), brain, [notifier])
    store = TaskStore(tmp_path)
    t = store.create("Wochenstart", "Fasse meine Woche zusammen", daily_at="08:00")
    store.update(t["id"], next_run=time.time() - 1)
    assert [x["id"] for x in store.due()] == [t["id"]]
    await sched.run_task(store.get(t["id"]))
    assert sent == ["🔔 Wochenstart: Diese Woche: 3 Termine, 2 Fristen."]
    left = store.get(t["id"])
    assert left and left["next_run"] > time.time() and left["runs"] == 1  # wiederkehrend bleibt


async def test_mcp_tools(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path))
    from jarvis import mcp_tasks
    m = importlib.reload(mcp_tasks)
    t = m.task_create("Paket", "Prüfe, ob mein DHL-Paket zugestellt wurde", every_minutes=60)
    assert m.task_list()[0]["name"] == "Paket" and "alle 60 Min." in m.task_list()[0]["plan"]
    assert "pausiert" in m.task_pause(t["id"])["plan"]
    assert m.task_delete(t["id"])["status"] == "gelöscht" and m.task_list() == []
