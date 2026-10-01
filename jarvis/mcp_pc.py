"""MCP-Server `pc`: Laptop/PC steuern über den Jarvis-PC-Agent."""

from __future__ import annotations

import os
from pathlib import Path

from .mcp_common import JarvisMCP
from .pcstore import PcStore

store = PcStore(Path(os.getenv("JARVIS_DATA", Path(__file__).resolve().parent.parent / "data")))
mcp = JarvisMCP("pc")
WAIT = float(os.getenv("JARVIS_PC_WAIT", "20"))


def _do(action: str, params: dict | None = None, device: str = ""):
    name = store.resolve(device)
    item = store.queue.queue(action, params or {}, device=name)
    res = store.queue.wait(item["id"], timeout=WAIT)
    if res is None:
        return {"device": name, "status": "keine Antwort", "hinweis": "Der PC hat nicht reagiert (offline/Standby?). "
                "Die Aufgabe verfällt nach 2 Minuten."}
    if not res.get("ok"):
        raise RuntimeError(f"{name}: {res.get('result')}")
    return {"device": name, "result": res.get("result")}


@mcp.tool()
def pc_devices() -> list[dict]:
    """Verbundene PCs/Laptops (online-Status, System, freigegebene Befehle)."""
    return store.devices()


@mcp.tool()
def pc_open_url(url: str, device: str = "") -> dict:
    """Öffnet eine Webseite im Browser des PCs."""
    return _do("open_url", {"url": url}, device)


@mcp.tool()
def pc_open_app(name: str, device: str = "") -> dict:
    """Startet ein Programm (z.B. 'spotify', 'chrome', 'notepad', 'Visual Studio Code')."""
    return _do("open_app", {"name": name}, device)


@mcp.tool()
def pc_open_path(path: str, device: str = "") -> dict:
    """Öffnet eine Datei oder einen Ordner (z.B. '~/Downloads')."""
    return _do("open_path", {"path": path}, device)


@mcp.tool()
def pc_media(key: str = "play_pause", device: str = "") -> dict:
    """Musik/Medien: key = play_pause | next | previous | stop."""
    return _do("media", {"key": key}, device)


@mcp.tool()
def pc_volume(level: int | None = None, change: str = "", device: str = "") -> dict:
    """Lautstärke: level 0-100 setzen ODER change = up | down | mute."""
    params = {"level": level} if level is not None else {"change": change or "up"}
    return _do("volume", params, device)


@mcp.tool()
def pc_lock(device: str = "") -> dict:
    """Sperrt den Bildschirm."""
    return _do("lock", {}, device)


@mcp.tool()
def pc_sleep(device: str = "") -> dict:
    """Versetzt den PC in Standby. Vorher kurz bestätigen lassen."""
    return _do("sleep", {}, device)


@mcp.tool()
def pc_shutdown(minutes: int = 1, device: str = "") -> dict:
    """Fährt den PC in N Minuten herunter. NUR nach ausdrücklicher Bestätigung."""
    return _do("shutdown", {"minutes": minutes}, device)


@mcp.tool()
def pc_cancel_shutdown(device: str = "") -> dict:
    """Bricht ein geplantes Herunterfahren ab."""
    return _do("cancel_shutdown", {}, device)


@mcp.tool()
def pc_notify(text: str, title: str = "Jarvis", device: str = "") -> dict:
    """Zeigt eine Benachrichtigung auf dem PC-Bildschirm."""
    return _do("notify", {"title": title, "text": text}, device)


@mcp.tool()
def pc_speak(text: str, device: str = "") -> dict:
    """Liest einen Text über die Lautsprecher des PCs vor."""
    return _do("speak", {"text": text}, device)


@mcp.tool()
def pc_clipboard_set(text: str, device: str = "") -> dict:
    """Legt Text in die Zwischenablage des PCs (z.B. einen Entwurf zum Einfügen)."""
    return _do("clipboard_set", {"text": text}, device)


@mcp.tool()
def pc_clipboard_get(device: str = "") -> dict:
    """Liest die Zwischenablage des PCs."""
    return _do("clipboard_get", {}, device)


@mcp.tool()
def pc_status(device: str = "") -> dict:
    """Status des PCs: Akku, System, Uhrzeit, freigegebene Befehle."""
    return _do("status", {}, device)


@mcp.tool()
def pc_run(name: str, device: str = "") -> dict:
    """Führt einen auf dem PC freigegebenen Befehl aus (Namen siehe pc_devices/pc_status)."""
    return _do("run", {"name": name}, device)


if __name__ == "__main__":
    mcp.run()
