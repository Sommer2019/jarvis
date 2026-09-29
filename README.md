# Jarvis – dein persönlicher Assistent

Jarvis kümmert sich um **E-Mails, Kalender, Aufgaben und Recherche**. Du steuerst ihn
**per Sprache oder Text vom Handy**, über eine installierbare Android-App (PWA) oder über
Telegram-Sprachnachrichten.

**Kosten: nur dein normales Claude-Abo (Pro oder Max).** Jarvis nutzt keine API-Tokens.
Das „Gehirn“ ist die offizielle Claude-Code-CLI im Headless-Modus (`claude -p`), und die
meldet sich mit deinem Abo-Login an. Spracherkennung (Whisper) und Sprachausgabe (Piper
bzw. die Stimme deines Handys) laufen lokal und kostenlos. Gmail, Google Kalender und
Telegram kosten ebenfalls nichts.

```
 Handy ──(PWA: Sprache/Text)──┐
 Handy ──(Telegram-Sprachnachricht)──┐
                              ▼      ▼
                     ┌────────────────────────┐
                     │  jarvis serve          │  (dein PC / Raspberry Pi / Server)
                     │  • Web-API + PWA       │
                     │  • Telegram-Bot        │
                     │  • Whisper (STT) lokal │
                     │  • Piper (TTS) lokal   │
                     │  • Routinen (Briefing) │
                     └──────────┬─────────────┘
                                ▼
                  claude -p  (Claude-Abo via OAuth)
                                ▼
            MCP-Server „google“: Gmail + Kalender (eigener Code)
            + Web-Suche + Gedächtnis-Dateien (workspace/)
```

## Was Jarvis kann

| Bereich | Beispiele |
|---|---|
| **E-Mail** | „Was ist heute Wichtiges reingekommen?“ · „Antworte Max, dass ich Donnerstag kann“ (erstellt einen Entwurf und fragt vor dem Senden) · „Archivier alle Newsletter von gestern“ |
| **Kalender** | „Was steht morgen an?“ · „Trag Freitag 14 Uhr Zahnarzt ein“ · „Wann habe ich nächste Woche 2 Stunden frei?“ |
| **Erinnerungen / To-dos** | „Erinnere mich morgen um 9 an die Steuer“ (legt einen Kalendertermin mit Alarm an und notiert es in `todo.md`) |
| **Gedächtnis** | „Merk dir, dass meine Schwester Lena heißt“ (landet in `workspace/memory.md`) |
| **Recherche** | „Wie wird das Wetter am Wochenende in Hamburg?“ · „Such mir ein Rezept mit Kürbis“ |
| **Routinen** | Morgen-Briefing jeden Tag per Push · optionaler Posteingangs-Wächter, der sich nur bei Wichtigem meldet |

Sicherheitsregeln (siehe `workspace/CLAUDE.md`): Mails werden nur nach deiner ausdrücklichen
Bestätigung gesendet, und im Standard ist Senden technisch ganz abgeschaltet
(`JARVIS_ALLOW_SEND_EMAIL=false`, dann gibt es nur Entwürfe). Anweisungen, die in E-Mails
stehen, führt Jarvis nicht aus. Die Shell (Bash) ist für Jarvis gesperrt.

---

## Einrichtung

Du brauchst einen Rechner, der dauerhaft läuft (Raspberry Pi 4/5, alter Laptop, NAS mit Docker
oder ein kleiner VPS). Voraussetzungen: Python ≥ 3.10, Node.js ≥ 18 und ein Claude-Pro/Max-Abo.

### 1. Installieren

```bash
git clone https://github.com/sommer2019/jarvis.git && cd jarvis
npm install -g @anthropic-ai/claude-code      # die Claude-Code-CLI
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[voice]"                     # ohne Sprache: pip install -e .
cp .env.example .env
```

### 2. Mit deinem Claude-Abo verbinden (ohne API-Key)

```bash
claude setup-token
```
Melde dich mit deinem Pro/Max-Konto an und trag den angezeigten Token in `.env` als
`CLAUDE_CODE_OAUTH_TOKEN=` ein. Alternativ startest du auf dem Server einmal `claude` und
meldest dich mit `/login` an.

> ⚠️ Setze **keinen** `ANTHROPIC_API_KEY`. Claude Code würde sonst über die API abrechnen.
> Jarvis entfernt diese Variable zur Sicherheit ohnehin aus der Umgebung.

