from fastapi.testclient import TestClient

from jarvis.brain import Reply
from jarvis.config import Config
from jarvis.server import create_app


class FakeBrain:
    def __init__(self):
        self.calls = []

    async def ask(self, message, conversation="web", *, voice=False, channel=None):
        self.calls.append((message, conversation, voice, channel))
        return Reply("ok: " + message, "sid")

    def reset(self, conversation):
        self.calls.append(("RESET", conversation))


def make(tmp_path):
    cfg = Config(token="geheim", data_dir=tmp_path, workspace=tmp_path)
    brain = FakeBrain()
    return TestClient(create_app(cfg, brain)), brain


def test_auth_required(tmp_path):
    c, _ = make(tmp_path)
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert c.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer falsch"}).status_code == 401


def test_chat(tmp_path):
    c, brain = make(tmp_path)
    r = c.post("/api/chat", json={"message": "Termin morgen?", "voice": True},
               headers={"Authorization": "Bearer geheim"})
    assert r.status_code == 200 and r.json()["reply"] == "ok: Termin morgen?"
    assert brain.calls[0] == ("Termin morgen?", "web:web", True, "app-sprache")


def test_query_token_and_reset(tmp_path):
    c, brain = make(tmp_path)
    assert c.post("/api/reset?token=geheim").status_code == 200
    assert brain.calls[0] == ("RESET", "web:web")


def test_static_pages(tmp_path):
    c, _ = make(tmp_path)
    assert "Jarvis" in c.get("/").text
    assert c.get("/sw.js").status_code == 200
    assert c.get("/static/manifest.webmanifest").status_code == 200


def test_generated_token(tmp_path):
    from jarvis.server import resolve_token
    cfg = Config(token="", data_dir=tmp_path)
    t = resolve_token(cfg)
    assert len(t) > 20 and resolve_token(cfg) == t
