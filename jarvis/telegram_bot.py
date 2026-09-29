"""Telegram-Bot: Text- und Sprachnachrichten vom Handy – kostenlos.

Sprachnachricht aufnehmen → Whisper (lokal) → Claude → Antwort als Text
(+ optional als Sprachnachricht via Piper).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import stt, tts
from .brain import Brain
from .config import Config

log = logging.getLogger("jarvis.telegram")
MAX_LEN = 4000


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


class TelegramBot:
    def __init__(self, cfg: Config, brain: Brain):
        self.cfg = cfg
        self.brain = brain
        self.app = Application.builder().token(cfg.telegram_token).build()
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("neu", self.cmd_reset))
        self.app.add_handler(CommandHandler("id", self.cmd_id))
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

    # ---------------------------------------------------------------- helpers
    def allowed(self, update: Update) -> bool:
        uid = str(update.effective_user.id) if update.effective_user else ""
        return uid in self.cfg.telegram_allowed

    @staticmethod
    def conv(update: Update) -> str:
        return f"telegram:{update.effective_chat.id}"

    async def _reply(self, update: Update, text: str, as_voice: bool) -> None:
        for part in split_message(text or "(keine Antwort)"):
            await update.effective_message.reply_text(part)
        if as_voice and self.cfg.telegram_voice_reply and tts.available(self.cfg) and text:
            try:
                ogg = tts.wav_to_ogg_opus(await tts.synthesize(text, self.cfg))
                if ogg:
                    await update.effective_message.reply_voice(ogg)
            except Exception:  # Sprachausgabe ist nice-to-have
                log.exception("TTS fehlgeschlagen")

    async def _handle(self, update: Update, text: str, voice: bool) -> None:
        await update.effective_chat.send_action(ChatAction.TYPING)
        reply = await self.brain.ask(text, self.conv(update), voice=voice,
                                     channel="telegram-sprache" if voice else "telegram-text")
        await self._reply(update, reply.text, as_voice=voice and not reply.is_error)

    # --------------------------------------------------------------- handlers
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            await update.effective_message.reply_text(
                f"Kein Zugriff. Deine Telegram-ID ist {update.effective_user.id} – "
                "trage sie in TELEGRAM_ALLOWED_USER_IDS ein.")
            return
        await update.effective_message.reply_text(
            "Jarvis ist bereit. Schick mir Text oder eine Sprachnachricht. /neu startet ein neues Gespräch.")

    async def cmd_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(f"Deine Telegram-ID: {update.effective_user.id}")

    async def cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        self.brain.reset(self.conv(update))
        await update.effective_message.reply_text("Neues Gespräch gestartet.")

    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            log.warning("Abgelehnt: Telegram-User %s", update.effective_user.id)
            return
        await self._handle(update, update.effective_message.text, voice=False)

    async def on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        media = update.effective_message.voice or update.effective_message.audio
        file = await media.get_file()
        audio = bytes(await file.download_as_bytearray())
        await update.effective_chat.send_action(ChatAction.TYPING)
        try:
            text = await stt.transcribe(audio, self.cfg, suffix=".ogg")
        except stt.STTUnavailable as e:
            await update.effective_message.reply_text(str(e))
            return
        if not text:
            await update.effective_message.reply_text("Ich habe leider nichts verstanden.")
            return
        await update.effective_message.reply_text(f"🎙️ „{text}“")
        await self._handle(update, text, voice=True)

    # ---------------------------------------------------------------- runtime
    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram-Bot läuft")

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    async def notify(self, text: str) -> None:
        """Push-Nachricht an alle erlaubten Nutzer (für Briefings/Hinweise)."""
        for uid in self.cfg.telegram_allowed:
            for part in split_message(text):
                await self.app.bot.send_message(chat_id=int(uid), text=part)
