"""Jarvis – persönlicher Assistent auf Basis von Claude Code."""

try:
    from importlib.metadata import version as _version

    __version__ = _version("jarvis")
except Exception:  # noqa: BLE001 – nicht installiert (z.B. direkt aus dem Quellordner)
    __version__ = "0.0.0"