### 3. Gmail und Kalender verbinden (einmalig, kostenlos)

1. <https://console.cloud.google.com/> öffnen und ein neues Projekt anlegen (z.B. „Jarvis“).
2. Unter **APIs & Dienste → Bibliothek** die **Gmail API** und die **Google Calendar API** aktivieren.
3. Unter **OAuth-Zustimmungsbildschirm** den Typ „Extern“ wählen und dich selbst als **Testnutzer** eintragen.
   Danach unter „Veröffentlichungsstatus“ auf **„In Produktion“** stellen, sonst läuft der Login
   nach 7 Tagen ab. Eine Google-Prüfung ist für die reine Eigennutzung nicht nötig; du bestätigst
   beim Login einfach die Warnung „App nicht überprüft“.
4. Unter **Anmeldedaten → OAuth-Client-ID** den Typ **Desktop-App** wählen, die JSON-Datei herunterladen
   und als `data/google_credentials.json` speichern.
5. Den Login starten:
   ```bash
   jarvis google-auth
   ```
   Öffne den angezeigten Link im Browser. Läuft Jarvis auf einem Server ohne Bildschirm,
   verbinde dich vorher mit `ssh -L 8765:localhost:8765 dein-server`. Du kannst `jarvis google-auth`
   auch am PC ausführen und danach `data/google_token.json` auf den Server kopieren.

### 4. Prüfen und starten

```bash
jarvis doctor     # prüft Claude-Login, Google, Sprache, Telegram
jarvis chat       # erster Test im Terminal
jarvis serve      # startet Web-App, Telegram-Bot und Routinen
```

---

## Vom Handy aus nutzen

### Variante A: Jarvis-App auf Android (PWA, empfohlen)

Mikrofon-Zugriff im Browser funktioniert nur über **HTTPS**. Der einfachste und sicherste Weg
ist **Tailscale** (kostenlos). Damit ist Jarvis nur für deine eigenen Geräte erreichbar und nicht
offen im Internet.

1. Tailscale auf dem Jarvis-Rechner und auf dem Handy installieren und dich mit demselben Konto anmelden.
2. Auf dem Jarvis-Rechner HTTPS einschalten:
   ```bash
   tailscale serve --bg 8080
   ```
   Jarvis ist dann unter `https://<rechnername>.<dein-tailnet>.ts.net` erreichbar.
3. Auf dem Handy in **Chrome** `https://…ts.net/?token=DEIN_TOKEN` öffnen (den Token zeigt `jarvis token` an).
4. Im Chrome-Menü **„App installieren“** bzw. **„Zum Startbildschirm hinzufügen“** wählen.

Danach hast du ein Jarvis-Icon auf dem Homescreen:
- **Mikrofon antippen und sprechen.** Die Spracherkennung macht Android selbst, das kostet nichts.
  Die Antwort wird vorgelesen.
- **„Gespräch: an“** schaltet den Freisprech-Modus ein: Nach jeder Antwort hört Jarvis automatisch
  wieder zu, fast wie ein Telefonat.
- **Icon lange drücken → „Sprechen“:** startet Jarvis direkt im Zuhör-Modus. Diese Verknüpfung kannst
  du auch auf den Homescreen ziehen.
- Mit **„Hey Google, öffne Jarvis“** startest du die App freihändig.

### Variante B: Telegram (Sprachnachrichten, überall)

1. In Telegram **@BotFather** anschreiben, `/newbot` ausführen und den Token als `TELEGRAM_BOT_TOKEN` eintragen.
2. `jarvis serve` starten, deinem Bot `/id` schreiben und die angezeigte ID als `TELEGRAM_ALLOWED_USER_IDS`
   eintragen. Danach Jarvis neu starten.
3. Ab jetzt kannst du dem Bot Text oder **Sprachnachrichten** schicken. Mit `/neu` beginnt ein neues Gespräch.
   Über Telegram kommen auch Morgen-Briefing und Mail-Hinweise als Push.

Damit Jarvis per **Sprachnachricht antwortet**, installierst du ffmpeg und eine Piper-Stimme:
```bash
sudo apt install ffmpeg
mkdir -p models && cd models
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json
```
Danach in `.env` eintragen: `PIPER_VOICE=models/de_DE-thorsten-high.onnx`

