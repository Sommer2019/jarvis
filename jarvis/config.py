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

    # --- Discord -------------------------------------------------------------
    discord_token: str = field(default_factory=lambda: os.getenv("DISCORD_BOT_TOKEN", ""))
    discord_allowed: list[str] = field(default_factory=lambda: _list("DISCORD_ALLOWED_USER_IDS"))
    # Kanäle, in denen Jarvis ohne @Erwähnung antwortet (optional)
    discord_channels: list[str] = field(default_factory=lambda: _list("DISCORD_CHANNEL_IDS"))
    discord_voice_reply: bool = field(default_factory=lambda: _bool("DISCORD_VOICE_REPLY", False))

    # --- Google (Gmail + Kalender) ------------------------------------------
    google_enabled: bool = field(default_factory=lambda: _bool("GOOGLE_ENABLED", True))
    # Darf Jarvis Mails selbstständig abschicken? Aus = nur Entwürfe.
    allow_send_email: bool = field(default_factory=lambda: _bool("JARVIS_ALLOW_SEND_EMAIL", False))

    # --- CalDAV / CardDAV (Nextcloud, iCloud, mailbox.org, Posteo, …) ------
    caldav_url: str = field(default_factory=lambda: os.getenv("CALDAV_URL", ""))
    carddav_url: str = field(default_factory=lambda: os.getenv("CARDDAV_URL", ""))

    # --- Handy-App (Kontakte & Aktionen) ------------------------------------
    # auto = aktiv, sobald sich die Android-App einmal verbunden hat
    phone: str = field(default_factory=lambda: os.getenv("JARVIS_PHONE", "auto"))

    # --- WhatsApp (offizielle Cloud API von Meta) ---------------------------
    whatsapp_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_TOKEN", ""))
    whatsapp_phone_id: str = field(default_factory=lambda: os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""))
    whatsapp_verify_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_VERIFY_TOKEN", ""))
    whatsapp_app_secret: str = field(default_factory=lambda: os.getenv("WHATSAPP_APP_SECRET", ""))
    whatsapp_allowed: list[str] = field(default_factory=lambda: _list("WHATSAPP_ALLOWED_NUMBERS"))
    whatsapp_api_version: str = field(default_factory=lambda: os.getenv("WHATSAPP_API_VERSION", "v23.0"))
    whatsapp_voice_reply: bool = field(default_factory=lambda: _bool("WHATSAPP_VOICE_REPLY", False))

    # --- Sprache (lokal, kostenlos) -----------------------------------------
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: os.getenv("WHISPER_DEVICE", "auto"))
    piper_voice: str = field(default_factory=lambda: os.getenv("PIPER_VOICE", ""))  # Pfad zur .onnx

    # --- Jarvis telefoniert selbst (Zweit-SIM im Handy, Audio am PC) -------
    # Audiogeräte am PC: Name (Teil reicht) oder Nummer aus `jarvis call-setup`
    call_audio_in: str = field(default_factory=lambda: os.getenv("CALL_AUDIO_IN", ""))    # hört das Gegenüber
    call_audio_out: str = field(default_factory=lambda: os.getenv("CALL_AUDIO_OUT", ""))  # spricht ins Gespräch
    call_max_minutes: int = field(default_factory=lambda: int(os.getenv("CALL_MAX_MINUTES", "6")))
    call_model: str = field(default_factory=lambda: os.getenv("JARVIS_CALL_MODEL", ""))  # z.B. haiku (schneller)

    @property
    def calls_enabled(self) -> bool:
        return bool(self.call_audio_in and self.call_audio_out)

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
