"""Sprachausgabe lokal mit Piper – kostenlos, keine Cloud.

In der Web-App wird standardmäßig die Sprachausgabe des Handys genutzt; Piper
braucht man vor allem für Sprachnachrichten-Antworten in Telegram.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess
import wave
from functools import lru_cache

from .config import Config

log = logging.getLogger("jarvis.tts")


def available(cfg: Config) -> bool:
    if not cfg.piper_voice:
        return False
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _voice(path: str):
    from piper import PiperVoice

    return PiperVoice.load(path)


def _synthesize_wav(text: str, cfg: Config) -> bytes:
    voice = _voice(cfg.piper_voice)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        if hasattr(voice, "synthesize_wav"):  # piper-tts >= 1.3
            voice.synthesize_wav(text, wav)
        else:  # ältere Versionen
            voice.synthesize(text, wav)
    return buf.getvalue()


def wav_to_ogg_opus(wav: bytes) -> bytes | None:
    """Telegram-Sprachnachrichten brauchen OGG/Opus → ffmpeg."""
    if not shutil.which("ffmpeg"):
        return None
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=wav, capture_output=True,
    )
    return proc.stdout if proc.returncode == 0 else None


async def synthesize(text: str, cfg: Config) -> bytes:
    return await asyncio.to_thread(_synthesize_wav, text, cfg)
