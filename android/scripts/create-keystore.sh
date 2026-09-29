#!/usr/bin/env bash
# Erzeugt einen Signier-Schlüssel für die Jarvis-App und zeigt die Werte für die
# GitHub-Secrets an. Den Keystore gut aufbewahren – ohne ihn keine App-Updates!
set -euo pipefail
KS=${1:-jarvis-release.jks}
ALIAS=jarvis
read -rsp "Passwort für den Keystore (min. 6 Zeichen): " PASS; echo
keytool -genkeypair -v -keystore "$KS" -alias "$ALIAS" -keyalg RSA -keysize 4096 \
  -validity 10000 -storepass "$PASS" -keypass "$PASS" -dname "CN=Jarvis"
echo
echo "Trage in GitHub → Settings → Secrets and variables → Actions ein:"
echo "  ANDROID_KEYSTORE_BASE64   = (Inhalt von ${KS}.b64)"
echo "  ANDROID_KEYSTORE_PASSWORD = dein Passwort"
echo "  ANDROID_KEY_ALIAS         = $ALIAS"
echo "  ANDROID_KEY_PASSWORD      = dein Passwort"
base64 -w0 "$KS" > "$KS.b64" 2>/dev/null || base64 -i "$KS" -o "$KS.b64"
echo "→ ${KS}.b64 geschrieben (danach löschen)."
