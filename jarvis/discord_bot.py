"""Discord-Bot: Jarvis per Direktnachricht oder @Erwähnung – Text und Sprachnachrichten.

Einrichtung: https://discord.com/developers/applications → Bot anlegen → Token kopieren,
"Message Content Intent" einschalten, Bot mit dem OAuth2-Link auf deinen Server einladen
(Scopes: bot; Rechte: Nachrichten lesen/senden, Dateien anhängen).
"""

from __future__ import annotations

import asyncio
import logging

import discord

from . import stt, tts
from .brain import Brain
from .config import Config

log = logging.getLogger("jarvis.discord")
MAX_LEN = 1990
RESET_WORDS = {"!neu", "/neu", "neu"}


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


def is_audio(att: discord.Attachment) -> bool:
    ctype = (att.content_type or "").lower()
    return ctype.startswith("audio/") or att.filename.lower().endswith((".ogg", ".mp3", ".m4a", ".wav", ".webm"))


class DiscordBot:
    def __init__(self, cfg: Config, brain: Brain):
        self.cfg = cfg
        self.brain = brain
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        self.allowed = {int(u) for u in cfg.discord_allowed if u.isdigit()}
        self.channels = {int(c) for c in cfg.discord_channels if c.isdigit()}
        self._task: asyncio.Task | None = None
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    # ------------------------------------------------------------- Regeln
    def should_answer(self, message: discord.Message) -> bool:
        if message.author.bot or message.author.id not in self.allowed:
            return False
        if isinstance(message.channel, discord.DMChannel):
            return True
        if message.channel.id in self.channels:
            return True
        return self.client.user is not None and self.client.user in message.mentions

    def clean_text(self, message: discord.Message) -> str:
        text = message.content or ""
        if self.client.user:
            text = text.replace(f"<@{self.client.user.id}>", "").replace(f"<@!{self.client.user.id}>", "")
        return text.strip()

    @staticmethod
    def conv(message: discord.Message) -> str:
        return f"discord:{message.channel.id}"

    # ----------------------------------------------------------- Handler
    async def on_ready(self) -> None:
        log.info("Discord-Bot angemeldet als %s", self.client.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.author.id not in self.allowed:
            if isinstance(message.channel, discord.DMChannel):
                await message.channel.send(f"Kein Zugriff. Deine Discord-ID ist {message.author.id} – "
                                           "trage sie in DISCORD_ALLOWED_USER_IDS ein.")
            return
        if not self.should_answer(message):
            return
        try:
            await self.handle(message)
        except Exception:
            log.exception("Discord-Nachricht fehlgeschlagen")
            await message.channel.send("Da ist leider etwas schiefgegangen.")

    async def handle(self, message: discord.Message) -> None:
        text = self.clean_text(message)
        voice = False
        audio = next((a for a in message.attachments if is_audio(a)), None)
        if audio and not text:
            voice = True
            try:
                async with message.channel.typing():
                    text = await stt.transcribe(await audio.read(), self.cfg, suffix=".ogg")
            except stt.STTUnavailable as e:
                await message.channel.send(str(e))
                return
            if not text:
                await message.channel.send("Ich habe leider nichts verstanden.")
                return
            await message.channel.send(f"🎙️ „{text}“")
        if not text:
            return
        if text.lower() in RESET_WORDS:
            self.brain.reset(self.conv(message))
            await message.channel.send("Neues Gespräch gestartet.")
            return

        async with message.channel.typing():
            reply = await self.brain.ask(text, self.conv(message), voice=voice,
                                         channel="discord-sprache" if voice else "discord-text")
        for part in split_message(reply.text or "(keine Antwort)"):
            await message.channel.send(part)
        if voice and self.cfg.discord_voice_reply and tts.available(self.cfg) and not reply.is_error:
            ogg = tts.wav_to_ogg_opus(await tts.synthesize(reply.text, self.cfg))
            if ogg:
                import io

                await message.channel.send(file=discord.File(io.BytesIO(ogg), filename="jarvis.ogg"))

    # ----------------------------------------------------------- Betrieb
    async def start(self) -> None:
        self._task = asyncio.create_task(self.client.start(self.cfg.discord_token))

    async def stop(self) -> None:
        await self.client.close()
        if self._task:
            self._task.cancel()

    async def notify(self, text: str) -> None:
        """Push per Direktnachricht an alle erlaubten Nutzer."""
        await self.client.wait_until_ready()
        for uid in self.allowed:
            user = self.client.get_user(uid) or await self.client.fetch_user(uid)
            for part in split_message(text):
                await user.send(part)
