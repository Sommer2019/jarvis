#!/usr/bin/env python3
"""Jarvis-PC-Agent: lässt Jarvis deinen Laptop/PC steuern (Windows, macOS, Linux).

Läuft auf dem PC, holt Aufgaben vom Jarvis-Server und führt sie aus: Programme,
Webseiten und Dateien öffnen, Lautstärke, Musik, Sperren, Standby, Benachrichtigungen,
Vorlesen, Zwischenablage, Status – und eigene, freigegebene Befehle.

Nur Python-Standardbibliothek → läuft auch ohne Jarvis-Installation:
    python pc.py --server https://jarvis.dein-tailnet.ts.net --token DEIN_TOKEN
    python pc.py --server … --token … --install     # Autostart einrichten

Eigene Befehle (z.B. Backup-Skript) legst du NUR auf dem PC selbst fest, in
~/.jarvis-pc-commands.json:   {"backup": "C:\\\\Skripte\\\\backup.bat", "teams": "start msteams:"}
Jarvis kann ausschließlich diese Namen ausführen – keine beliebigen Befehle.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

VERSION = "1.0"
SYSTEM = platform.system()  # "Windows", "Darwin", "Linux"
COMMANDS_FILE = Path.home() / ".jarvis-pc-commands.json"

PC_ACTION_TYPES = {
    "open_url": "Webseite öffnen (url)",
    "open_app": "Programm starten (name)",
    "open_path": "Datei/Ordner öffnen (path)",
    "media": "Musik/Medien steuern (key: play_pause|next|previous|stop)",
    "volume": "Lautstärke (level 0-100 oder change: up|down|mute)",
    "lock": "Bildschirm sperren",
    "sleep": "Standby",
    "shutdown": "Herunterfahren (minutes)",
    "cancel_shutdown": "Herunterfahren abbrechen",
    "notify": "Benachrichtigung anzeigen (title, text)",
    "speak": "Text vorlesen (text)",
    "clipboard_set": "Text in die Zwischenablage (text)",
    "clipboard_get": "Zwischenablage lesen",
    "status": "Gerätestatus (Akku, System)",
    "run": "Freigegebenen Befehl ausführen (name)",
}


# ------------------------------------------------------------------ Helfer
def _bg(cmd, **kw) -> None:
    """Befehl im Hintergrund starten (nicht warten)."""
    flags = 0x08000000 if SYSTEM == "Windows" else 0  # CREATE_NO_WINDOW
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, **kw)


def _run(cmd, **kw) -> str:
    flags = 0x08000000 if SYSTEM == "Windows" else 0
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, creationflags=flags, **kw)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"Exit {r.returncode}").strip()[:300])
    return r.stdout.strip()


def _ps(script: str, wait: bool = True) -> str:
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    if wait:
        return _run(cmd)
    _bg(cmd)
    return ""


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _osa_quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _win_key(vk: int, times: int = 1) -> None:
    import ctypes

    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"'{tool}' ist auf diesem PC nicht installiert")
    return path


# --------------------------------------------------------------- Aktionen
def act_open_url(p):
    url = p["url"]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"geöffnet: {url}"


def act_open_app(p):
    name = p["name"]
    if SYSTEM == "Windows":
        _bg(["cmd", "/c", "start", "", name])
    elif SYSTEM == "Darwin":
        _run(["open", "-a", name])
    else:
        if shutil.which("gtk-launch") and subprocess.run(["gtk-launch", name], capture_output=True).returncode == 0:
            return f"gestartet: {name}"
        exe = shutil.which(name) or shutil.which(name.lower())
        if not exe:
            raise RuntimeError(f"Programm '{name}' nicht gefunden")
        _bg([exe], start_new_session=True)
    return f"gestartet: {name}"


def act_open_path(p):
    path = os.path.expanduser(p["path"])
    if not os.path.exists(path):
        raise RuntimeError(f"'{path}' existiert nicht")
    if SYSTEM == "Windows":
        os.startfile(path)  # noqa: S606
    elif SYSTEM == "Darwin":
        _run(["open", path])
    else:
        _bg(["xdg-open", path], start_new_session=True)
    return f"geöffnet: {path}"


_WIN_MEDIA = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
_MPRIS = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}


def act_media(p):
    key = p.get("key", "play_pause")
    if key not in _WIN_MEDIA:
        raise ValueError("key muss play_pause, next, previous oder stop sein")
    if SYSTEM == "Windows":
        _win_key(_WIN_MEDIA[key])
    elif SYSTEM == "Darwin":
        cmd = {"play_pause": "playpause", "next": "next track", "previous": "previous track", "stop": "pause"}[key]
        for app in ("Spotify", "Music"):
            try:
                _run(["osascript", "-e", f'if application "{app}" is running then tell application "{app}" to {cmd}'])
            except RuntimeError:
                pass
    else:
        _run([_need("playerctl"), _MPRIS[key]])
    return f"Medien: {key}"


def act_volume(p):
    level, change = p.get("level"), p.get("change")
    if SYSTEM == "Windows":
        if change == "mute":
            _win_key(0xAD)
        elif change in ("up", "down"):
            _win_key(0xAF if change == "up" else 0xAE, 5)  # 5 Schritte à 2 %
        elif level is not None:
            _win_key(0xAE, 50)  # erst ganz leise, dann auf Zielwert
            _win_key(0xAF, round(int(level) / 2))
    elif SYSTEM == "Darwin":
        if change == "mute":
            _run(["osascript", "-e", "set volume with output muted"])
        elif change in ("up", "down"):
            cur = int(_run(["osascript", "-e", "output volume of (get volume settings)"]) or 50)
            _run(["osascript", "-e", f"set volume output volume {max(0, min(100, cur + (10 if change == 'up' else -10)))}"])
        elif level is not None:
            _run(["osascript", "-e", f"set volume output volume {int(level)}"])
    else:
        pactl = _need("pactl")
        if change == "mute":
            _run([pactl, "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        elif change in ("up", "down"):
            _run([pactl, "set-sink-volume", "@DEFAULT_SINK@", "+10%" if change == "up" else "-10%"])
        elif level is not None:
            _run([pactl, "set-sink-volume", "@DEFAULT_SINK@", f"{int(level)}%"])
    return f"Lautstärke: {level if level is not None else change}"


def act_lock(p):
    if SYSTEM == "Windows":
        _run(["rundll32.exe", "user32.dll,LockWorkStation"])
    elif SYSTEM == "Darwin":
        _run(["pmset", "displaysleepnow"])
    else:
        for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"]):
            if shutil.which(cmd[0]) and subprocess.run(cmd, capture_output=True).returncode == 0:
                break
        else:
            raise RuntimeError("Keine Sperr-Funktion gefunden")
    return "gesperrt"


def act_sleep(p):
    if SYSTEM == "Windows":
        _bg(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    elif SYSTEM == "Darwin":
        _bg(["pmset", "sleepnow"])
    else:
        _bg(["systemctl", "suspend"])
    return "Standby"


def act_shutdown(p):
    minutes = max(1, int(p.get("minutes", 1)))
    if SYSTEM == "Windows":
        _run(["shutdown", "/s", "/t", str(minutes * 60)])
    elif SYSTEM == "Darwin":
        _run(["osascript", "-e", f'do shell script "shutdown -h +{minutes}" with administrator privileges'])
    else:
        _run(["shutdown", "-h", f"+{minutes}"])
    return f"fährt in {minutes} Min. herunter"


def act_cancel_shutdown(p):
    if SYSTEM == "Windows":
        _run(["shutdown", "/a"])
    else:
        _run(["shutdown", "-c"])
    return "Herunterfahren abgebrochen"


def act_notify(p):
    title, text = p.get("title") or "Jarvis", p.get("text", "")
    if SYSTEM == "Windows":
        _ps("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
            "$n=New-Object System.Windows.Forms.NotifyIcon; $n.Icon=[System.Drawing.SystemIcons]::Information; "
            f"$n.Visible=$true; $n.ShowBalloonTip(10000,{_ps_quote(title)},{_ps_quote(text)},"
            "[System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep 11; $n.Dispose()", wait=False)
    elif SYSTEM == "Darwin":
        _run(["osascript", "-e", f"display notification {_osa_quote(text)} with title {_osa_quote(title)}"])
    else:
        _run([_need("notify-send"), title, text])
    return "angezeigt"


def act_speak(p):
    text = p.get("text", "")
    if SYSTEM == "Windows":
        _ps("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Speak({_ps_quote(text)})", wait=False)
    elif SYSTEM == "Darwin":
        _bg(["say", text])
    else:
        tool = shutil.which("spd-say") or shutil.which("espeak-ng") or shutil.which("espeak")
        if not tool:
            raise RuntimeError("Keine Sprachausgabe installiert (spd-say/espeak)")
        _bg([tool, text])
    return "wird vorgelesen"


def act_clipboard_set(p):
    text = p.get("text", "")
    if SYSTEM == "Windows":
        _ps(f"Set-Clipboard -Value {_ps_quote(text)}")
    elif SYSTEM == "Darwin":
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
    else:
        tool = (["wl-copy"] if shutil.which("wl-copy") else [_need("xclip"), "-selection", "clipboard"])
        subprocess.run(tool, input=text, text=True, check=True)
    return "in der Zwischenablage"


def act_clipboard_get(p):
    if SYSTEM == "Windows":
        text = _ps("Get-Clipboard -Raw")
    elif SYSTEM == "Darwin":
        text = _run(["pbpaste"])
    else:
        text = _run(["wl-paste"] if shutil.which("wl-paste") else [_need("xclip"), "-selection", "clipboard", "-o"])
    return text[:5000]


def _battery() -> str:
    try:
        import psutil  # optional

        b = psutil.sensors_battery()
        if b:
            return f"{int(b.percent)} %" + (" (lädt)" if b.power_plugged else "")
    except Exception:  # noqa: BLE001
        pass
    if SYSTEM == "Linux":
        for bat in Path("/sys/class/power_supply").glob("BAT*"):
            try:
                cap = (bat / "capacity").read_text().strip()
                status = (bat / "status").read_text().strip()
                return f"{cap} % ({status})"
            except OSError:
                continue
    if SYSTEM == "Darwin":
        try:
            return _run(["pmset", "-g", "batt"]).splitlines()[-1].split("\t")[-1]
        except Exception:  # noqa: BLE001
            pass
    if SYSTEM == "Windows":
        try:
            return _ps("(Get-CimInstance Win32_Battery).EstimatedChargeRemaining") + " %"
        except Exception:  # noqa: BLE001
            pass
    return "unbekannt (kein Akku?)"


def act_status(p):
    return {"hostname": socket.gethostname(), "system": f"{SYSTEM} {platform.release()}",
            "user": os.getenv("USER") or os.getenv("USERNAME") or "?", "battery": _battery(),
            "time": time.strftime("%Y-%m-%d %H:%M"), "commands": sorted(load_commands())}


def load_commands() -> dict[str, str]:
    try:
        data = json.loads(COMMANDS_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        return {}


def act_run(p):
    cmds = load_commands()
    name = p.get("name", "")
    if name not in cmds:
        raise RuntimeError(f"Befehl '{name}' ist auf diesem PC nicht freigegeben. Vorhanden: {', '.join(cmds) or 'keine'}")
    out = subprocess.run(cmds[name], shell=True, capture_output=True, text=True, timeout=120)  # noqa: S602 – lokal freigegeben
    return {"exit": out.returncode, "output": (out.stdout + out.stderr).strip()[-2000:]}


ACTIONS = {name: globals()[f"act_{name}"] for name in PC_ACTION_TYPES}


def run_action(action: dict) -> tuple[bool, object]:
    fn = ACTIONS.get(action.get("type"))
    if not fn:
        return False, f"Unbekannte Aktion {action.get('type')}"
    try:
        return True, fn(action.get("params") or {})
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ------------------------------------------------------------------ Agent
class Agent:
    def __init__(self, server: str, token: str, name: str = ""):
        self.server = server.rstrip("/")
        self.token = token
        self.name = name or socket.gethostname()

    def _req(self, method: str, path: str, body: dict | None = None, timeout: int = 40):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.server + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "User-Agent": f"jarvis-pc-agent/{VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def hello(self) -> None:
        self._req("POST", "/api/pc/hello", {"device": self.name, "system": f"{SYSTEM} {platform.release()}",
                                            "commands": sorted(load_commands()), "version": VERSION})

    def run_forever(self) -> None:
        print(f"Jarvis-PC-Agent „{self.name}“ verbindet mit {self.server} …", flush=True)
        backoff = 2
        registered = False
        while True:
            try:
                if not registered:
                    self.hello()
                    registered = True
                    print("✅ verbunden – warte auf Aufgaben", flush=True)
                    backoff = 2
                res = self._req("GET", f"/api/pc/actions?device={urllib.parse.quote(self.name)}&wait=25")
                for action in res.get("actions", []):
                    ok, result = run_action(action)
                    print(f"{'✔' if ok else '✖'} {action['type']}: {result}", flush=True)
                    self._req("POST", f"/api/pc/actions/{action['id']}/done", {"ok": ok, "result": result})
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print("❌ Token falsch – bitte prüfen (jarvis token)", flush=True)
                    time.sleep(60)
                else:
                    print(f"Serverfehler {e.code}, neuer Versuch …", flush=True)
                    time.sleep(backoff)
                registered = False
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"Keine Verbindung ({e}), neuer Versuch in {backoff}s …", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                registered = False


# -------------------------------------------------------------- Autostart
def install_autostart(server: str, token: str, name: str) -> str:
    script = Path(__file__).resolve()
    py = sys.executable
    args = [str(script), "--server", server, "--token", token] + (["--name", name] if name else [])
    if SYSTEM == "Windows":
        startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
        pyw = Path(py).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else py)
        target = startup / "jarvis-pc-agent.vbs"  # .vbs startet ohne sichtbares Fenster
        cmdline = " ".join(f'""{a}""' for a in [exe] + args)
        target.write_text(f'CreateObject("WScript.Shell").Run "{cmdline}", 0, False\n', encoding="utf-8")
        return str(target)
    if SYSTEM == "Darwin":
        target = Path.home() / "Library/LaunchAgents/de.jarvis.pc-agent.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        items = "".join(f"<string>{a}</string>" for a in [py] + args)
        target.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>de.jarvis.pc-agent</string>
<key>ProgramArguments</key><array>{items}</array><key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
""")
        subprocess.run(["launchctl", "load", "-w", str(target)], capture_output=True)
        return str(target)
    target = Path.home() / ".config/systemd/user/jarvis-pc-agent.service"
    target.parent.mkdir(parents=True, exist_ok=True)
    quoted = " ".join(f'"{a}"' for a in [py] + args)
    target.write_text(f"[Unit]\nDescription=Jarvis PC-Agent\nAfter=graphical-session.target\n\n"
                      f"[Service]\nExecStart={quoted}\nRestart=always\nRestartSec=5\n\n"
                      f"[Install]\nWantedBy=default.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "jarvis-pc-agent"], capture_output=True)
    return str(target)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Jarvis-PC-Agent – lässt Jarvis diesen PC steuern")
    ap.add_argument("--server", default=os.getenv("JARVIS_SERVER", ""), help="Adresse des Jarvis-Servers")
    ap.add_argument("--token", default=os.getenv("JARVIS_TOKEN", ""), help="Jarvis-Token (jarvis token)")
    ap.add_argument("--name", default=os.getenv("JARVIS_PC_NAME", ""), help="Name dieses PCs (Standard: Rechnername)")
    ap.add_argument("--install", action="store_true", help="Autostart beim Anmelden einrichten")
    ap.add_argument("--test", metavar="AKTION", help="Aktion lokal testen, z.B. --test status")
    a = ap.parse_args(argv)
    if a.test:
        print(run_action({"type": a.test, "params": {}}))
        return
    if not a.server or not a.token:
        ap.error("--server und --token angeben (oder JARVIS_SERVER/JARVIS_TOKEN setzen)")
    if a.install:
        print(f"✅ Autostart eingerichtet: {install_autostart(a.server, a.token, a.name)}")
        if SYSTEM == "Windows":
            print("   Startet ab der nächsten Anmeldung automatisch im Hintergrund.")
        return
    try:
        Agent(a.server, a.token, a.name).run_forever()
    except KeyboardInterrupt:
        print("\nbeendet")


if __name__ == "__main__":
    main()
