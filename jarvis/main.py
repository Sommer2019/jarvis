"""Kommandozeile: `jarvis serve`, `jarvis chat`, `jarvis google-auth`, `jarvis briefing`, `jarvis doctor`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys

from pathlib import Path

from .config import ROOT, config


async def _serve() -> None:
    import uvicorn

    from .brain import Brain
    from .scheduler import Scheduler
    from .server import create_app

    brain = Brain(config)
    notifiers = []
    wa = None
    if config.whatsapp_token and config.whatsapp_phone_id:
        from .whatsapp import WhatsApp

        wa = WhatsApp(config, brain)
        notifiers.append(wa.notify)
        if not config.whatsapp_app_secret:
            logging.warning("WHATSAPP_APP_SECRET fehlt – Webhook-Signaturen werden nicht geprüft!")
    app = create_app(config, brain, whatsapp=wa)
    bot = None
    if config.telegram_token:
        from .telegram_bot import TelegramBot

        if not config.telegram_allowed:
            logging.warning("TELEGRAM_ALLOWED_USER_IDS leer – der Bot antwortet niemandem. "
                            "Schreib dem Bot /id, um deine ID zu erfahren.")
        bot = TelegramBot(config, brain)
        await bot.start()
        notifiers.append(bot.notify)

    discord_bot = None
    if config.discord_token:
        from .discord_bot import DiscordBot

        if not config.discord_allowed:
            logging.warning("DISCORD_ALLOWED_USER_IDS leer – schreib dem Bot per DM, er nennt dir deine ID.")
        discord_bot = DiscordBot(config, brain)
        await discord_bot.start()
        notifiers.append(discord_bot.notify)

    scheduler = Scheduler(config, brain, notifiers)
    scheduler.start()

    if os.getenv("JARVIS_PC_LOCAL", "").lower() in ("1", "true", "yes", "ja"):
        # Jarvis läuft auf diesem PC → ihn direkt mitsteuern
        import threading

        from .pc import Agent
        from .server import resolve_token

        agent = Agent(f"http://127.0.0.1:{config.port}", resolve_token(config), os.getenv("JARVIS_PC_NAME", ""))
        threading.Thread(target=agent.run_forever, daemon=True, name="pc-agent").start()

    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="info"))
    try:
        await server.serve()
    finally:
        scheduler.stop()
        if bot:
            await bot.stop()
        if discord_bot:
            await discord_bot.stop()


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


def claude_check(token: str | None = None) -> tuple[bool, str]:
    """Testet den Abo-Login mit einer Mini-Anfrage. Liefert (ok, Fehlermeldung)."""
    import json

    from .brain import parse_output, subscription_env

    claude = shutil.which(config.claude_bin)
    if not claude:
        return False, "Claude Code nicht gefunden"
    env = subscription_env()
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    try:
        res = subprocess.run([claude, "-p", "Antworte nur mit OK", "--output-format", "json", "--tools", ""],
                             capture_output=True, text=True, env=env, timeout=180, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "Zeitüberschreitung (Internetverbindung?)"
    reply = parse_output(res.stdout, res.stderr, res.returncode)
    return (not reply.is_error), reply.text


def set_env_value(key: str, value: str) -> Path:
    """Setzt KEY=value in .env (ersetzt eine vorhandene Zeile oder hängt an)."""
    path = ROOT / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)
    os.environ[key] = value
    return path


def _login() -> int:
    """Führt durch `claude setup-token` und speichert den Token in .env."""
    claude = shutil.which(config.claude_bin)
    if not claude:
        print("❌ Claude Code ist nicht installiert.")
        return 1
    ok, _ = claude_check()
    if ok:
        print("✅ Du bist bereits mit deinem Claude-Abo verbunden.")
        if input("Trotzdem neu anmelden? [j/N] ").strip().lower() not in ("j", "ja", "y"):
            return 0
    print("""
So geht's:
  1. Gleich erscheint ein Login-Link. Öffne ihn (antippen bzw. lange drücken → Link öffnen)
     und melde dich mit deinem Claude Pro/Max-Konto an.
  2. Kopiere den Code von der Webseite, füge ihn hier ein und drücke Enter.
  3. Den Token, den Claude danach anzeigt, liest Jarvis automatisch mit – nichts abtippen.
