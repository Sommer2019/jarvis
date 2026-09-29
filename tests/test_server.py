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
    assert brain.calls[0] == ("Termin morgen?", "web:web", True, "web-app-sprache")


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


def test_phone_bridge(tmp_path):
    c, brain = make(tmp_path)
    h = {"Authorization": "Bearer geheim", "X-Jarvis-App": "1.0"}
    r = c.post("/api/phone/contacts", headers=h,
               json={"contacts": [{"name": "Oma", "phones": ["+49 30 123456"]}, {"name": ""}]})
    assert r.json() == {"saved": 1}
    assert (tmp_path / "phone_app.json").exists()

    from jarvis.phone import PhoneStore
    item = PhoneStore(tmp_path).queue("call", {"number": "+4930123456"})
    r = c.post("/api/chat", json={"message": "Ruf Oma an"}, headers=h)
    assert brain.calls[-1][3] == "android-app-text"
    assert [a["id"] for a in r.json()["actions"]] == [item["id"]]
    assert c.post(f"/api/phone/actions/{item['id']}/done", headers=h).json() == {"ok": True}
    assert c.get("/api/phone/actions", headers=h).json() == {"actions": []}


def test_whatsapp_webhook(tmp_path):
    import hashlib, hmac, json

    cfg = Config(token="geheim", data_dir=tmp_path, workspace=tmp_path, whatsapp_verify_token="vt",
                 whatsapp_app_secret="sec")

    class FakeWA:
        payloads = []

        def verify_webhook(self, mode, token, challenge):
            return challenge if (mode, token) == ("subscribe", "vt") else None

        async def handle_payload(self, p):
            self.payloads.append(p)

    wa = FakeWA()
    c = TestClient(create_app(cfg, FakeBrain(), whatsapp=wa))
    assert c.get("/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=123").text == "123"
    assert c.get("/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1").status_code == 403
    body = json.dumps({"entry": []}).encode()
    assert c.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=falsch"}).status_code == 401
    sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    assert c.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sig,
                                                             "Content-Type": "application/json"}).status_code == 200
    assert wa.payloads == [{"entry": []}]
