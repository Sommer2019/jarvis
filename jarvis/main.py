"""Kommandozeile: `jarvis serve`, `jarvis chat`, `jarvis google-auth`, `jarvis briefing`, `jarvis doctor`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys

from .config import config


async def _serve() -> None:
    import uvicorn

    from .brain import Brain
    from .scheduler import Scheduler
    from .server import create_app

    brain = Brain(config)
    app = create_app(config, brain)
    notifiers = []
    bot = None
    if config.telegram_token:
        from .telegram_bot import TelegramBot

        if not config.telegram_allowed:
            logging.warning("TELEGRAM_ALLOWED_USER_IDS leer – der Bot antwortet niemandem. "
                            "Schreib dem Bot /id, um deine ID zu erfahren.")
        bot = TelegramBot(config, brain)
        await bot.start()
        notifiers.append(bot.notify)

    scheduler = Scheduler(config, brain, notifiers)
    scheduler.start()

    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="info"))
    try:
        await server.serve()
    finally:
        scheduler.stop()
        if bot:
            await bot.stop()


async def _chat() -> None:
    from .brain import Brain

    brain = Brain(config)
    print("Jarvis (Terminal). /neu = neues Gespräch, Strg+D = Ende")
    while True:
        try:
            msg = input("\nDu: ").strip()
        except EOFError:
            break
        if not msg:
            continue
        if msg == "/neu":
            brain.reset("cli")
            print("Neues Gespräch.")
            continue
        reply = await brain.ask(msg, "cli", channel="terminal")
        print(f"\nJarvis: {reply.text}")


async def _briefing() -> None:
    from .brain import Brain
    from .scheduler import BRIEFING_PROMPT, Scheduler

    brain = Brain(config)
    sched = Scheduler(config, brain, [])
    print(await sched.run_routine("briefing", BRIEFING_PROMPT))


def _doctor() -> int:
    ok = True

    def check(name: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"{'✅' if good else '❌'} {name}" + (f"  → {hint}" if not good and hint else ""))

    claude = shutil.which(config.claude_bin)
    check("Claude Code installiert", bool(claude), "npm install -g @anthropic-ai/claude-code")
    if os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY ist gesetzt – Jarvis ignoriert ihn bewusst (sonst API-Kosten).")
    if claude:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        res = subprocess.run([claude, "-p", "Antworte nur mit OK", "--output-format", "json",
                              "--tools", ""], capture_output=True, text=True, env=env, timeout=120)
        good = res.returncode == 0 and '"is_error":false' in res.stdout.replace(" ", "")
        check("Claude-Abo-Login funktioniert", good,
              "`claude setup-token` ausführen und CLAUDE_CODE_OAUTH_TOKEN in .env setzen "
              "(oder einmal `claude` starten und /login)")
    if config.google_enabled:
        check("Google-OAuth-Client vorhanden", config.google_credentials_file.exists(),
              f"JSON nach {config.google_credentials_file} legen (siehe README)")
        check("Google-Login erledigt", config.google_token_file.exists(), "jarvis google-auth")
    check("Telegram konfiguriert", bool(config.telegram_token and config.telegram_allowed),
          "TELEGRAM_BOT_TOKEN und TELEGRAM_ALLOWED_USER_IDS setzen (optional)")
    try:
        import faster_whisper  # noqa: F401
        check("Spracherkennung (faster-whisper)", True)
    except ImportError:
        check("Spracherkennung (faster-whisper)", False, "pip install -e '.[voice]'")
    from . import tts
    check("Sprachausgabe (Piper, optional)", tts.available(config), "PIPER_VOICE setzen (siehe README)")
    check("ffmpeg (für Telegram-Sprachantworten)", bool(shutil.which("ffmpeg")), "apt install ffmpeg")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Dein persönlicher Assistent")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Web-App, API, Telegram-Bot und Routinen starten")
    sub.add_parser("chat", help="Im Terminal mit Jarvis chatten")
    sub.add_parser("google-auth", help="Einmalig mit Google (Gmail/Kalender) verbinden")
    sub.add_parser("briefing", help="Morgen-Briefing jetzt erstellen und ausgeben")
    sub.add_parser("doctor", help="Einrichtung prüfen")
    sub.add_parser("token", help="Web-Token für die Handy-App anzeigen")
    args = parser.parse_args()

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.ensure_dirs()

    if args.cmd == "serve":
        asyncio.run(_serve())
    elif args.cmd == "chat":
        asyncio.run(_chat())
    elif args.cmd == "google-auth":
        from .google_auth import run_auth_flow

        run_auth_flow(config.google_credentials_file, config.google_token_file)
    elif args.cmd == "briefing":
        asyncio.run(_briefing())
    elif args.cmd == "doctor":
        sys.exit(_doctor())
    elif args.cmd == "token":
        from .server import resolve_token

        print(resolve_token(config))
    else:
        parser.print_help()
