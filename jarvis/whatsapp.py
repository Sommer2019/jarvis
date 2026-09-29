"""WhatsApp über die offizielle WhatsApp Cloud API (Meta).

Du schreibst oder sprichst Jarvis per WhatsApp an, Jarvis antwortet. Antworten
innerhalb von 24 Stunden nach deiner letzten Nachricht ("Service-Fenster") sind
kostenlos. Deshalb schickt Jarvis Push-Nachrichten (Briefing usw.) nur,
solange dieses Fenster offen ist.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path

import httpx

from . import stt, tts
from .brain import Brain
from .config import Config

log = logging.getLogger("jarvis.whatsapp")
MAX_LEN = 4000
WINDOW = 24 * 3600 - 300  # 24h minus Sicherheitspuffer


def normalize(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    if not secret:
        return True  # ohne App-Secret keine Prüfung (nicht empfohlen)
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def extract_messages(payload: dict) -> list[dict]:
    """Webhook-Payload → [{from, id, type, text|media_id}]"""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []) or []:
                item = {"from": m.get("from", ""), "id": m.get("id", ""), "type": m.get("type")}
                if m.get("type") == "text":
                    item["text"] = m["text"].get("body", "")
                elif m.get("type") in ("audio", "voice"):
                    item["media_id"] = (m.get("audio") or m.get("voice") or {}).get("id")
                elif m.get("type") == "button":
                    item["type"], item["text"] = "text", m["button"].get("text", "")
                elif m.get("type") == "interactive":
                    reply = m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}
                    item["type"], item["text"] = "text", reply.get("title", "")
                out.append(item)
    return out


class WhatsApp:
    def __init__(self, cfg: Config, brain: Brain):
        self.cfg = cfg
        self.brain = brain
        self.base = f"https://graph.facebook.com/{cfg.whatsapp_api_version}"
        self.http = httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {cfg.whatsapp_token}"})
        self.allowed = {normalize(n) for n in cfg.whatsapp_allowed}
        self.window_file = Path(cfg.data_dir) / "whatsapp_window.json"

    # ------------------------------------------------------------ 24h-Fenster
    def _windows(self) -> dict[str, float]:
        try:
            return json.loads(self.window_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _touch(self, number: str) -> None:
        w = self._windows()
        w[number] = time.time()
        self.window_file.write_text(json.dumps(w))

    def window_open(self, number: str) -> bool:
        return time.time() - self._windows().get(number, 0) < WINDOW

    # ------------------------------------------------------------- Graph API
    async def _post(self, path: str, **kw) -> dict:
        r = await self.http.post(f"{self.base}/{path}", **kw)
        if r.status_code >= 400:
            log.error("WhatsApp-API %s: %s", r.status_code, r.text[:300])
        r.raise_for_status()
        return r.json()

    async def send_text(self, to: str, text: str) -> None:
        for i in range(0, len(text), MAX_LEN):
            await self._post(f"{self.cfg.whatsapp_phone_id}/messages", json={
                "messaging_product": "whatsapp", "to": to, "type": "text",
                "text": {"body": text[i:i + MAX_LEN], "preview_url": False},
            })

    async def send_voice(self, to: str, ogg: bytes) -> None:
        media = await self._post(f"{self.cfg.whatsapp_phone_id}/media",
                                 data={"messaging_product": "whatsapp", "type": "audio/ogg"},
                                 files={"file": ("antwort.ogg", ogg, "audio/ogg")})
        await self._post(f"{self.cfg.whatsapp_phone_id}/messages", json={
            "messaging_product": "whatsapp", "to": to, "type": "audio", "audio": {"id": media["id"]}})

    async def mark_read(self, message_id: str) -> None:
        try:
            await self._post(f"{self.cfg.whatsapp_phone_id}/messages", json={
                "messaging_product": "whatsapp", "status": "read", "message_id": message_id})
        except httpx.HTTPError:
            pass

    async def download_media(self, media_id: str) -> bytes:
        meta = (await self.http.get(f"{self.base}/{media_id}")).json()
        r = await self.http.get(meta["url"])
        r.raise_for_status()
        return r.content

    # -------------------------------------------------------------- Webhook
    def verify_webhook(self, mode: str, token: str, challenge: str) -> str | None:
        if mode == "subscribe" and self.cfg.whatsapp_verify_token and hmac.compare_digest(
                token or "", self.cfg.whatsapp_verify_token):
            return challenge
        return None

    async def handle(self, msg: dict) -> None:
        sender = normalize(msg["from"])
        if sender not in self.allowed:
            log.warning("WhatsApp von nicht erlaubter Nummer %s ignoriert", sender)
            return
        self._touch(sender)
        await self.mark_read(msg["id"])
        voice = False
        if msg["type"] == "text":
            text = msg.get("text", "")
        elif msg.get("media_id"):
            voice = True
            try:
                text = await stt.transcribe(await self.download_media(msg["media_id"]), self.cfg, suffix=".ogg")
            except stt.STTUnavailable as e:
                await self.send_text(sender, str(e))
                return
            if not text:
                await self.send_text(sender, "Ich habe leider nichts verstanden.")
                return
            await self.send_text(sender, f"🎙️ „{text}“")
        else:
            await self.send_text(sender, "Bitte schick mir Text oder eine Sprachnachricht.")
            return

        if text.strip().lower() in ("/neu", "neu", "neues gespräch"):
            self.brain.reset(f"whatsapp:{sender}")
            await self.send_text(sender, "Neues Gespräch gestartet.")
            return

        reply = await self.brain.ask(text, f"whatsapp:{sender}", voice=voice,
                                     channel="whatsapp-sprache" if voice else "whatsapp-text")
        await self.send_text(sender, reply.text or "(keine Antwort)")
        if voice and self.cfg.whatsapp_voice_reply and tts.available(self.cfg) and not reply.is_error:
            ogg = tts.wav_to_ogg_opus(await tts.synthesize(reply.text, self.cfg))
            if ogg:
                await self.send_voice(sender, ogg)

    async def handle_payload(self, payload: dict) -> None:
        for msg in extract_messages(payload):
            try:
                await self.handle(msg)
            except Exception:
                log.exception("WhatsApp-Nachricht fehlgeschlagen")

    async def notify(self, text: str) -> None:
        """Push nur innerhalb des kostenlosen 24h-Fensters."""
        for number in self.allowed:
            if self.window_open(number):
                await self.send_text(number, text)
            else:
                log.info("WhatsApp-Push an %s übersprungen (24h-Fenster geschlossen)", number)
