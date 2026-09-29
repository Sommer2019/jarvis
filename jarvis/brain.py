"""Das "Gehirn": ruft die Claude-Code-CLI im Headless-Modus auf.

Warum die CLI und nicht die API?  `claude -p` authentifiziert sich über deinen
Claude-Pro/Max-Login (OAuth-Token aus `claude setup-token`). Damit laufen alle
Anfragen über dein Abo – es entstehen keine API-Kosten pro Token.

Wichtig: Ist ANTHROPIC_API_KEY gesetzt, würde Claude Code stattdessen über die
(kostenpflichtige) API abrechnen. Deshalb entfernen wir diese Variable hier
grundsätzlich aus der Umgebung des Unterprozesses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .phone import PhoneStore

log = logging.getLogger("jarvis.brain")

# Variablen, die Claude Code auf API-Abrechnung umschalten würden.
_BILLING_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

VOICE_HINT = (
    "Diese Nachricht kam per Sprache und deine Antwort wird vorgelesen: "
    "antworte kurz und natürlich gesprochen, ohne Markdown, ohne Listen-Symbole, ohne Links."
)


@dataclass
class Reply:
    text: str
    session_id: str | None = None
    is_error: bool = False


class SessionStore:
    """Merkt sich pro Kanal (web, telegram:<id>, ...) die Claude-Session-ID."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, str] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text())
            except json.JSONDecodeError:
                log.warning("sessions.json beschädigt – starte neu")

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, session_id: str) -> None:
        self._data[key] = session_id
        self._save()

    def reset(self, key: str) -> None:
        if self._data.pop(key, None) is not None:
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2))


# Variablen einer umgebenden Claude-Code-Sitzung (falls Jarvis aus einem Claude-Code-
# Terminal gestartet wird) – sonst hängt sich der Unterprozess an deren Session.
_NESTING_ENV_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_REMOTE_SESSION_ID",
)


