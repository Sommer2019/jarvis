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
