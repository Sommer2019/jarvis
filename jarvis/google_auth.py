"""Google-OAuth für Gmail und Kalender (kostenlos, eigenes Google-Cloud-Projekt)."""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",   # lesen, labeln, archivieren, Entwürfe
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def load_credentials(token_file: Path) -> Credentials:
    if not token_file.exists():
        raise RuntimeError(
            f"Kein Google-Token gefunden ({token_file}). Einmalig `jarvis google-auth` ausführen."
        )
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
        else:
            raise RuntimeError("Google-Token ungültig. Bitte `jarvis google-auth` erneut ausführen.")
    return creds


def run_auth_flow(credentials_file: Path, token_file: Path, port: int = 8765) -> None:
    """Öffnet den Google-Login. Auf einem Server ohne Browser: Link am PC öffnen und
    vorher `ssh -L 8765:localhost:8765 server` machen – oder die Auth am PC ausführen
    und data/google_token.json auf den Server kopieren."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not credentials_file.exists():
        raise SystemExit(
            f"{credentials_file} fehlt. In der Google Cloud Console einen OAuth-Client "
            "(Typ 'Desktop-App') anlegen und die JSON dort ablegen. Siehe README."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=False, prompt="consent",
                                  authorization_prompt_message="Öffne diesen Link im Browser:\n{url}\n")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)
    print(f"Gespeichert: {token_file}")
