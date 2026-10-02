"""Spracherkennung lokal mit faster-whisper – kostenlos, keine Cloud."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from functools import lru_cache

from .config import Config

log = logging.getLogger("jarvis.stt")


class STTUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _model(name: str, device: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover
        raise STTUnavailable("faster-whisper nicht installiert: pip install -e '.[voice]'") from e
    compute = "int8" if device in ("cpu", "auto") else "float16"
    log.info("Lade Whisper-Modell %s (%s) …", name, device)
    return WhisperModel(name, device=device, compute_type=compute)


def _transcribe_file(path: str, cfg: Config) -> str:
    model = _model(cfg.whisper_model, cfg.whisper_device)
    segments, _ = model.transcribe(path, language=cfg.language or None, vad_filter=True, beam_size=5)
    return " ".join(s.text.strip() for s in segments).strip()


async def transcribe(audio: bytes, cfg: Config, suffix: str = ".ogg") -> str:
    """Audio (ogg/opus, webm, wav, mp3, m4a …) → Text."""
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(audio)
        f.flush()
        return await asyncio.to_thread(_transcribe_file, f.name, cfg)


# Whisper erfindet bei Rauschen/Freizeichen gern solche Sätze – verwerfen
HALLUCINATIONS = ("untertitel", "copyright", "vielen dank fürs zuschauen", "swr", "zdf", "amara.org")


def transcribe_pcm(samples, cfg: Config) -> str:
    """16-kHz-Mono-Audio (float32-Array) → Text. Für Telefonate (läuft im Thread)."""
    model = _model(cfg.whisper_model, cfg.whisper_device)
    segments, _ = model.transcribe(samples, language=cfg.language or None, vad_filter=True,
                                   beam_size=1, condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segments).strip()
    if not text or any(h in text.lower() for h in HALLUCINATIONS) and len(text) < 80:
        return ""
    return text
