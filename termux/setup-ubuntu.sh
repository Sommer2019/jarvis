#!/usr/bin/env bash
# Läuft INNERHALB der Ubuntu-Umgebung (proot-distro) – wird von install.sh aufgerufen.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
REPO="${JARVIS_REPO:-https://github.com/Sommer2019/jarvis.git}"
BRANCH="${JARVIS_BRANCH:-master}"
DIR="${JARVIS_DIR:-/root/jarvis}"

say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }

say "Ubuntu-Pakete"
apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv python3-pip git curl ca-certificates

say "Claude Code installieren"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v claude >/dev/null; then
  curl -fsSL https://claude.ai/install.sh | bash || true
fi
if ! command -v claude >/dev/null; then
  echo "Nativer Installer fehlgeschlagen – nutze npm"
  apt-get install -y --no-install-recommends nodejs npm
  npm install -g @anthropic-ai/claude-code
fi
grep -q '.local/bin' /root/.bashrc 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> /root/.bashrc
claude --version

say "Jarvis herunterladen"
if [ -d "$DIR/.git" ]; then
  # auch flache Ein-Branch-Klone auf den gewünschten Branch umstellen
  git -C "$DIR" remote set-url origin "$REPO"
  git -C "$DIR" remote set-branches --add origin "$BRANCH"
  git -C "$DIR" fetch --depth 1 origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
  git -C "$DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

if [ ! -f .env ]; then
  say "Konfiguration anlegen"
  cp .env.example .env
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  sed -i "s|^JARVIS_HOST=.*|JARVIS_HOST=127.0.0.1|; s|^JARVIS_TOKEN=.*|JARVIS_TOKEN=$TOKEN|" .env
  # Spracherkennung macht auf dem Handy Android selbst → kein Whisper nötig
  sed -i "s|^GOOGLE_ENABLED=.*|GOOGLE_ENABLED=false|" .env
  echo "CLAUDE_BIN=$(command -v claude)" >> .env
fi

if [ -z "${JARVIS_SKIP_LOGIN:-}" ]; then
  say "Mit deinem Claude-Abo verbinden"
  # prüft selbst, ob schon verbunden; sonst Login-Link + Token einfügen
  .venv/bin/jarvis login </dev/tty || echo "Login später nachholen: ~/jarvis-login.sh"
fi

say "Einrichtung prüfen"
.venv/bin/jarvis doctor || true
echo
echo "Mail/Kalender/Kontakte: mit ~/jarvis-shell.sh und 'nano .env' eintragen (siehe README)."
