# Jarvis – dein persönlicher Assistent

Jarvis kümmert sich um **E-Mails, Kalender, Kontakte, Aufgaben und Recherche**. Du steuerst ihn
**per Sprache oder Text vom Handy**: über die eigene **Android-App** (APK aus GitHub Actions),
die Web-App, **WhatsApp**, **Telegram** oder **Discord**. In der App kann Jarvis außerdem aufs
Handy zugreifen: Kontakte, **Standort**, Anrufe, SMS, WhatsApp-Nachrichten, Navigation, Wecker und
Timer. Dazu kommt **GitHub**: Benachrichtigungen, PRs, Reviews, CI-Status und Issues.

**Läuft wahlweise komplett auf dem Handy** (Termux, ohne PC/Server, siehe
[Jarvis komplett auf dem Handy](#jarvis-komplett-auf-dem-handy)) oder auf einem Rechner zu Hause.

**Kosten: nur dein normales Claude-Abo (Pro oder Max).** Jarvis nutzt keine API-Tokens.
Das „Gehirn“ ist die offizielle Claude-Code-CLI im Headless-Modus (`claude -p`), und die
meldet sich mit deinem Abo-Login an. Spracherkennung (Whisper) und Sprachausgabe (Piper
bzw. die Stimme deines Handys) laufen lokal und kostenlos. Gmail, Google Kalender und
Telegram kosten ebenfalls nichts.

```
 Android-App ─(Sprache/Text, Kontakte, Aktionen)─┐
 WhatsApp ─(Sprachnachricht/Text)────────────────┤
 Telegram ─(Sprachnachricht/Text)────────────────┤
 Discord  ─(DM/@Erwähnung, Sprachnachricht)──────┤
                                                 ▼
                     ┌──────────────────────────────┐
                     │  jarvis serve                │  (dein PC / Raspberry Pi / Server)
                     │  • Web-API + Web-App         │
                     │  • Telegram-Bot, WhatsApp    │
                     │  • Whisper (STT) lokal       │
                     │  • Piper (TTS) lokal         │
                     │  • Routinen (Briefing)       │
                     └──────────────┬───────────────┘
                                    ▼
                      claude -p  (Claude-Abo via OAuth)
                                    ▼
   MCP-Server (eigener Code):  google = Gmail + Google Kalender
                               mail   = E-Mail per IMAP/SMTP (jeder Anbieter)
                               github = Benachrichtigungen, Issues, PRs, CI
                               dav    = CalDAV-Kalender + CardDAV-Kontakte
                               phone  = Handy-Kontakte, Standort, Aktionen (über die App)
   + Web-Suche + Gedächtnis-Dateien (workspace/)
```

## Was Jarvis kann

| Bereich | Beispiele |
|---|---|
| **E-Mail** (Gmail + jeder IMAP-Anbieter) | „Was ist heute Wichtiges reingekommen?“ · „Antworte Max, dass ich Donnerstag kann“ (erstellt einen Entwurf und fragt vor dem Senden) · „Archivier alle Newsletter von gestern“ |
| **Kalender** (Google und/oder CalDAV) | „Was steht morgen an?“ · „Trag Freitag 14 Uhr Zahnarzt ein“ · „Wann habe ich nächste Woche 2 Stunden frei?“ |
| **Kontakte** (CardDAV + Handy) | „Wie ist die Nummer von Lena?“ · „Wer hat nächste Woche Geburtstag?“ · „Speicher Max' neue Mail max@firma.de“ |
| **Standort** (in der App) | „Wo ist die nächste Apotheke?“ · „Wie wird das Wetter hier?“ · „Wie lange brauche ich von hier nach Hause?“ |
| **GitHub** | „Was ist auf GitHub los?“ · „Ist der Build von jarvis grün?“ · „Welche Reviews warten auf mich?“ · „Leg ein Issue an: Login-Button ist kaputt“ |
| **Laptop/PC** | „Mach Musik an“ · „Lauter“ · „Öffne Spotify“ · „Sperr meinen Laptop“ · „Öffne meinen Downloads-Ordner“ · „Wie voll ist der Akku vom Laptop?“ · „Kopier mir den Entwurf in die Zwischenablage“ |
| **Jarvis meldet sich** | Nachrichten aufs Handy, bei Dringendem **ruft Jarvis an**: Das Handy klingelt, du nimmst ab, Jarvis sagt, was los ist, und hört auf deine Antwort |
| **Handy** (in der App) | „Ruf Mama an“ · „Schreib Max per WhatsApp, dass ich 10 Minuten später komme“ · „Navigier mich zur Arbeit“ · „Wecker auf 6:30“ · „Timer 12 Minuten“ |
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
jarvis login
```
Das startet `claude setup-token`: Du meldest dich mit deinem Pro/Max-Konto an und fügst den
angezeigten Token ein. Jarvis prüft ihn und speichert ihn in `.env` (`CLAUDE_CODE_OAUTH_TOKEN`). Alternativ startest du auf dem Server einmal `claude` und
meldest dich mit `/login` an.

> ⚠️ Setze **keinen** `ANTHROPIC_API_KEY`. Claude Code würde sonst über die API abrechnen.
> Jarvis entfernt diese Variable zur Sicherheit ohnehin aus der Umgebung.

### 3. Gmail und Google Kalender verbinden (einmalig, kostenlos; optional)

Nutzt du kein Google, setz `GOOGLE_ENABLED=false` und nimm CalDAV (Schritt 3b).

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

### 3a. E-Mail bei anderen Anbietern (IMAP/SMTP)

**Am einfachsten mit dem Assistenten:** `jarvis mail-setup` (auf dem Handy im Ubuntu-Shell,
`~/jarvis-shell.sh`). Er kennt Gmail, GMX, Web.de, Outlook, iCloud, T-Online, Yahoo, Posteo und
mailbox.org, erklärt das nötige App-Passwort, prüft die Anmeldung sofort und speichert alles. Für
weitere Konten führst du ihn einfach nochmal aus.

Von Hand geht es so:

Trag in `.env` die Werte deines Anbieters ein. Nutze wenn möglich ein **App-Passwort**:

| Anbieter | `IMAP_HOST` | `SMTP_HOST` : `SMTP_PORT` | Hinweis |
|---|---|---|---|
| GMX | `imap.gmx.net` | `mail.gmx.net` : 587 | IMAP in den GMX-Einstellungen erlauben |
| Web.de | `imap.web.de` | `smtp.web.de` : 587 | IMAP in den Web.de-Einstellungen erlauben |
| T-Online | `secureimap.t-online.de` | `securesmtp.t-online.de` : 465 | E-Mail-Passwort im Kundencenter setzen |
| Outlook/Hotmail | `outlook.office365.com` | `smtp.office365.com` : 587 | App-Kennwort nötig (2FA an) |
| iCloud | `imap.mail.me.com` | `smtp.mail.me.com` : 587 | App-spezifisches Passwort |
| Yahoo | `imap.mail.yahoo.com` | `smtp.mail.yahoo.com` : 465 | App-Passwort |
| Posteo | `posteo.de` | `posteo.de` : 465 | |
| mailbox.org | `imap.mailbox.org` | `smtp.mailbox.org` : 465 | |
| Gmail per IMAP | `imap.gmail.com` | `smtp.gmail.com` : 465 | App-Passwort (Alternative zu Schritt 3) |

**Mehrere Konten:** Leg zusätzlich `data/mail_accounts.json` an:
```json
[
  {"name": "gmx", "imap_host": "imap.gmx.net", "username": "ich@gmx.de", "password": "…",
   "smtp_host": "mail.gmx.net", "smtp_port": 587, "from_name": "Robin"},
  {"name": "arbeit", "imap_host": "outlook.office365.com", "username": "robin@firma.de", "password": "…",
   "smtp_host": "smtp.office365.com", "smtp_port": 587}
]
```
Jarvis prüft dann alle Konten („Was ist neu?“) und antwortet jeweils vom richtigen Konto. Entwürfe
landen im Entwürfe-Ordner des Kontos, du siehst sie also in jeder Mail-App. Senden ist wie bei
Gmail nur mit `JARVIS_ALLOW_SEND_EMAIL=true` möglich.

### 3b. Kalender und Kontakte per CalDAV/CardDAV (optional)

Das funktioniert mit Nextcloud, iCloud, mailbox.org, Posteo, Fastmail, Synology, Baïkal, Radicale usw.
Trag in `.env` die Zugangsdaten ein. Meist reicht die Basis-URL, Kalender und Adressbücher findet
Jarvis dann selbst:

| Anbieter | `CALDAV_URL` | `CARDDAV_URL` |
|---|---|---|
| Nextcloud | `https://cloud.example.de/remote.php/dav` | gleich |
| iCloud | `https://caldav.icloud.com` | `https://contacts.icloud.com` |
| mailbox.org | `https://dav.mailbox.org/caldav/` | `https://dav.mailbox.org/carddav/` |
| Posteo | `https://posteo.de:8443/` | `https://posteo.de:8443/` |

Nutze ein **App-Passwort**, nicht dein Hauptpasswort. Bei iCloud geht das unter appleid.apple.com →
„App-spezifische Passwörter“. Google und CalDAV kannst du auch parallel nutzen, dann prüft Jarvis
beide Kalender.

### 3c. GitHub (optional)

1. Unter <https://github.com/settings/personal-access-tokens> einen **Fine-grained token** anlegen.
2. Unter „Repository access“ die gewünschten Repos oder „All repositories“ wählen.
3. Rechte vergeben: *Contents*, *Metadata*, *Pull requests*, *Issues*, *Actions* und *Commit statuses*
   jeweils auf „Read“. Soll Jarvis Issues anlegen und kommentieren, *Issues*/*Pull requests* auf
   „Read and write“. Für die Benachrichtigungen brauchst du zusätzlich einen **klassischen** Token
   mit Scope `notifications`, denn Fine-grained Tokens können keine Benachrichtigungen lesen.
4. In `.env` eintragen: `GITHUB_TOKEN=…`. Mit `GITHUB_READONLY=true` kann Jarvis nur lesen.

### 4. Prüfen und starten

```bash
jarvis doctor     # prüft Claude-Login, Google, Sprache, Telegram
jarvis chat       # erster Test im Terminal
jarvis serve      # startet Web-App, Telegram-Bot und Routinen
```

---

## Jarvis komplett auf dem Handy

Kein PC und kein Server nötig: Jarvis und Claude Code laufen direkt auf deinem Android-Handy,
und die Jarvis-App startet und steuert sie.

**Warum steckt das nicht komplett in der APK?** Ohne API-Kosten, also nur über dein Abo, darf
ausschließlich die offizielle **Claude-Code-CLI** mit Claude sprechen. Eine App, die Claude direkt
mit deinem Abo-Login aufruft, verstößt gegen die Nutzungsbedingungen von Anthropic und wird
gesperrt. Die CLI ist aber ein Linux-Programm, deshalb läuft sie in **Termux** mit einer kleinen
Ubuntu-Umgebung. Die APK ist dein Frontend und startet alles automatisch.

**Einrichtung (einmalig, ca. 15 Minuten, ca. 1,5 GB Speicher):**
1. **Termux** aus **F-Droid** installieren (<https://f-droid.org/packages/com.termux/>). Die
   Play-Store-Version ist veraltet. Optional dazu **Termux:API** (Token in die Zwischenablage) und
   **Termux:Boot** (Autostart nach Neustart), beide ebenfalls aus F-Droid.
2. In Termux:
   ```bash
   pkg install -y git && git clone https://github.com/Sommer2019/jarvis.git && bash jarvis/termux/install.sh
   ```
   Das Skript richtet die Ubuntu-Umgebung, Claude Code und Jarvis ein. Zwischendurch meldest du dich
   einmal mit deinem Claude-Abo an: den Link antippen, einloggen und den Token einfügen.
   Ist das Repo privat, verwende `https://<github-token>@github.com/…` als Adresse. Liegt die aktuelle
   Version nicht auf `master`, setz vorher `export JARVIS_BRANCH=<branch>`.
3. **Jarvis-App** (APK) öffnen, „Jarvis läuft auf diesem Handy“ ankreuzen und den Token einfügen
   (er liegt in der Zwischenablage oder steht am Ende der Installation).
4. In den Android-Einstellungen der Jarvis-App die Berechtigung **„Befehle in Termux ausführen“**
   erlauben. Dann startet die App Jarvis selbst, wenn er nicht läuft.
5. Android → Apps → **Termux → Akku → „Nicht einschränken“**. Sonst beendet Android Jarvis im
   Hintergrund, und Routinen wie das Morgen-Briefing laufen nicht.

**Claude-Login fehlgeschlagen?** (`jarvis doctor` zeigt ❌ bei „Claude-Abo-Login“): In Termux
`~/jarvis-login.sh` ausführen. Das zeigt den Login-Link, du fügst den Token ein, Jarvis prüft ihn sofort
und startet neu. Tipp: Den Token (`sk-ant-oat…`) lange drücken → „Kopieren“ und vollständig einfügen.

**Einstellungen ändern** (Mail, Kalender, Telegram …): In Termux `~/jarvis-shell.sh` ausführen,
dann `nano .env`, danach `~/jarvis-stop.sh && ~/jarvis-start.sh`. **Updates:** `~/jarvis-update.sh`.

Gut zu wissen:
- Die Spracherkennung macht Android selbst; Whisper ist auf dem Handy nicht nötig.
- Telegram funktioniert auch vom Handy aus, auf dem Jarvis läuft. Das ist praktisch für
  Push-Nachrichten wie das Briefing.
- Für WhatsApp braucht Meta eine öffentlich erreichbare Adresse. Das geht auf dem Handy nur mit
  Tailscale Funnel und ist dort eher unpraktisch; dafür ist ein Rechner zu Hause besser.
- Der Akkuverbrauch ist gering, solange du Jarvis nicht benutzt. Häufige Routinen wie der
  Mail-Check alle 15 Minuten kosten aber spürbar Akku.

---

## Laptop/PC steuern

Auf jedem Laptop, den Jarvis steuern soll, läuft ein kleiner **PC-Agent**. Er braucht nur Python 3
und sonst nichts. Er holt sich Aufgaben von Jarvis und führt sie aus: Programme, Webseiten und
Dateien öffnen, Musik (Play/Pause/Weiter), Lautstärke, Bildschirm sperren, Standby/Herunterfahren
(nur nach Bestätigung), Benachrichtigung anzeigen, Text vorlesen, Zwischenablage und Status (Akku).

**Läuft Jarvis auf diesem PC selbst?** Dann setz in `.env` den Wert `JARVIS_PC_LOCAL=true`, fertig.

**Anderer Laptop (Windows, macOS, Linux):**
1. Python 3 installieren (Windows: python.org, bei der Installation „Add to PATH“ anhaken).
2. Die Datei `pc.py` herunterladen:
   <https://raw.githubusercontent.com/Sommer2019/jarvis/master/jarvis/pc.py>
3. Testen:
   ```bash
   python pc.py --server http://<jarvis-adresse>:8080 --token DEIN_TOKEN --name Arbeitslaptop
   ```
   Im Fenster erscheint „✅ verbunden“. Frag Jarvis jetzt z.B. „Wie ist der Status von meinem Laptop?“.
4. Autostart einrichten: denselben Befehl mit `--install` anhängen. Unter Windows startet der Agent
   dann bei jeder Anmeldung unsichtbar im Hintergrund, unter macOS per LaunchAgent und unter Linux
   als systemd-Dienst.

**Adresse:** Am einfachsten geht es mit **Tailscale** auf allen Geräten. Läuft Jarvis auf dem PC,
nimmst du die Tailscale-Adresse des PCs. Läuft Jarvis **auf dem Handy**, setz dort in `.env`
`JARVIS_HOST=0.0.0.0` (in Termux: `~/jarvis-shell.sh` → `nano .env`). Der Laptop nutzt dann
`http://<Tailscale-IP-des-Handys>:8080`. Der Token schützt den Zugang.

**Eigene Befehle** (z.B. ein Backup-Skript) legst du nur auf dem Laptop selbst fest, in der Datei
`~/.jarvis-pc-commands.json`:
```json
{"backup": "C:\\Skripte\\backup.bat", "teams": "start msteams:", "vpn": "rasdial Firma"}
```
Jarvis kann nur diese Namen ausführen („Starte das Backup“), keine beliebigen Befehle.

---

## Handy fernsteuern & Anrufe von Jarvis

Setzt du in der App unter ⚙ das Häkchen **„Im Hintergrund verbunden bleiben“**, ist das Handy für Jarvis
auch bei geschlossener App erreichbar. Das ist praktisch, wenn Jarvis auf dem PC läuft:
- **Nachrichten von Jarvis** kommen als Benachrichtigung, zum Beispiel das Morgen-Briefing oder ein
  Hinweis auf eine wichtige Mail.
- **Jarvis ruft an:** Bei Dringendem oder wenn Jarvis eine Entscheidung von dir braucht, klingelt das
  Handy mit einem Anruf-Bildschirm, auch auf dem Sperrbildschirm. Nimmst du ab, liest Jarvis die
  Nachricht vor und hört auf deine Antwort. Lehnst du ab, bleibt die Nachricht als Benachrichtigung.
- **Handy-Aktionen von unterwegs**, z.B. per Telegram oder vom PC aus („Ruf Mama an“, „Navigier mich
  nach Hause“): Kalendertermine trägt Jarvis direkt ein. Anrufe, SMS, WhatsApp und Navigation erscheinen
  als Benachrichtigung, die du antippst. Android erlaubt Apps nicht, so etwas ungefragt im Hintergrund
  zu starten.

Beim Einschalten fragt Android nach drei Erlaubnissen: **Benachrichtigungen**, **„Akku-Optimierung
ignorieren“** (sonst trennt Android die Verbindung) und, ab Android 14, **„Vollbild-Benachrichtigungen“**
für den Anruf-Bildschirm. Die Verbindung startet nach einem Neustart automatisch wieder.

---

## Vom Handy aus nutzen

### Variante A: Jarvis-App für Android (APK, empfohlen)

Die native App kann mehr als die Web-App: Sie nutzt die **Android-Spracherkennung und -Stimme**,
darf auf **Kontakte, Telefon, SMS, WhatsApp, Maps und Wecker** zugreifen, bietet eine
**Schnelleinstellungs-Kachel** („Jarvis“: runterwischen, antippen, sprechen) und lässt sich als
**Assistent** auswählen. Dann startet sie, wenn du die Home-Taste lange drückst.

**APK herunterladen:** Die APK wird bei jedem Push von GitHub Actions gebaut (Workflow
„Android-App (APK)“ unter **Actions** → letzter Lauf → Artefakt `jarvis-apk-…`). Für einen
direkten Download-Link auf dem Handy erstellst du ein Release:
```bash
git tag v0.1.0 && git push origin v0.1.0
```
Alternativ: **Actions → „Android-App (APK)“ → „Run workflow“** und bei „Release erstellen“ z.B. `v0.2.0` eintragen.
Die APK hängt dann unter **Releases** und lässt sich direkt am Handy herunterladen und installieren
(„Installation aus unbekannten Quellen“ einmal erlauben).

**Eigener Signier-Schlüssel (empfohlen):** Ohne eigenen Schlüssel signiert die Pipeline mit einem
wechselnden Debug-Schlüssel. Dann lässt sich ein Update oft nur nach Deinstallation der alten
Version installieren. Einmalig einrichten:
```bash
android/scripts/create-keystore.sh
```
Trag die angezeigten vier Werte als GitHub-Secrets ein (Repo → Settings → Secrets and variables →
Actions): `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` und
`ANDROID_KEY_PASSWORD`. Bewahre die `.jks`-Datei gut auf.

**Einrichten:** Läuft Jarvis auf dem Handy selbst, kreuzt du „Jarvis läuft auf diesem Handy“ an
(siehe [oben](#jarvis-komplett-auf-dem-handy)). Sonst fragt die App beim ersten Start nach der Server-Adresse (z.B.
`https://jarvis.dein-tailnet.ts.net`, siehe Tailscale unten) und dem Token (`jarvis token`).
Setzt du dort das Häkchen „Handy-Kontakte teilen“, lädt die App deine Kontakte etwa alle 12 Stunden
auf **deinen eigenen** Jarvis-Server (`data/phone_contacts.json`). Sie gehen nirgendwo anders hin.
Über ⚙ oben rechts kommst du jederzeit wieder in die Einstellungen.

**Handy-Kalender:** Mit dem Häkchen „Handy-Kalender mit Jarvis teilen“ nutzt Jarvis die Kalender, die
auf deinem Handy eingerichtet sind (Google, Outlook, Samsung …), ganz ohne eigenes Google-Setup. Die
App schickt beim Öffnen die Termine der letzten 7 und der nächsten 90 Tage an deinen Jarvis-Server.
Neue Termine trägt die App direkt in den Android-Kalender ein, von dort synchronisieren sie wie gewohnt.

**Gmail über die Gmail-App?** Das geht leider nicht: Die Gmail-App lässt keine anderen Apps an die
Mails. Für Gmail nimmst du stattdessen `jarvis mail-setup` → Gmail (App-Passwort, 2 Minuten).

**Standort:** Setzt du in den App-Einstellungen das Häkchen „Standort mit Jarvis teilen“, schickt die
App bei jeder Anfrage deinen aktuellen Ort mit. Die Adresse wird auf dem Handy selbst ermittelt, ohne
Google-Dienste. Die Daten gehen nur an deinen Jarvis-Server, dort wird nur der letzte Standort
gespeichert (`data/phone_location.json`). Den nutzt Jarvis auch, wenn du ihn über Telegram oder
Discord fragst („Wo bin ich gerade?“). Ohne Häkchen wird kein Standort übertragen.

**Wie Handy-Aktionen funktionieren:** Jarvis legt eine Aktion in eine Warteschlange, und die App
führt sie aus. Kommt die Anfrage aus der App, passiert das sofort. Kommt sie über Telegram oder
WhatsApp, passiert es beim nächsten Öffnen der App (nach 10 Minuten verfällt die Aktion).
**Anrufe, SMS und WhatsApp-Nachrichten werden nur vorbereitet:** Du tippst selbst auf
Anrufen bzw. Senden. Wecker und Timer stellt Jarvis direkt.

Selbst bauen (Android Studio oder Android-SDK + Gradle 8.14):
`gradle -p android assembleRelease`

### Variante A2: Web-App (PWA, ohne APK)

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

### Variante B2: Discord

1. Unter <https://discord.com/developers/applications> **New Application** anlegen, dann unter **Bot**
   den Token kopieren (`DISCORD_BOT_TOKEN`) und **Message Content Intent** einschalten.
2. Unter **OAuth2 → URL Generator** Scope `bot` und die Rechte *Send Messages*, *Read Message History*
   und *Attach Files* wählen. Über den Link lädst du den Bot auf deinen Server ein.
3. Schreib dem Bot eine Direktnachricht. Er nennt dir deine ID, die trägst du als
   `DISCORD_ALLOWED_USER_IDS` ein und startest Jarvis neu.

Jarvis antwortet in Direktnachrichten, wenn du ihn mit **@Jarvis** erwähnst, und in Kanälen aus
`DISCORD_CHANNEL_IDS` auch ohne Erwähnung. **Sprachnachrichten** aus der Discord-App versteht er
ebenfalls. `!neu` startet ein neues Gespräch. Briefing und Hinweise kommen dann auch als Discord-DM.

### Variante C: WhatsApp

Jarvis nutzt die **offizielle WhatsApp Cloud API** von Meta. Du chattest mit Jarvis wie mit einem
Kontakt, per Text oder Sprachnachricht.

**Kosten:** Antworten auf deine Nachrichten sind innerhalb von 24 Stunden kostenlos
(„Service-Fenster“). Jarvis schickt Push-Nachrichten wie das Briefing über WhatsApp deshalb nur,
solange dieses Fenster offen ist; sonst übernehmen Telegram oder ntfy. Prüf vor dem Einrichten
kurz die aktuellen Preise von Meta.

**Einrichten (einmalig):**
1. Unter <https://developers.facebook.com/> eine App vom Typ **Business** anlegen und das Produkt
   **WhatsApp** hinzufügen. Meta stellt dir eine **Test-Telefonnummer** bereit (kostenlos). Trag
   deine eigene Handynummer als Empfänger ein. Für Dauerbetrieb verbindest du eine eigene Nummer,
   z.B. eine günstige Zweit-SIM oder eine Festnetznummer. Diese Nummer darf nicht gleichzeitig in
   der normalen WhatsApp-App aktiv sein.
2. In `.env` eintragen:
   - `WHATSAPP_PHONE_NUMBER_ID`: die „Phone number ID“
   - `WHATSAPP_TOKEN`: ein dauerhafter Token (Business-Einstellungen → Systembenutzer → Token mit
     `whatsapp_business_messaging`)
   - `WHATSAPP_APP_SECRET`: App-Einstellungen → Allgemein
   - `WHATSAPP_VERIFY_TOKEN`: ein frei gewähltes Wort
   - `WHATSAPP_ALLOWED_NUMBERS`: deine Handynummer, z.B. `491701234567`
3. Meta muss Jarvis aus dem Internet erreichen. Mit Tailscale geht das über **Funnel**:
   ```bash
   tailscale funnel --bg 8080
   ```
   Trag im Meta-Dashboard unter WhatsApp → Konfiguration → Webhook die Callback-URL
   `https://<rechner>.<tailnet>.ts.net/webhook/whatsapp` und deinen Verify-Token ein.
   Abonniere dann das Feld **messages**.
   Hinweis: Mit Funnel ist die Jarvis-Oberfläche öffentlich erreichbar. Sie ist weiterhin durch
   deinen Token geschützt, und der Webhook prüft die Signatur von Meta. Wähle deshalb einen
   langen Token.

**Und WhatsApp-Nachrichten an Freunde?** Das geht über die Android-App: „Schreib Lena per WhatsApp,
dass …“ öffnet WhatsApp mit dem fertigen Text, und du tippst nur noch auf Senden. Deine privaten
WhatsApp-Chats liest Jarvis bewusst **nicht** mit. Das ginge nur über inoffizielle
WhatsApp-Web-Schnittstellen, und dafür sperrt WhatsApp Konten.

### Variante D: Tasker, Shortcuts & Co.

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
- `JARVIS_BRIEFING_TIME=07:30`: jeden Morgen Termine, wichtige Mails und To-dos per Telegram, ntfy,
  Discord bzw. WhatsApp (dort nur, wenn das 24-Stunden-Fenster offen ist).
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
  mcp_mail.py       MCP-Server: E-Mail per IMAP/SMTP (jeder Anbieter, mehrere Konten)
  mcp_dav.py        MCP-Server: CalDAV-Kalender + CardDAV-Kontakte
  mcp_phone.py      MCP-Server: Handy-Kontakte + Aktionen (über die App)
  phone.py          Warteschlange/Kontakte-Speicher für die App
  whatsapp.py       WhatsApp Cloud API (Webhook, Sprachnachrichten)
  discord_bot.py    Discord-Bot (DM, @Erwähnung, Sprachnachrichten)
  mcp_github.py     MCP-Server: GitHub (Benachrichtigungen, PRs, Issues, CI)
  pc.py             PC-Agent (nur Standardbibliothek, läuft eigenständig auf jedem Laptop)
  mcp_pc.py         MCP-Server: Laptop/PC steuern
  actions.py        Aufgaben-Warteschlange für Geräte (Handy, PCs)
  server.py         FastAPI: /api/chat, /api/voice, /api/tts + PWA
  telegram_bot.py   Telegram: Text & Sprachnachrichten
  scheduler.py      Morgen-Briefing, Posteingangs-Wächter
  stt.py / tts.py   Whisper / Piper (lokal)
  static/           Web-Oberfläche (auch in der Android-App angezeigt)
android/            native Android-App (Kotlin, WebView + Sprach-/Handy-Brücke, Termux-Start)
termux/             Installer: Jarvis + Claude Code komplett auf dem Handy
.github/workflows/  APK-Build + Release, Python-Tests
workspace/CLAUDE.md Persönlichkeit & Regeln von Jarvis
tests/              pytest (mit Fake-Claude, verbraucht kein Kontingent)
```

Tests ausführen: `pip install -e ".[dev]" && pytest`
