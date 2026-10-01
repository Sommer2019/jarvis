"""Web-Server: API + installierbare Handy-App (PWA) mit Sprachsteuerung."""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import stt, tts
from .brain import Brain
from .config import Config
from .files import FileStore
from .pcstore import PcStore
from .phone import PhoneStore

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


class LocationIn(BaseModel):
    lat: float
    lon: float
    accuracy: float = 0
    address: str = ""
    time: int | None = None  # Unix-Sekunden


class ChatIn(BaseModel):
    message: str
    voice: bool = False
    conversation: str = "web"
    location: LocationIn | None = None


class TTSIn(BaseModel):
    text: str
    rate: float = 1.0


class PhoneContact(BaseModel):
    name: str
    phones: list[str] = []
    emails: list[str] = []


class PhoneContactsIn(BaseModel):
    contacts: list[PhoneContact]


class PcHello(BaseModel):
    device: str
    system: str = ""
    commands: list[str] = []
    version: str = ""


class ActionDone(BaseModel):
    ok: bool = True
    result: object = None


class PhoneCalendar(BaseModel):
    id: int
    name: str = ""
    account: str = ""
    writable: bool = False
    primary: bool = False


class PhoneEvent(BaseModel):
    event_id: int
    calendar_id: int
    title: str = ""
    start: int  # Unix-Millisekunden
    end: int
    all_day: bool = False
    location: str = ""
    description: str = ""


class PhoneCalendarIn(BaseModel):
    calendars: list[PhoneCalendar]
    events: list[PhoneEvent]