def subscription_env() -> dict[str, str]:
    drop = set(_BILLING_ENV_VARS) | set(_NESTING_ENV_VARS)
    return {k: v for k, v in os.environ.items() if k not in drop}


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.sessions = SessionStore(cfg.data_dir / "sessions.json")
        self._locks: dict[str, asyncio.Lock] = {}
        self.mcp_config_path = cfg.data_dir / "mcp.json"
        self._write_mcp_config()
        self._seed_workspace()

    def _seed_workspace(self) -> None:
        seeds = {
            "memory.md": "# Gedächtnis\n\n(Dauerhafte Fakten über den Nutzer – Jarvis pflegt diese Datei.)\n",
            "todo.md": "# Aufgaben\n\n",
        }
        for name, content in seeds.items():
            path = self.cfg.workspace / name
            if not path.exists():
                path.write_text(content)

    # ------------------------------------------------------------------ setup
    def _write_mcp_config(self) -> None:
        pkg_root = str(Path(__file__).resolve().parent.parent)

        def server(module: str, **env: str) -> dict:
            base = {"JARVIS_DATA": str(self.cfg.data_dir), "JARVIS_TIMEZONE": self.cfg.timezone,
                    "PYTHONPATH": pkg_root}
            return {"command": sys.executable, "args": ["-m", module], "env": {**base, **env}}

        servers: dict = {}
        if self.cfg.google_enabled:
            servers["google"] = server(
                "jarvis.mcp_google",
                JARVIS_ALLOW_SEND_EMAIL="true" if self.cfg.allow_send_email else "false",
                GOOGLE_CREDENTIALS_FILE=str(self.cfg.google_credentials_file),
                GOOGLE_TOKEN_FILE=str(self.cfg.google_token_file),
            )
        if self.cfg.caldav_url or self.cfg.carddav_url:
            # Zugangsdaten erbt der Unterprozess aus der Umgebung (CALDAV_*/CARDDAV_*),
            # damit Passwörter nicht in data/mcp.json landen.
            servers["dav"] = server("jarvis.mcp_dav")
        if self.phone_enabled():
            servers["phone"] = server("jarvis.mcp_phone")
        self.mcp_servers = list(servers)
        self.mcp_config_path.write_text(json.dumps({"mcpServers": servers}, indent=2))

    def phone_enabled(self) -> bool:
        mode = self.cfg.phone.lower()
        if mode in ("auto", ""):
            return PhoneStore(self.cfg.data_dir).app_registered()
        return mode in ("1", "true", "yes", "ja", "on")

    def _seed_workspace(self) -> None:
        seeds = {
            "memory.md": "# Gedächtnis\n\n(Dauerhafte Fakten über den Nutzer – Jarvis pflegt diese Datei.)\n",
            "todo.md": "# Aufgaben\n\n",
        }
        for name, content in seeds.items():
            path = self.cfg.workspace / name
            if not path.exists():
                path.write_text(content)

    # Eingebaute Claude-Code-Tools: Gedächtnis/Notizen im Workspace + Recherche.
    # Bash ist bewusst NICHT dabei.
    BUILTIN_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"]

    def builtin_tools(self) -> list[str]:
        extra = [t.split("(")[0] for t in self.cfg.extra_tools if not t.startswith("mcp__")]
        return list(dict.fromkeys(self.BUILTIN_TOOLS + extra))

    def allowed_tools(self) -> list[str]:
        tools = list(self.BUILTIN_TOOLS)
        # "mcp__<server>" erlaubt alle Tools des jeweiligen MCP-Servers
        tools.extend(f"mcp__{name}" for name in self.mcp_servers)
        tools.extend(self.cfg.extra_tools)
        return tools

    def build_command(self, session_id: str | None) -> list[str]:
        cmd = [
            self.cfg.claude_bin,
            "-p",
            "--output-format", "json",
            "--mcp-config", str(self.mcp_config_path),
            "--strict-mcp-config",
            "--tools", ",".join(self.builtin_tools()),
            "--allowedTools", ",".join(self.allowed_tools()),
            # Alles, was nicht explizit erlaubt ist, wird ohne Nachfrage abgelehnt
            "--permission-mode", "dontAsk",
        ]
        if self.cfg.claude_model:
            cmd += ["--model", self.cfg.claude_model]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

    def context_header(self, channel: str) -> str:
        now = datetime.now(ZoneInfo(self.cfg.timezone))
        stamp = f"{_WEEKDAYS[now.weekday()]}, {now:%d.%m.%Y %H:%M} ({self.cfg.timezone})"
        who = f" | Nutzer: {self.cfg.user_name}" if self.cfg.user_name else ""
        return f"[Jetzt: {stamp} | Kanal: {channel}{who}]"

    # ----------------------------------------------------------------- public
    def reset(self, conversation: str) -> None:
        self.sessions.reset(conversation)

    async def ask(self, message: str, conversation: str = "web", *, voice: bool = False,
                  channel: str | None = None) -> Reply:
        lock = self._locks.setdefault(conversation, asyncio.Lock())
        async with lock:
            prompt = self.context_header(channel or conversation)
            if voice:
                prompt += "\n" + VOICE_HINT
            prompt += "\n\n" + message

            session_id = self.sessions.get(conversation)
            reply = await self._run(prompt, session_id)
            if reply.is_error and session_id and "No conversation found" in reply.text:
                log.info("Session %s abgelaufen – starte neue", session_id)
                self.sessions.reset(conversation)
                reply = await self._run(prompt, None)
            if reply.session_id and not reply.is_error:
                self.sessions.set(conversation, reply.session_id)
            return reply

    async def _run(self, prompt: str, session_id: str | None) -> Reply:
        self._write_mcp_config()  # z.B. Handy-App kann inzwischen verbunden sein
        cmd = self.build_command(session_id)
        log.debug("Starte: %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cfg.workspace),
                env=subscription_env(),
            )
        except FileNotFoundError:
            return Reply(f"Claude-CLI nicht gefunden ({self.cfg.claude_bin}). Ist Claude Code installiert?",
                         is_error=True)
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), self.cfg.claude_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return Reply("Das hat zu lange gedauert – ich habe abgebrochen.", is_error=True)
        return parse_output(out.decode(errors="replace"), err.decode(errors="replace"), proc.returncode)


def parse_output(stdout: str, stderr: str, returncode: int | None) -> Reply:
    stdout = stdout.strip()
    data = None
    if stdout:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Falls doch mehrere JSON-Zeilen kommen: letzte gültige nehmen
            for line in reversed(stdout.splitlines()):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    if isinstance(data, list):  # stream-artige Ausgabe
        data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), None)
    if not isinstance(data, dict):
        msg = (stderr or stdout or "keine Ausgabe").strip()
        return Reply(f"Fehler von Claude Code: {msg[-500:]}", is_error=True)

    text = data.get("result") or ""
    is_error = bool(data.get("is_error")) or (returncode not in (0, None))
    if is_error and not text:
        text = (stderr or data.get("subtype") or "unbekannter Fehler").strip()[-500:]
    return Reply(text=text.strip(), session_id=data.get("session_id"), is_error=is_error)
