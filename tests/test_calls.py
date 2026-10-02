"""Jarvis telefoniert selbst: Nummernsperre, Pausen-Erkennung, kompletter Gesprächsablauf (ohne echtes Audio)."""

import asyncio
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis import calls
from jarvis.brain import Reply
from jarvis.calls import CallAgent, CallStore, Segmenter, parse_reply
from jarvis.config import Config
from jarvis.phone import PhoneStore
from jarvis.server import create_app
from tests.test_server import FakeBrain


def test_blocked_numbers(tmp_path):
    store = CallStore(tmp_path)
    for bad in ("112", "110", "+49 112", "0900 123456", "0137 1234567", "11833", "116117", "abc"):
        with pytest.raises(ValueError):
            store.create(bad, "Termin")
    call = store.create("0049 30 1234567", "Termin für Samstag", "Friseur")
    assert call["number"] == "+49301234567" and call["status"] == "queued"
    with pytest.raises(RuntimeError, match="läuft schon"):
        store.create("030 7654321", "noch einer")
    with pytest.raises(ValueError, match="goal"):
        CallStore(tmp_path / "x").create("030 7654321", " ")


def test_report_states(tmp_path):
    store = CallStore(tmp_path)
    c = store.create("030 1234567", "Test")
    assert store.report(c["id"], "dialing")["status"] == "dialing"
    assert store.report(c["id"], "ended", "vom Nutzer beendet")["status"] == "ended"
    assert store.report(c["id"], "dialing")["status"] == "ended"  # nicht wieder aufleben
    assert store.report("gibtsnicht", "ended") is None


def test_parse_reply():
    spoken, hang, result = parse_reply("Super, dann **Samstag** um zehn. Tschüss! [AUFLEGEN] "
                                       "[ERGEBNIS: Termin Sa 10 Uhr bei Müller]")
    assert spoken == "Super, dann Samstag um zehn. Tschüss!" and hang and result == "Termin Sa 10 Uhr bei Müller"
    assert parse_reply("Wann passt es Ihnen?") == ("Wann passt es Ihnen?", False, "")


