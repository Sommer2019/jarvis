#!/data/data/com.termux/files/usr/bin/bash
# ============================================================================
#  Jarvis komplett auf dem Handy – Installer für Termux
#
#  In Termux (aus F-Droid, NICHT Play Store) ausführen:
#    curl -fsSL https://raw.githubusercontent.com/Sommer2019/jarvis/master/termux/install.sh | bash
#  oder nach dem Klonen:  bash termux/install.sh
#
#  Richtet ein: Ubuntu-Umgebung (proot-distro) mit Claude Code + Jarvis,
#  Start-/Stopp-Skripte, Autostart (Termux:Boot) und die Freigabe, dass die
#  Jarvis-App Jarvis selbstständig starten darf.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
# Aus einem Klon gestartet? Dann dessen Repo-URL und Branch übernehmen.
CLONE_REPO="$(git -C "$HERE/.." remote get-url origin 2>/dev/null || true)"
CLONE_BRANCH="$(git -C "$HERE/.." rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
REPO="${JARVIS_REPO:-${CLONE_REPO:-https://github.com/Sommer2019/jarvis.git}}"
BRANCH="${JARVIS_BRANCH:-${CLONE_BRANCH:-master}}"
DISTRO=ubuntu

say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }

say "Termux-Pakete installieren"
pkg update -y
pkg install -y proot-distro curl git termux-api || pkg install -y proot-distro curl git

# Nicht über den Rootfs-Pfad prüfen – der unterscheidet sich je nach proot-distro-Version
in_ubuntu() { proot-distro login "$DISTRO" --shared-tmp -- "$@"; }
if ! in_ubuntu true >/dev/null 2>&1; then
  say "Ubuntu-Umgebung installieren (einmalig, ca. 1 GB)"
  proot-distro install "$DISTRO"
fi

say "Jarvis in Ubuntu einrichten"
# Setup-Skript über das gemeinsame /tmp übergeben (--shared-tmp: Termux-$TMPDIR = /tmp in Ubuntu)
SHARED_TMP="${TMPDIR:-$PREFIX/tmp}"
mkdir -p "$SHARED_TMP"
if [ -n "$HERE" ] && [ -f "$HERE/setup-ubuntu.sh" ]; then
  cp "$HERE/setup-ubuntu.sh" "$SHARED_TMP/jarvis-setup.sh"
else
  curl -fsSL "https://raw.githubusercontent.com/Sommer2019/jarvis/$BRANCH/termux/setup-ubuntu.sh" -o "$SHARED_TMP/jarvis-setup.sh"
fi
in_ubuntu env JARVIS_REPO="$REPO" JARVIS_BRANCH="$BRANCH" bash /tmp/jarvis-setup.sh
rm -f "$SHARED_TMP/jarvis-setup.sh"

say "Start-/Stopp-Skripte anlegen"
cat > "$HOME/jarvis-start.sh" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
# Startet Jarvis im Hintergrund (wird auch von der Jarvis-App aufgerufen)
if pgrep -f "jarvis serve" >/dev/null; then echo "Jarvis läuft bereits"; exit 0; fi
command -v termux-wake-lock >/dev/null && termux-wake-lock
nohup proot-distro login ubuntu --shared-tmp -- bash -lc 'cd /root/jarvis && exec .venv/bin/jarvis serve' \
  >> "$HOME/jarvis.log" 2>&1 &
echo "Jarvis gestartet (Log: ~/jarvis.log)"
SH
cat > "$HOME/jarvis-stop.sh" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
pkill -f "jarvis serve" && echo "Jarvis gestoppt" || echo "Jarvis lief nicht"
command -v termux-wake-unlock >/dev/null && termux-wake-unlock
SH
echo "$BRANCH" > "$HOME/.jarvis-branch"
cat > "$HOME/jarvis-update.sh" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
# Holt die neueste Jarvis-Version (in die Ubuntu-Umgebung, aus der Jarvis läuft) und startet neu
B="$(cat "$HOME/.jarvis-branch" 2>/dev/null || echo master)"
bash "$HOME/jarvis-stop.sh" || true
proot-distro login ubuntu --shared-tmp -- env B="$B" bash -lc 'cd /root/jarvis \
  && git remote set-branches --add origin "$B" \
  && git fetch --depth 1 origin "+refs/heads/$B:refs/remotes/origin/$B" \
  && git checkout -q -B "$B" "origin/$B" \
  && .venv/bin/pip install -q -e . \
  && echo "Jarvis aktualisiert: $(git log -1 --format="%h %s")"'