def create_app(cfg: Config, brain: Brain | None = None, whatsapp=None) -> FastAPI:
    brain = brain or Brain(cfg)
    token = resolve_token(cfg)
    phone = PhoneStore(cfg.data_dir)
    pcs = PcStore(cfg.data_dir)
    files = FileStore(cfg.workspace, cfg.data_dir)

    async def long_poll(fetch, wait: int):
        """Wartet bis zu `wait` Sekunden auf neue Aufgaben (spart Akku/Traffic gegenüber Dauerabfragen)."""
        items = fetch()
        end = asyncio.get_running_loop().time() + max(0, min(wait, 30))
        while not items and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(1)
            items = fetch()
        return items
    app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None)

    def from_app(request: Request) -> bool:
        """Anfragen der nativen Android-App tragen den Header X-Jarvis-App."""
        info = request.headers.get("x-jarvis-app")
        if info:
            phone.register_app({"version": info})
        return bool(info)

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
        from . import __version__

        return {"ok": True, "version": __version__, "stt": True, "tts": tts.available(cfg)}

    def channel(request: Request, voice: bool) -> str:
        kind = "android-app" if from_app(request) else "web-app"
        return f"{kind}-{'sprache' if voice else 'text'}"

    def location_context(loc: LocationIn | None) -> str:
        if loc is None:
            return ""
        saved = phone.save_location(loc.model_dump())
        saved["age_minutes"] = 0
        return "[Standort des Nutzers: " + PhoneStore.describe(saved) + " – nur nutzen, wenn relevant]"

    @app.post("/api/chat", dependencies=[Depends(auth)])
    async def chat(body: ChatIn, request: Request):
        if not body.message.strip():
            raise HTTPException(400, "Leere Nachricht")
        reply = await brain.ask(body.message, conv(body.conversation), voice=body.voice,
                                channel=channel(request, body.voice), context=location_context(body.location))
        return {"reply": reply.text, "error": reply.is_error, "actions": phone.pending()}

    # ------------------------------------------------------------- Handy
    @app.post("/api/phone/contacts", dependencies=[Depends(auth)])
    async def phone_contacts(body: PhoneContactsIn, request: Request):
        from_app(request)
        return {"saved": phone.save_contacts([c.model_dump() for c in body.contacts])}

    @app.post("/api/phone/calendar", dependencies=[Depends(auth)])
    async def phone_calendar(body: PhoneCalendarIn, request: Request):
        from_app(request)
        n = phone.save_calendar([c.model_dump() for c in body.calendars],
                                [e.model_dump() for e in body.events])
        return {"saved": n}

    @app.post("/api/phone/location", dependencies=[Depends(auth)])
    async def phone_location(body: LocationIn, request: Request):
        from_app(request)
        return phone.save_location(body.model_dump())

    @app.get("/api/phone/actions", dependencies=[Depends(auth)])
    async def phone_actions(request: Request, wait: int = 0):
        from_app(request)
        return {"actions": await long_poll(phone.pending, wait)}

    @app.post("/api/phone/actions/{action_id}/done", dependencies=[Depends(auth)])
    async def phone_action_done(action_id: str):
        return {"ok": phone.done(action_id)}

    # --------------------------------------------------------------- PC
    @app.post("/api/pc/hello", dependencies=[Depends(auth)])
    async def pc_hello(body: PcHello):
        return pcs.register(body.model_dump())

    @app.get("/api/pc/actions", dependencies=[Depends(auth)])
    async def pc_actions(device: str, wait: int = 0):
        pcs.touch(device)
        return {"actions": await long_poll(lambda: pcs.queue.pending(device), wait)}

    @app.post("/api/pc/actions/{action_id}/done", dependencies=[Depends(auth)])
    async def pc_action_done(action_id: str, body: ActionDone):
        return {"ok": pcs.queue.done(action_id, body.result, body.ok)}

    # ----------------------------------------------------------- Dateien
    @app.post("/api/files", dependencies=[Depends(auth)])
    async def file_upload(file: UploadFile = File(...), source: str = Form("")):
        from urllib.parse import unquote

        try:
            meta = await asyncio.to_thread(files.save, file.file, unquote(file.filename or "datei"), source)
        except ValueError as e:
            raise HTTPException(413, str(e))
        return meta

    @app.get("/api/files", dependencies=[Depends(auth)])
    async def file_list():
        return {"files": files.list()}

    @app.get("/api/files/{file_id}", dependencies=[Depends(auth)])
    async def file_download(file_id: str):
        meta = files.get(file_id)
        if not meta or not files.path(meta).exists():
            raise HTTPException(404, "Datei nicht (mehr) vorhanden")
        return FileResponse(files.path(meta), filename=meta["name"], media_type=meta["mime"])

    # ---------------------------------------------------------- WhatsApp
    @app.get("/webhook/whatsapp")
    async def whatsapp_verify(request: Request):
        q = request.query_params
        answer = whatsapp and whatsapp.verify_webhook(q.get("hub.mode", ""), q.get("hub.verify_token", ""),
                                                      q.get("hub.challenge", ""))
        if not answer:
            raise HTTPException(403, "Verifizierung fehlgeschlagen")
        return PlainTextResponse(answer)

    @app.post("/webhook/whatsapp")
    async def whatsapp_webhook(request: Request, background: BackgroundTasks):
        if not whatsapp:
            raise HTTPException(404)
        from .whatsapp import verify_signature

        body = await request.body()
        if not verify_signature(cfg.whatsapp_app_secret, body, request.headers.get("x-hub-signature-256", "")):
            raise HTTPException(401, "Signatur ungültig")
        # Meta erwartet schnell ein 200 – Verarbeitung im Hintergrund
        background.add_task(whatsapp.handle_payload, await request.json())
        return {"ok": True}

    @app.post("/api/voice", dependencies=[Depends(auth)])
    async def voice(request: Request, audio: UploadFile = File(...), conversation: str = Form("web")):
        data = await audio.read()
        suffix = Path(audio.filename or "a.webm").suffix or ".webm"
        try:
            text = await stt.transcribe(data, cfg, suffix=suffix)
        except stt.STTUnavailable as e:
            raise HTTPException(501, str(e))
        if not text:
            return {"transcript": "", "reply": "Ich habe nichts verstanden.", "error": True}
        reply = await brain.ask(text, conv(conversation), voice=True, channel=channel(request, True))
        return {"transcript": text, "reply": reply.text, "error": reply.is_error, "actions": phone.pending()}

    @app.post("/api/tts", dependencies=[Depends(auth)])
    async def speak(body: TTSIn):
        if not tts.available(cfg):
            raise HTTPException(501, "Piper nicht eingerichtet")
        if not body.text.strip():
            raise HTTPException(400, "Leerer Text")
        return Response(await tts.synthesize(body.text[:3000], cfg, body.rate), media_type="audio/wav")

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
