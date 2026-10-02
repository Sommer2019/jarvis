"""Natürliche Stimme: jarvis voice-setup + /api/tts."""

import sys
import types

from jarvis import tts, voice_setup
from tests.test_server import make


def test_model_urls_and_quality(monkeypatch):
    onnx, meta = voice_setup.model_urls("thorsten", "high")
    assert onnx == ("https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/"
                    "thorsten/high/de_DE-thorsten-high.onnx")
    assert meta == onnx + ".json"
    monkeypatch.setattr(voice_setup.platform, "machine", lambda: "aarch64")
    assert voice_setup.default_quality("thorsten") == "medium"   # Handy: schnellere Variante
    assert voice_setup.default_quality("kerstin") == "low"
    monkeypatch.setattr(voice_setup.platform, "machine", lambda: "x86_64")
    assert voice_setup.default_quality("thorsten") == "high"


def test_setup_downloads_and_sets_env(tmp_path, monkeypatch, capsys):
    fetched, env = [], {}
    monkeypatch.setattr(voice_setup, "ensure_piper", lambda: True)
    monkeypatch.setattr(voice_setup.platform, "machine", lambda: "x86_64")

    def fake_download(url, target):
        fetched.append(url)
        target.write_text("modell")

    monkeypatch.setattr(voice_setup, "_download", fake_download)
    loaded = []
    fake = types.ModuleType("piper")
    fake.PiperVoice = types.SimpleNamespace(load=lambda p: loaded.append(p))
    monkeypatch.setitem(sys.modules, "piper", fake)

    onnx = voice_setup.setup(tmp_path / "models", set_env=lambda k, v: env.update({k: v}))
    assert onnx == tmp_path / "models" / "de_DE-thorsten-high.onnx" and onnx.exists()
    assert len(fetched) == 2 and env == {"PIPER_VOICE": str(onnx)} and loaded == [str(onnx)]
    # zweiter Lauf lädt nichts erneut
    voice_setup.setup(tmp_path / "models", set_env=lambda k, v: None)
    assert len(fetched) == 2
    assert voice_setup.setup(tmp_path, "gibtsnicht") is None
    assert voice_setup.setup(tmp_path, "kerstin", "high") is None
    assert "Natürliche Stimme" in capsys.readouterr().out


def test_tts_endpoint(tmp_path, monkeypatch):
    c, _ = make(tmp_path)
    auth = {"Authorization": "Bearer geheim"}
    assert c.post("/api/tts", json={"text": "Hallo"}, headers=auth).status_code == 501  # nicht eingerichtet
    calls = []

    async def fake_synth(text, cfg, rate=1.0):
        calls.append((text, rate))
        return b"RIFF-wav"

    monkeypatch.setattr(tts, "available", lambda cfg: True)
    monkeypatch.setattr(tts, "synthesize", fake_synth)
    r = c.post("/api/tts", json={"text": "Guten Morgen.", "rate": 1.2}, headers=auth)
    assert r.status_code == 200 and r.content == b"RIFF-wav" and r.headers["content-type"] == "audio/wav"
    assert calls == [("Guten Morgen.", 1.2)]
    assert c.post("/api/tts", json={"text": "  "}, headers=auth).status_code == 400
    assert c.get("/api/health").json()["tts"] is True