bash "$HOME/jarvis-start.sh"
SH
cat > "$HOME/jarvis-login.sh" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
# Verbindet Jarvis mit deinem Claude-Abo (Token einfügen) und startet Jarvis neu
proot-distro login ubuntu --shared-tmp -- bash -lc 'cd /root/jarvis && .venv/bin/jarvis login' && {
  bash "$HOME/jarvis-stop.sh" >/dev/null 2>&1 || true
  bash "$HOME/jarvis-start.sh"
}
SH
cat > "$HOME/jarvis-token.sh" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
# Zeigt den Token für die Jarvis-App (und kopiert ihn, falls Termux:API installiert ist)
T="$(proot-distro login ubuntu --shared-tmp -- sh -c "grep '^JARVIS_TOKEN=' /root/jarvis/.env | cut -d= -f2-" | tr -d '\r')"
echo "Token für die Jarvis-App: $T"
command -v termux-clipboard-set >/dev/null && printf '%s' "$T" | timeout 5 termux-clipboard-set && echo "(in die Zwischenablage kopiert)"
SH
cat > "$HOME/jarvis-shell.sh" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
# Öffnet eine Shell in der Jarvis-Umgebung (z.B. für `jarvis doctor`, `jarvis google-auth`, .env bearbeiten)
exec proot-distro login ubuntu --shared-tmp -- bash -lc 'cd /root/jarvis && source .venv/bin/activate && exec bash'
SH
chmod +x "$HOME"/jarvis-*.sh

say "Autostart beim Einschalten (braucht die App Termux:Boot)"
mkdir -p "$HOME/.termux/boot"
cp "$HOME/jarvis-start.sh" "$HOME/.termux/boot/jarvis"

say "Jarvis-App darf Jarvis starten (allow-external-apps)"
mkdir -p "$HOME/.termux"
touch "$HOME/.termux/termux.properties"
if grep -q '^allow-external-apps' "$HOME/.termux/termux.properties"; then
  sed -i 's/^allow-external-apps.*/allow-external-apps = true/' "$HOME/.termux/termux.properties"
else
  echo "allow-external-apps = true" >> "$HOME/.termux/termux.properties"
fi
command -v termux-reload-settings >/dev/null && termux-reload-settings || true

bash "$HOME/jarvis-start.sh"

TOKEN="$(in_ubuntu sh -c "grep '^JARVIS_TOKEN=' /root/jarvis/.env | cut -d= -f2-" 2>/dev/null | tr -d '\r' || true)"
if [ -n "$TOKEN" ] && command -v termux-clipboard-set >/dev/null; then
  printf "%s" "$TOKEN" | timeout 5 termux-clipboard-set && COPIED=" (in die Zwischenablage kopiert)" || true
fi

cat <<TXT

✅ Fertig! Jarvis läuft jetzt auf deinem Handy.

In der Jarvis-App:
  1. „Jarvis läuft auf diesem Handy“ auswählen
  2. Token einfügen: $TOKEN${COPIED:-}
  3. In den Android-Einstellungen der Jarvis-App unter „Berechtigungen“ →
     „Befehle in Termux ausführen“ erlauben (damit die App Jarvis selbst starten kann)

Wichtig: Android → Apps → Termux → Akku → „Nicht einschränken“, sonst beendet
Android Jarvis im Hintergrund.

Nützlich:  ~/jarvis-token.sh (Token für die App)  ·  ~/jarvis-login.sh (Claude-Abo verbinden)  ·  ~/jarvis-shell.sh (Einstellungen, jarvis doctor)
           ~/jarvis-update.sh  ·  ~/jarvis-stop.sh
TXT
