"""Zentrale Konfiguration – alles kommt aus Umgebungsvariablen bzw. der .env-Datei."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "ja", "on"}


def _list(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


@dataclass
class Config:
    # --- Claude Code -------------------------------------------------------
    claude_bin: str = field(default_factory=lambda: os.getenv("CLAUDE_BIN", "claude"))
    # leer = Standardmodell deines Abos
    claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", ""))
    claude_timeout: int = field(default_factory=lambda: int(os.getenv("CLAUDE_TIMEOUT", "300")))

    # --- Pfade ---------------------------------------------------------------
    workspace: Path = field(default_factory=lambda: Path(os.getenv("JARVIS_WORKSPACE", ROOT / "workspace")))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("JARVIS_DATA", ROOT / "data")))

    # --- Persönliches -------------------------------------------------------
    user_name: str = field(default_factory=lambda: os.getenv("JARVIS_USER_NAME", ""))
    timezone: str = field(default_factory=lambda: os.getenv("JARVIS_TIMEZONE", "Europe/Berlin"))
    language: str = field(default_factory=lambda: os.getenv("JARVIS_LANGUAGE", "de"))

    # --- Web / PWA -----------------------------------------------------------
    host: str = field(default_factory=lambda: os.getenv("JARVIS_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("JARVIS_PORT", "8080")))
    token: str = field(default_factory=lambda: os.getenv("JARVIS_TOKEN", ""))

    # --- Telegram ------------------------------------------------------------
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed: list[str] = field(default_factory=lambda: _list("TELEGRAM_ALLOWED_USER_IDS"))
    telegram_voice_reply: bool = field(default_factory=lambda: _bool("TELEGRAM_VOICE_REPLY", True))

    # --- Google (Gmail + Kalender) ------------------------------------------
    google_enabled: bool = field(default_factory=lambda: _bool("GOOGLE_ENABLED", True))
    # Darf Jarvis Mails selbstständig abschicken? Aus = nur Entwürfe.
    allow_send_email: bool = field(default_factory=lambda: _bool("JARVIS_ALLOW_SEND_EMAIL", False))

    # --- Sprache (lokal, kostenlos) -----------------------------------------
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: os.getenv("WHISPER_DEVICE", "auto"))
    piper_voice: str = field(default_factory=lambda: os.getenv("PIPER_VOICE", ""))  # Pfad zur .onnx

    # --- Automatische Routinen ---------------------------------------------
    briefing_time: str = field(default_factory=lambda: os.getenv("JARVIS_BRIEFING_TIME", ""))  # z.B. 07:30
    inbox_check_minutes: int = field(default_factory=lambda: int(os.getenv("JARVIS_INBOX_CHECK_MINUTES", "0")))

    # Optional: Push aufs Handy über ntfy (z.B. https://ntfy.sh/mein-geheimes-topic)
    ntfy_url: str = field(default_factory=lambda: os.getenv("NTFY_URL", ""))
    quiet_hours: str = field(default_factory=lambda: os.getenv("JARVIS_QUIET_HOURS", "22-7"))

    # Zusätzliche erlaubte Claude-Code-Tools (kommagetrennt)
    extra_tools: list[str] = field(default_factory=lambda: _list("JARVIS_EXTRA_TOOLS"))

    @property
    def google_credentials_file(self) -> Path:
        return Path(os.getenv("GOOGLE_CREDENTIALS_FILE", self.data_dir / "google_credentials.json"))

    @property
    def google_token_file(self) -> Path:
        return Path(os.getenv("GOOGLE_TOKEN_FILE", self.data_dir / "google_token.json"))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)


config = Config()