""")
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")}
    candidates: list[str] = []
    try:
        from .login import extract_tokens, run_captured

        candidates = extract_tokens(run_captured([claude, "setup-token"], env))
    except Exception as e:  # noqa: BLE001 – z.B. kein Terminal: klassisch weiter
        print(f"(Automatisches Mitlesen nicht möglich: {e})")
        subprocess.run([claude, "setup-token"], env=env)
    print()

    for token in candidates[:3]:
        print(f"Token erkannt ({token[:18]}…{token[-4:]}, {len(token)} Zeichen) – prüfe …")
        ok, err = claude_check(token)
        if ok:
            return _save_token(token)
        print(f"   funktioniert nicht: {err[:200]}")

    print("Kein gültiger Token automatisch erkannt – bitte von Hand einfügen.")
    for _ in range(3):
        token = "".join(input("Token hier einfügen (sk-ant-oat…): ").split())  # Zeilenumbrüche/Leerzeichen raus
        if not token:
            print("Abgebrochen.")
            return 1
        if not token.startswith("sk-ant-"):
            print("⚠️  Das sieht nicht wie ein Claude-Token aus (sollte mit sk-ant- beginnen). Nochmal:")
            continue
        print(f"Prüfe Token ({len(token)} Zeichen) …")
        ok, err = claude_check(token)
        if ok:
            return _save_token(token)
        print(f"❌ Token funktioniert nicht: {err[:300]}")
    return 1


def _save_token(token: str) -> int:
    path = set_env_value("CLAUDE_CODE_OAUTH_TOKEN", token)
    print(f"✅ Verbunden! Token gespeichert in {path}.")
    print("   Falls Jarvis schon läuft: neu starten (Handy: ~/jarvis-stop.sh; ~/jarvis-start.sh).")
    return 0


MAIL_PRESETS = [
    # Name, IMAP, SMTP, SMTP-Port, Hinweis
    ("Gmail", "imap.gmail.com", "smtp.gmail.com", 465,
     "Gmail braucht ein App-Passwort (nicht dein normales Passwort):\n"
     "  1. https://myaccount.google.com/signinoptions/twosv → Bestätigung in zwei Schritten einschalten\n"
     "  2. https://myaccount.google.com/apppasswords → Name „Jarvis“ → Erstellen\n"
     "  3. Das 16-stellige Passwort hier einfügen."),
    ("GMX", "imap.gmx.net", "mail.gmx.net", 587,
     "Bei GMX vorher im Webmail unter Einstellungen → POP3/IMAP den IMAP-Zugriff erlauben."),
    ("Web.de", "imap.web.de", "smtp.web.de", 587,
     "Bei Web.de vorher im Webmail unter Einstellungen → POP3/IMAP den IMAP-Zugriff erlauben."),
    ("Outlook / Hotmail / Live", "outlook.office365.com", "smtp.office365.com", 587,
     "Bei Microsoft-Konten mit Zwei-Faktor-Anmeldung ein App-Kennwort unter account.microsoft.com → Sicherheit erstellen."),
    ("iCloud", "imap.mail.me.com", "smtp.mail.me.com", 587,
     "iCloud braucht ein app-spezifisches Passwort: appleid.apple.com → Anmeldung und Sicherheit."),
    ("T-Online", "secureimap.t-online.de", "securesmtp.t-online.de", 465,
     "T-Online braucht ein eigenes E-Mail-Passwort (Kundencenter → E-Mail-Einstellungen)."),
    ("Yahoo", "imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465,
     "Yahoo braucht ein App-Passwort (Kontosicherheit → App-Passwort generieren)."),
    ("Posteo", "posteo.de", "posteo.de", 465, ""),
    ("mailbox.org", "imap.mailbox.org", "smtp.mailbox.org", 465, ""),
]


def _mail_setup() -> int:
    """Assistent: Mailkonto einrichten, Login prüfen, speichern."""
    import getpass
    import json

    from . import mcp_mail

    print("E-Mail-Konto für Jarvis einrichten\n")
    for i, (name, *_rest) in enumerate(MAIL_PRESETS, 1):
        print(f"  {i}. {name}")
    print(f"  {len(MAIL_PRESETS) + 1}. Anderer Anbieter")
    try:
        choice = int(input("\nNummer: ").strip())
    except ValueError:
        print("Abgebrochen.")
        return 1
    if 1 <= choice <= len(MAIL_PRESETS):
        name, imap_host, smtp_host, smtp_port, hint = MAIL_PRESETS[choice - 1]
        imap_port = 993
        imap_ssl = True
    else:
        name = input("Anbieter-Name: ").strip() or "mail"
        imap_host = input("IMAP-Server (z.B. imap.example.de): ").strip()
        imap_port = int(input("IMAP-Port [993]: ").strip() or 993)
        ssl_default = "j" if imap_port == 993 else "n"
        imap_ssl = (input(f"Direkt verschlüsselt (SSL, Port 993)? Sonst STARTTLS. [{ssl_default}] ").strip().lower()
                    or ssl_default) in ("j", "ja", "y")
        smtp_host = input("SMTP-Server (z.B. smtp.example.de): ").strip()
        smtp_port = int(input("SMTP-Port [465]: ").strip() or 465)
        hint = ""
    if hint:
        print("\n" + hint)
    address = input("\nE-Mail-Adresse: ").strip()
    password = getpass.getpass("Passwort / App-Passwort (Eingabe unsichtbar): ").strip()
    if name == "Gmail":
        password = password.replace(" ", "")  # App-Passwörter werden mit Leerzeichen angezeigt
    display = input("Absendername (optional, z.B. Robin Wagner): ").strip()

    acc = mcp_mail.Account(name=name.split()[0].lower().replace(".", ""), imap_host=imap_host, imap_port=imap_port,
                           imap_ssl=imap_ssl, username=address, password=password,
                           smtp_host=smtp_host, smtp_port=smtp_port, smtp_username=address,
                           smtp_password=password, from_address=address, from_name=display)
    print("\nPrüfe Anmeldung …")
    try:
        with mcp_mail.imap(acc) as conn:
            count = len(mcp_mail.folders(conn))
        print(f"✅ Anmeldung erfolgreich ({count} Ordner gefunden).")
    except Exception as e:  # noqa: BLE001
        print(f"❌ Anmeldung fehlgeschlagen: {e}")
        print("   Adresse/Passwort prüfen – bei Gmail, iCloud, Yahoo & Co. ein App-Passwort verwenden.")
        return 1

    existing = mcp_mail.accounts()
    if not os.getenv("IMAP_HOST") or os.getenv("IMAP_USERNAME", "").lower() == address.lower():
        for key, val in {"IMAP_HOST": imap_host, "IMAP_PORT": str(imap_port), "IMAP_SSL": str(acc.imap_ssl).lower(),
                         "IMAP_USERNAME": address, "IMAP_PASSWORD": password, "SMTP_HOST": smtp_host,
                         "SMTP_PORT": str(smtp_port), "MAIL_FROM": address, "MAIL_FROM_NAME": display,
                         "MAIL_ACCOUNT_NAME": acc.name}.items():
            set_env_value(key, val)
        where = ".env"
    else:  # weiteres Konto
        path = config.data_dir / "mail_accounts.json"
        items = json.loads(path.read_text()) if path.exists() else []
        items = [a for a in items if a.get("username", "").lower() != address.lower()]
        items.append({"name": acc.name if acc.name not in existing else f"{acc.name}{len(items) + 2}",
                      "imap_host": imap_host, "imap_port": imap_port, "imap_ssl": acc.imap_ssl,
                      "username": address, "password": password, "smtp_host": smtp_host,
                      "smtp_port": smtp_port, "from_name": display})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, indent=2, ensure_ascii=False))
        path.chmod(0o600)
        where = str(path)
    if os.getenv("JARVIS_ALLOW_SEND_EMAIL", "false").lower() not in ("1", "true", "yes", "ja"):
        if input("Darf Jarvis Mails nach deiner Bestätigung selbst senden? (sonst nur Entwürfe) [j/N] ").strip().lower() in ("j", "ja", "y"):
            set_env_value("JARVIS_ALLOW_SEND_EMAIL", "true")
    print(f"\n✅ Gespeichert in {where}. Jarvis neu starten (Handy: ~/jarvis-stop.sh; ~/jarvis-start.sh).")
    print("   Weiteres Konto? Einfach `jarvis mail-setup` nochmal ausführen.")
    return 0


def _doctor() -> int:
    ok = True

    def check(name: str, good: bool, hint: str = "", optional: bool = False) -> None:
        nonlocal ok
        if not optional:
            ok &= good
        mark = "✅" if good else ("⚪" if optional else "❌")
        print(f"{mark} {name}" + (f"  → {hint}" if not good and hint else ""))

    print("Pflicht:")
    claude = shutil.which(config.claude_bin)
    check("Claude Code installiert", bool(claude), "npm install -g @anthropic-ai/claude-code")
    if os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY ist gesetzt – Jarvis ignoriert ihn bewusst (sonst API-Kosten).")
    if claude:
        good, err = claude_check()
        check("Claude-Abo-Login funktioniert", good, "`jarvis login` ausführen")
        if not good:
            print(f"   Meldung von Claude: {err[:300]}")

    print("\nDienste (nur was du eingerichtet hast):")
    if config.google_enabled:
        check("Google-OAuth-Client vorhanden", config.google_credentials_file.exists(),
              f"JSON nach {config.google_credentials_file} legen (siehe README) – oder GOOGLE_ENABLED=false")
        check("Google-Login erledigt", config.google_token_file.exists(), "jarvis google-auth")
    from . import mcp_mail
    if mcp_mail.configured():
        check("Mailkonten (IMAP)", _mail_ok(), "IMAP-Server/Benutzer/App-Passwort prüfen")
    if config.caldav_url or config.carddav_url:
        check("CalDAV/CardDAV", _dav_ok(), "URL/Benutzer/App-Passwort prüfen")
    if config.whatsapp_token:
        check("WhatsApp konfiguriert", bool(config.whatsapp_phone_id and config.whatsapp_allowed
                                            and config.whatsapp_verify_token),
              "WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN, WHATSAPP_ALLOWED_NUMBERS setzen")
    if os.getenv("GITHUB_TOKEN"):
        check("GitHub", _github_ok(), "GITHUB_TOKEN prüfen (Fine-grained Token, Zugriff auf Repos)")
    if config.discord_token:
        check("Discord konfiguriert", bool(config.discord_allowed), "DISCORD_ALLOWED_USER_IDS setzen")
    if config.telegram_token:
        check("Telegram konfiguriert", bool(config.telegram_allowed), "TELEGRAM_ALLOWED_USER_IDS setzen")

    print("\nOptional (auf dem Handy nicht nötig – Android erkennt Sprache selbst):")
    if not config.telegram_token:
        check("Telegram", False, "TELEGRAM_BOT_TOKEN setzen, falls gewünscht", optional=True)
    try:
        import faster_whisper  # noqa: F401
        check("Spracherkennung auf dem Server (Whisper)", True, optional=True)
    except ImportError:
        check("Spracherkennung auf dem Server (Whisper)", False,
              "nur für Sprachnachrichten in Telegram/Discord/WhatsApp: pip install -e '.[voice]'", optional=True)
    from . import tts
    check("Sprachausgabe auf dem Server (Piper)", tts.available(config),
          "nur für Sprach-Antworten in Telegram/Discord: PIPER_VOICE setzen", optional=True)
    check("ffmpeg", bool(shutil.which("ffmpeg")), "nur mit Piper nötig: apt install ffmpeg", optional=True)
    print("\n" + ("Alles bereit. ✅" if ok else "Bitte die ❌-Punkte beheben."))
    return 0 if ok else 1


def _github_ok() -> bool:
    try:
        from . import mcp_github

        print(f"   angemeldet als {mcp_github.me()}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"   {type(e).__name__}: {e}")
        return False


def _mail_ok() -> bool:
    from . import mcp_mail

    ok = True
    for acc in mcp_mail.accounts().values():
        try:
            with mcp_mail.imap(acc):
                print(f"   ✓ {acc.name} ({acc.username})")
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ {acc.name}: {type(e).__name__}: {e}")
            ok = False
    return ok


def _dav_ok() -> bool:
    try:
        from . import mcp_dav

        if config.caldav_url:
            mcp_dav.caldav_list_calendars()
        if config.carddav_url:
            mcp_dav.carddav().addressbooks()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"   {type(e).__name__}: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Dein persönlicher Assistent")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Web-App, API, Telegram-Bot und Routinen starten")
    sub.add_parser("chat", help="Im Terminal mit Jarvis chatten")
    sub.add_parser("google-auth", help="Einmalig mit Google (Gmail/Kalender) verbinden")
    sub.add_parser("briefing", help="Morgen-Briefing jetzt erstellen und ausgeben")
    sub.add_parser("doctor", help="Einrichtung prüfen")
    sub.add_parser("login", help="Mit deinem Claude-Abo verbinden (claude setup-token → .env)")
    sub.add_parser("mail-setup", help="E-Mail-Konto einrichten (Gmail, GMX, Web.de, Outlook, …)")
    pa = sub.add_parser("pc-agent", help="Diesen PC/Laptop für Jarvis steuerbar machen")
    pa.add_argument("--server", default="", help="Jarvis-Adresse (Standard: dieser Rechner)")
    pa.add_argument("--token", default="", help="Jarvis-Token (Standard: aus .env)")
    pa.add_argument("--name", default="", help="Name dieses PCs")
    pa.add_argument("--install", action="store_true", help="Autostart einrichten")
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
    elif args.cmd == "pc-agent":
        from . import pc
        from .server import resolve_token

        argv = ["--server", args.server or f"http://127.0.0.1:{config.port}",
                "--token", args.token or resolve_token(config)]
        if args.name:
            argv += ["--name", args.name]
        if args.install:
            argv.append("--install")
        pc.main(argv)
    elif args.cmd == "mail-setup":
        sys.exit(_mail_setup())
    elif args.cmd == "login":
        sys.exit(_login())
    elif args.cmd == "doctor":
        sys.exit(_doctor())
    elif args.cmd == "token":
        from .server import resolve_token

        print(resolve_token(config))
    else:
        parser.print_help()
