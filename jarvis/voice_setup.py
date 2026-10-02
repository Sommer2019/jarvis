"""`jarvis voice-setup`: natürliche deutsche Stimme (Piper) einrichten – kostenlos, läuft lokal.

Lädt Piper und ein Stimmenmodell von Hugging Face (rhasspy/piper-voices) nach
data/models und trägt PIPER_VOICE in .env ein. Danach nutzt die Handy-App die
Stimme (Stimme → „Natürliche Stimme (Jarvis-Server)“), Telegram schickt damit Sprachnachrichten.
"""

from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE"

# name → (Beschreibung, verfügbare Qualitäten, beste zuerst)
VOICES = {
    "thorsten": ("männlich, ruhig und klar – klingt am meisten nach Jarvis", ["high", "medium", "low"]),
    "thorsten_emotional": ("männlich, etwas lebendiger", ["medium"]),
    "kerstin": ("weiblich, freundlich", ["low"]),
    "ramona": ("weiblich, warm", ["low"]),
}


def default_quality(name: str) -> str:
    """Auf dem Handy (ARM) die schnellere Variante, damit Antworten ohne Wartezeit kommen."""
    qualities = VOICES[name][1]
    arm = platform.machine().lower() in ("aarch64", "arm64", "armv7l", "armv8l")
    if arm and "medium" in qualities:
        return "medium"
    return qualities[0]


def model_urls(name: str, quality: str) -> tuple[str, str]:
    stem = f"de_DE-{name}-{quality}"
    url = f"{BASE}/{name}/{quality}/{stem}.onnx"
    return url, url + ".json"


def _download(url: str, target: Path) -> None:
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as res, open(tmp, "wb") as out:  # noqa: S310 – feste HTTPS-URL
        total = int(res.headers.get("content-length") or 0)
        done = 0
        while chunk := res.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r   {target.name}: {done * 100 // total:3d} %", end="", flush=True)
    print()
    tmp.replace(target)


def ensure_piper() -> bool:
    if importlib.util.find_spec("piper"):
        return True
    print("▶ Installiere Piper (Sprachausgabe) …")
    res = subprocess.run([sys.executable, "-m", "pip", "install", "piper-tts>=1.2"])
    if res.returncode != 0:
        print("❌ Piper ließ sich nicht installieren. Manuell: pip install piper-tts")
        return False
    importlib.invalidate_caches()
    return True


def setup(models_dir: Path, name: str = "thorsten", quality: str = "", set_env=None) -> Path | None:
    if name not in VOICES:
        print(f"Unbekannte Stimme '{name}'. Verfügbar: {', '.join(VOICES)}")
        return None
    quality = quality or default_quality(name)
    if quality not in VOICES[name][1]:
        print(f"'{name}' gibt es nur in: {', '.join(VOICES[name][1])}")
        return None
    if not ensure_piper():
        return None
    models_dir.mkdir(parents=True, exist_ok=True)
    onnx_url, json_url = model_urls(name, quality)
    onnx = models_dir / onnx_url.rsplit("/", 1)[1]
    try:
        for url, target in ((json_url, onnx.with_suffix(".onnx.json")), (onnx_url, onnx)):
            if not target.exists():
                print(f"▶ Lade {target.name} …")
                _download(url, target)
    except OSError as e:
        print(f"❌ Download fehlgeschlagen: {e}")
        return None
    try:  # kurzer Probelauf
        from piper import PiperVoice

        PiperVoice.load(str(onnx))
    except Exception as e:  # noqa: BLE001
        print(f"❌ Stimme lässt sich nicht laden: {type(e).__name__}: {e}")
        return None
    if set_env:
        set_env("PIPER_VOICE", str(onnx))
    print(f"✅ Stimme „{name}“ ({quality}) eingerichtet: {onnx}")
    print("   Jarvis neu starten. In der App: ⚙ → Stimme → „Natürliche Stimme (Jarvis-Server)“ wählen.")
    return onnx
