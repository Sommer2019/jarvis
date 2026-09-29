import json
import sys
from pathlib import Path

import pytest

from jarvis.brain import Brain, parse_output
from jarvis.config import Config

FAKE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def brain(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-used")
    cfg = Config(claude_bin=str(FAKE), workspace=tmp_path / "ws", data_dir=tmp_path / "data",
                 google_enabled=True, extra_tools=[])
    b = Brain(cfg)
    b.calls = lambda: [json.loads(l) for l in log.read_text().splitlines()]
    return b


async def test_ask_and_resume(brain):
    r1 = await brain.ask("Hallo", "web")
    assert not r1.is_error and r1.text == "Antwort auf: Hallo"
    r2 = await brain.ask("Noch was", "web")
    assert r2.session_id == r1.session_id
    calls = brain.calls()
    assert "--resume" not in calls[0]["argv"]
    assert calls[1]["argv"][calls[1]["argv"].index("--resume") + 1] == r1.session_id


async def test_api_key_is_stripped(brain):
    await brain.ask("x", "web")
    assert brain.calls()[0]["api_key_present"] is False


async def test_command_restricts_tools(brain):
    await brain.ask("x", "web")
    argv = brain.calls()[0]["argv"]
    tools = argv[argv.index("--tools") + 1].split(",")
    assert "Bash" not in tools
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert "mcp__google" in allowed
    assert "--strict-mcp-config" in argv
    mcp = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())
    assert mcp["mcpServers"]["google"]["args"] == ["-m", "jarvis.mcp_google"]


async def test_voice_hint_and_context(brain):
    await brain.ask("Was steht an?", "tg", voice=True, channel="telegram-sprache")
    prompt = brain.calls()[0]["prompt"]
    assert "Kanal: telegram-sprache" in prompt and "ohne Markdown" in prompt


async def test_expired_session_restarts(brain):
    brain.sessions.set("web", "expired")
    r = await brain.ask("Hi", "web")
    assert not r.is_error and r.session_id != "expired"


async def test_reset(brain):
    await brain.ask("a", "web")
    brain.reset("web")
    await brain.ask("b", "web")
    assert "--resume" not in brain.calls()[1]["argv"]


async def test_workspace_seeded(brain):
    assert (brain.cfg.workspace / "memory.md").exists()


async def test_missing_binary(tmp_path):
    cfg = Config(claude_bin="/nope/claude", workspace=tmp_path, data_dir=tmp_path, google_enabled=False)
    r = await Brain(cfg).ask("x")
    assert r.is_error and "nicht gefunden" in r.text


def test_parse_output_garbage():
    r = parse_output("kaputt", "Invalid API key", 1)
    assert r.is_error and "Invalid API key" in r.text
