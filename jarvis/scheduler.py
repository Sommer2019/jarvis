"""Routinen: Morgen-Briefing und regelmäßiger Posteingangs-Check mit Push aufs Handy.

Achtung: jede Routine verbraucht Nachrichten deines Abo-Kontingents. Intervalle
daher lieber großzügig wählen (z.B. 60 Minuten).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from .brain import Brain
from .config import Config

log = logging.getLogger("jarvis.scheduler")
Notifier = Callable[[str], Awaitable[None]]

NOTHING = "NICHTS"

BRIEFING_PROMPT = """Erstelle mein Morgen-Briefing:
1. Heutige und morgige Termine (mit Uhrzeit, Ort, ggf. Vorbereitung).
2. Wichtige ungelesene Mails der letzten 24h aus allen Mailkonten – kurz, mit Handlungsbedarf.
3. Offene Punkte aus meinen Notizen (todo.md), falls vorhanden.
Halte es kompakt und gut auf dem Handy lesbar."""

INBOX_PROMPT = f"""Prüfe alle meine Mailkonten auf neue ungelesene Mails seit {{since}}
(Gmail: 'in:inbox is:unread after:{{epoch}}'; IMAP: mail_search mit unread=True, since_days=1, pro Konto).
Melde dich NUR bei wirklich Wichtigem (persönliche Mails von echten Menschen, Fristen, Rechnungen, Termine, Sicherheitswarnungen).
Newsletter, Werbung und Benachrichtigungen ignorierst du.
Wenn es nichts Wichtiges gibt, antworte exakt mit: {NOTHING}
Sonst: kurze Zusammenfassung pro Mail + Vorschlag, was ich tun sollte. Nichts senden, nichts löschen."""


def parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        h, m = value.strip().split(":")
        return int(h), int(m)
    except ValueError:
        return None


def in_quiet_hours(now: datetime, spec: str) -> bool:
    try:
        start, end = (int(x) for x in spec.split("-"))
    except ValueError:
        return False
    h = now.hour
    return (start <= h or h < end) if start > end else (start <= h < end)


def next_daily(now: datetime, hh: int, mm: int) -> datetime:
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


async def ntfy_notifier(url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(url, content=text.encode(), headers={"Title": "Jarvis"})


class Scheduler:
    def __init__(self, cfg: Config, brain: Brain, notifiers: list[Notifier]):
        self.cfg = cfg
        self.brain = brain
        self.notifiers = list(notifiers)
        if cfg.ntfy_url:
            self.notifiers.append(lambda t: ntfy_notifier(cfg.ntfy_url, t))
        self.tz = ZoneInfo(cfg.timezone)
        self._tasks: list[asyncio.Task] = []
        self._last_inbox = datetime.now(self.tz)

    async def notify(self, text: str) -> None:
        for n in self.notifiers:
            try:
                await n(text)
            except Exception:
                log.exception("Benachrichtigung fehlgeschlagen")

    async def run_routine(self, name: str, prompt: str) -> str:
        conv = f"routine:{name}"
        self.brain.reset(conv)  # jede Routine frisch, damit der Kontext klein bleibt
        reply = await self.brain.ask(prompt, conv, channel=f"routine-{name}")
        return reply.text

    async def briefing(self) -> None:
        text = await self.run_routine("briefing", BRIEFING_PROMPT)
        await self.notify("☀️ Guten Morgen!\n\n" + text)

    async def inbox_check(self) -> None:
        since = self._last_inbox
        self._last_inbox = datetime.now(self.tz)
        prompt = INBOX_PROMPT.format(since=since.strftime("%d.%m. %H:%M"), epoch=int(since.timestamp()))
        text = await self.run_routine("inbox", prompt)
        if text.strip().strip(".").upper() != NOTHING:
            await self.notify("📬 " + text)

    async def _daily_loop(self, hhmm: tuple[int, int]) -> None:
        while True:
            now = datetime.now(self.tz)
            await asyncio.sleep((next_daily(now, *hhmm) - now).total_seconds())
            try:
                await self.briefing()
            except Exception:
                log.exception("Briefing fehlgeschlagen")
            await asyncio.sleep(61)

    async def _interval_loop(self, minutes: int) -> None:
        while True:
            await asyncio.sleep(minutes * 60)
            if in_quiet_hours(datetime.now(self.tz), self.cfg.quiet_hours):
                continue
            try:
                await self.inbox_check()
            except Exception:
                log.exception("Inbox-Check fehlgeschlagen")

    def start(self) -> None:
        if not self.notifiers:
            log.info("Keine Push-Kanäle (Telegram/ntfy) – Routinen deaktiviert")
            return
        hhmm = parse_hhmm(self.cfg.briefing_time) if self.cfg.briefing_time else None
        if hhmm:
            self._tasks.append(asyncio.create_task(self._daily_loop(hhmm)))
            log.info("Morgen-Briefing täglich um %02d:%02d", *hhmm)
        if self.cfg.inbox_check_minutes > 0:
            self._tasks.append(asyncio.create_task(self._interval_loop(self.cfg.inbox_check_minutes)))
            log.info("Posteingang-Check alle %d Minuten", self.cfg.inbox_check_minutes)

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