def tone(seconds, amp=0.2):
    t = np.arange(int(16000 * seconds)) / 16000
    return (amp * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


def frames_of(x):
    return [x[i:i + calls.FRAME] for i in range(0, len(x) - calls.FRAME + 1, calls.FRAME)]


def test_segmenter_cuts_utterances():
    seg = Segmenter(end_silence=0.5)
    noise = (np.random.default_rng(1).normal(0, 0.002, 16000 * 2)).astype(np.float32)
    signal = np.concatenate([noise, tone(1.0), noise, tone(0.1), noise])  # Satz + kurzer Knacks
    out = [u for f in frames_of(signal) if (u := seg.feed(f)) is not None]
    assert len(out) == 1 and 0.9 < len(out[0]) / 16000 < 2.0


class FakeAudio:
    """Spielt dem Agenten vorbereitete Äußerungen vor (Ton) und merkt sich, was er „spricht“."""

    def __init__(self, utterances):
        self.utterances = list(utterances)
        self.played = []
        self.closed = False

    def frames(self, timeout):
        while True:
            if self.utterances:
                self.utterances.pop(0)
                for f in frames_of(np.concatenate([tone(0.6), np.zeros(16000, np.float32)])):
                    yield f
            else:
                time.sleep(0.002)
                yield np.zeros(calls.FRAME, np.float32)

    def play(self, samples, rate):
        self.played.append(self.current)

    def close(self):
        self.closed = True


class ScriptBrain:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def ask(self, message, conversation="web", **kw):
        self.calls.append((message, conversation, kw))
        return Reply(self.replies.pop(0) if self.replies else "Zusammenfassung", "sid")


def make_agent(tmp_path, heard, replies):
    cfg = Config(data_dir=tmp_path / "data", workspace=tmp_path / "ws", user_name="Robin")
    cfg.ensure_dirs()
    brain = ScriptBrain(replies)
    audio = FakeAudio(heard)
    texts = list(heard)
    agent = CallAgent(cfg, brain, audio_factory=lambda: audio, stt=lambda samples: texts.pop(0))

    def fake_tts(text):
        audio.current = text
        return np.zeros(10, np.float32), 16000

    agent.tts = fake_tts
    agent.answer_timeout, agent.turn_timeout = 3, 0.3
    return agent, brain, audio


def test_full_call(tmp_path):
    agent, brain, audio = make_agent(
        tmp_path, ["Friseur Müller, guten Tag?", "Samstag um zehn hätte ich frei."],
        ["Ich würde gern einen Termin für Samstagvormittag ausmachen.",
         "Perfekt, Samstag um zehn. Vielen Dank, auf Wiederhören! [AUFLEGEN] [ERGEBNIS: Termin Samstag 10 Uhr]"])
    call = agent.store.create("030 1234567", "Termin Samstagvormittag", "Friseur Müller")
    agent.store.report(call["id"], "dialing")
    asyncio.run(agent.tick())

    done = agent.store.get(call["id"])
    assert done["status"] == "ended" and done["result"] == "Termin Samstag 10 Uhr"
    # Pflicht-Vorstellung als KI steht im ersten gesprochenen Satz
    assert audio.played[0].startswith("Guten Tag, hier spricht Jarvis, ein KI-Assistent, im Auftrag von Robin.")
    assert audio.played[-1] == "Perfekt, Samstag um zehn. Vielen Dank, auf Wiederhören!"
    assert [t["who"] for t in done["transcript"]] == ["gegenüber", "jarvis", "gegenüber", "jarvis"]
    # schneller Modus + Regeln nur im ersten Zug
    first, second = brain.calls[0][2], brain.calls[1][2]
    assert first["lite"] and "KI-Assistent von Robin" in first["context"] and second["context"] == ""
    actions = PhoneStore(agent.cfg.data_dir).pending()
    assert [a["type"] for a in actions] == ["agent_hangup", "notify"]
    assert "Termin Samstag 10 Uhr" in actions[1]["params"]["text"]
    assert audio.closed and len(list((agent.cfg.workspace / "anrufe").glob("*.md"))) == 1


def test_nobody_answers(tmp_path):
    agent, _, audio = make_agent(tmp_path, [], [])
    agent.answer_timeout = 0.3
    call = agent.store.create("030 1234567", "Test")
    agent.store.report(call["id"], "dialing")
    asyncio.run(agent.tick())
    done = agent.store.get(call["id"])
    assert done["status"] == "failed" and "Niemand" in done["error"] and audio.played == []
    notify = [a for a in PhoneStore(agent.cfg.data_dir).pending() if a["type"] == "notify"][0]
    assert "fehlgeschlagen" in notify["params"]["title"]


def test_phone_never_dialed(tmp_path):
    agent, _, _ = make_agent(tmp_path, [], [])
    call = agent.store.create("030 1234567", "Test")
    agent.store.update(call["id"], created=time.time() - 200)
    asyncio.run(agent.tick())
    assert agent.store.get(call["id"])["status"] == "failed"


def test_call_state_endpoint(tmp_path):
    cfg = Config(token="geheim", data_dir=tmp_path, workspace=tmp_path)
    c = TestClient(create_app(cfg, FakeBrain()))
    auth = {"Authorization": "Bearer geheim"}
    call = CallStore(tmp_path).create("030 1234567", "Test")
    assert c.post(f"/api/calls/{call['id']}/state", json={"state": "dialing"}, headers=auth).json() == {"status": "dialing"}
    assert c.post(f"/api/calls/{call['id']}/state", json={"state": "kaputt"}, headers=auth).status_code == 400
    assert c.post("/api/calls/nix/state", json={"state": "ended"}, headers=auth).status_code == 404
    assert c.get("/api/calls", headers=auth).json()["calls"][0]["status"] == "dialing"


def test_mcp_tools(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("JARVIS_DATA", str(tmp_path))
    monkeypatch.setenv("CALL_AUDIO_IN", "CABLE Output")
    monkeypatch.setenv("CALL_AUDIO_OUT", "CABLE-A Input")
    from jarvis import mcp_phone

    mod = importlib.reload(mcp_phone)
    res = mod.phone_agent_call("030 1234567", "Termin ausmachen", "Friseur")
    action = PhoneStore(tmp_path).pending()[0]
    assert action["type"] == "agent_call" and action["params"]["call_id"] == res["call_id"]
    assert mod.phone_agent_call_status()["status"] == "queued"
    assert mod.phone_agent_hangup()["ok"] and CallStore(tmp_path).latest()["abort"] is True
