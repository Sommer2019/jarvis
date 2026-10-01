"""PC-Agent Ende-zu-Ende: Server ↔ Agent (HTTP, Long-Polling) ↔ MCP-Tools."""

import importlib
import socket
import threading
import time

import pytest
import uvicorn

from jarvis import pc
from jarvis.config import Config
from jarvis.pcstore import PcStore
from jarvis.server import create_app
from tests.test_server import FakeBrain


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    port = free_port()
    app = create_app(Config(token="geheim", data_dir=tmp_path, workspace=tmp_path), FakeBrain())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_PC_WAIT", "8")
    from jarvis import mcp_pc
    mod = importlib.reload(mcp_pc)
    yield port, tmp_path, mod
    server.should_exit = True


def test_agent_roundtrip(live, monkeypatch):
    port, data, mcp_pc = live
    calls = []
    monkeypatch.setitem(pc.ACTIONS, "notify", lambda p: calls.append(p) or "angezeigt")
    agent = pc.Agent(f"http://127.0.0.1:{port}", "geheim", "laptop")
    threading.Thread(target=agent.run_forever, daemon=True).start()
    for _ in range(50):
        if PcStore(data).any_registered():
            break
        time.sleep(0.1)
    assert [d["name"] for d in mcp_pc.pc_devices()] == ["laptop"]
    t0 = time.time()
    r = mcp_pc.pc_notify("Pizza ist da")
    assert r == {"device": "laptop", "result": "angezeigt"} and calls == [{"title": "Jarvis", "text": "Pizza ist da"}]
    assert time.time() - t0 < 4  # Long-Polling: kommt sofort an
    st = mcp_pc.pc_status()["result"]
    assert "system" in st and "battery" in st
    with pytest.raises(RuntimeError, match="nicht freigegeben"):
        mcp_pc.pc_run("backup")


def test_wrong_token_and_no_device(live):
    port, data, mcp_pc = live
    with pytest.raises(RuntimeError, match="Kein PC verbunden"):
        mcp_pc.pc_lock()
    import urllib.error
    with pytest.raises(urllib.error.HTTPError):
        pc.Agent(f"http://127.0.0.1:{port}", "falsch", "x").hello()


def test_offline_device(live):
    port, data, mcp_pc = live
    store = PcStore(data)
    store.register({"device": "alt", "system": "Windows 11"})
    d = store._load()
    d["alt"]["last_seen"] -= 3600
    store._save(d)
    with pytest.raises(RuntimeError, match="Kein PC online"):
        mcp_pc.pc_lock()


def test_phone_long_poll(live):
    port, data, _ = live
    import json
    import urllib.request
    from jarvis.phone import PhoneStore

    def later():
        time.sleep(1.5)
        PhoneStore(data).queue("ring", {"text": "Dein Zug fällt aus!"})

    threading.Thread(target=later, daemon=True).start()
    t0 = time.time()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/phone/actions?wait=10",
                                 headers={"Authorization": "Bearer geheim"})
    acts = json.loads(urllib.request.urlopen(req, timeout=15).read())["actions"]
    assert [a["type"] for a in acts] == ["ring"] and 1.0 < time.time() - t0 < 5


def test_pc_tool_list_matches_agent():
    assert set(pc.ACTIONS) == set(pc.PC_ACTION_TYPES)
