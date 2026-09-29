"""Web-Server: API + installierbare Handy-App (PWA) mit Sprachsteuerung."""

from __future__ import annotations

import hmac
import logging
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import stt, tts
from .brain import Brain
from .config import Config

log = logging.getLogger("jarvis.server")
STATIC = Path(__file__).resolve().parent / "static"


def resolve_token(cfg: Config) -> str:
    """Nutzt JARVIS_TOKEN oder erzeugt einmalig einen zufälligen Token in data/web_token.txt."""
    if cfg.token:
        return cfg.token
    path = cfg.data_dir / "web_token.txt"
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    path.chmod(0o600)
    log.warning("Neuer Web-Token erzeugt: %s (gespeichert in %s)", token, path)
    return token


class ChatIn(BaseModel):
    message: str
    voice: bool = False
    conversation: str = "web"


class TTSIn(BaseModel):
    text: str


def create_app(cfg: Config, brain: Brain | None = None) -> FastAPI:
    brain = brain or Brain(cfg)
    token = resolve_token(cfg)
    app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None)

    def auth(request: Request) -> None:
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        given = given or request.query_params.get("token", "")
        if not given or not hmac.compare_digest(given, token):
            raise HTTPException(401, "Falscher oder fehlender Token")

    def conv(name: str) -> str:
        # Web-Konversationen getrennt von Telegram & Routinen halten
        return "web:" + (name or "default")[:40]

    @app.get("/api/health")
    async def health():
        return {"ok": True, "stt": True, "tts": tts.available(cfg)}

    @app.post("/api/chat", dependencies=[Depends(auth)])
    async def chat(body: ChatIn):
        if not body.message.strip():
            raise HTTPException(400, "Leere Nachricht")
        reply = await brain.ask(body.message, conv(body.conversation), voice=body.voice,
                                channel="app-sprache" if body.voice else "app-text")
        return {"reply": reply.text, "error": reply.is_error}

    @app.post("/api/voice", dependencies=[Depends(auth)])
    async def voice(audio: UploadFile = File(...), conversation: str = Form("web")):
        data = await audio.read()
        suffix = Path(audio.filename or "a.webm").suffix or ".webm"
        try:
            text = await stt.transcribe(data, cfg, suffix=suffix)
        except stt.STTUnavailable as e:
            raise HTTPException(501, str(e))
        if not text:
            return {"transcript": "", "reply": "Ich habe nichts verstanden.", "error": True}
        reply = await brain.ask(text, conv(conversation), voice=True, channel="app-sprache")
        return {"transcript": text, "reply": reply.text, "error": reply.is_error}

    @app.post("/api/tts", dependencies=[Depends(auth)])
    async def speak(body: TTSIn):
        if not tts.available(cfg):
            raise HTTPException(501, "Piper nicht eingerichtet")
        return Response(await tts.synthesize(body.text, cfg), media_type="audio/wav")

    @app.post("/api/reset", dependencies=[Depends(auth)])
    async def reset(conversation: str = "web"):
        brain.reset(conv(conversation))
        return {"ok": True}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    async def service_worker():
        # Muss von der Wurzel ausgeliefert werden, damit die PWA installierbar ist
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