### Variante C: Tasker, Shortcuts & Co.

Jarvis hat eine einfache HTTP-API. Die kannst du z.B. mit der App „HTTP Shortcuts“, mit Tasker
oder mit einem iOS-Kurzbefehl aufrufen:
```bash
curl -X POST https://…ts.net/api/chat \
  -H "Authorization: Bearer DEIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "Trag morgen 9 Uhr Meeting mit Anna ein"}'
```
Sprachaufnahmen kannst du an `/api/voice` schicken (Multipart-Feld `audio`). Jarvis antwortet
mit `{transcript, reply}`.

### Und ein echter Telefonanruf?

Eine eigene Telefonnummer, die Jarvis anrufen kann, geht nur über einen Telefonie-Anbieter wie
Twilio oder sipgate. Das kostet Geld pro Nummer und Minute, und das wolltest du vermeiden. Deshalb
ist es bewusst nicht eingebaut. Der **Gesprächsmodus der App** (Variante A) fühlt sich praktisch
wie ein Anruf an und kostet nichts.

---

## Routinen

In `.env`:
- `JARVIS_BRIEFING_TIME=07:30`: jeden Morgen Termine, wichtige Mails und To-dos per Telegram bzw. ntfy.
- `JARVIS_INBOX_CHECK_MINUTES=60`: prüft stündlich den Posteingang und meldet sich nur bei
  Wichtigem. In den Ruhezeiten (`JARVIS_QUIET_HOURS=22-7`) prüft Jarvis nicht.
- `NTFY_URL=https://ntfy.sh/ein-langes-geheimes-topic`: Push über die kostenlose ntfy-App,
  falls du kein Telegram nutzen willst.

`jarvis briefing` erstellt das Briefing sofort, zum Testen.

## Abo-Kontingent: gut zu wissen

Jede Anfrage an Jarvis zählt zum **Nutzungslimit deines Abos**, genauso wie ein Chat auf
claude.ai. Es entstehen keine Zusatzkosten, aber mit Pro kannst du das Limit bei sehr häufigen
Routinen schneller erreichen. Tipps dafür:
- Den Mail-Check nicht öfter als etwa alle 60 Minuten laufen lassen.
- Mit `CLAUDE_MODEL=sonnet` sparst du Kontingent. Für komplexe Aufgaben passt der Standard.
- Mit `/neu` (Telegram) bzw. **＋** (App) startest du ein frisches Gespräch, wenn ein altes sehr lang wird.

Jarvis ist für die **persönliche Nutzung mit deinem eigenen Abo** gedacht. Stell ihn nicht anderen
Personen als Dienst zur Verfügung, dafür wäre ein API-Key vorgesehen.

## Betrieb

**Docker:**
```bash
docker compose up -d --build
docker compose exec jarvis claude setup-token   # falls kein Token in .env steht
docker compose exec jarvis jarvis doctor
```

**systemd** (ohne Docker): Vorlage in `deploy/jarvis.service`.

## Anpassen

- **Persönlichkeit und Regeln:** `workspace/CLAUDE.md`
- **Was Jarvis über dich weiß:** `workspace/memory.md` (pflegt er selbst, du kannst es aber auch bearbeiten)
- **Aufgabenliste:** `workspace/todo.md`
- **Neue Fähigkeiten:** weitere Tools in `jarvis/mcp_google.py` ergänzen oder zusätzliche
  MCP-Server in `jarvis/brain.py` → `_write_mcp_config()` eintragen (z.B. Notion oder Home Assistant).

## Projektstruktur

```
jarvis/
  brain.py          ruft `claude -p` auf (Abo-Login, Sessions pro Kanal, Tool-Freigaben)
  mcp_google.py     MCP-Server: Gmail + Google Kalender
  server.py         FastAPI: /api/chat, /api/voice, /api/tts + PWA
  telegram_bot.py   Telegram: Text & Sprachnachrichten
  scheduler.py      Morgen-Briefing, Posteingangs-Wächter
  stt.py / tts.py   Whisper / Piper (lokal)
  static/           die Handy-App
workspace/CLAUDE.md Persönlichkeit & Regeln von Jarvis
tests/              pytest (mit Fake-Claude, verbraucht kein Kontingent)
```

Tests ausführen: `pip install -e ".[dev]" && pytest`
