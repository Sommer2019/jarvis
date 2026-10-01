"""MCP-Server `tasks`: Jarvis legt sich selbst Daueraufträge an („wenn … dann …“, „jeden Montag …“)."""

from __future__ import annotations

import os
from pathlib import Path

from .mcp_common import JarvisMCP
from .tasks import TaskStore

store = TaskStore(Path(os.getenv("JARVIS_DATA", Path(__file__).resolve().parent.parent / "data")),
                  os.getenv("JARVIS_TIMEZONE", "Europe/Berlin"))
mcp = JarvisMCP("tasks")


def _short(t: dict) -> dict:
    return {"id": t["id"], "name": t["name"], "plan": store.describe(t), "instruction": t["instruction"],
            "watch_url": t.get("watch_url") or None, "watch_contains": t.get("watch_contains") or None,
            "once": t.get("once"), "alert": t.get("alert"), "runs": t.get("runs", 0),
            "last_result": t.get("last_result") or None}


@mcp.tool()
def task_create(name: str, instruction: str = "", every_minutes: int = 0, daily_at: str = "", weekdays: str = "",
                at: str = "", watch_url: str = "", watch_contains: str = "", once: bool | None = None,
                alert: str = "auto") -> dict:
    """Legt einen Dauerauftrag an, den Jarvis selbstständig ausführt.

    Zeitplan (eins davon): every_minutes (min. 15) | daily_at='07:30' (+ weekdays='mo-fr') | at='2026-10-03T15:00' (einmalig).
    Webseiten-Wächter: watch_url (+ optional watch_contains='Suchbegriff'). Die Seite wird alle every_minutes
    (Standard 30, min. 2) geladen – ohne Claude-Kontingent. Mit watch_contains meldet sich Jarvis, sobald der
    Begriff auf der Seite steht; mit instruction (z.B. 'Preis unter 300 €') prüft Jarvis die Bedingung nur,
    wenn sich die Seite geändert hat.
    instruction: Was zu tun/prüfen ist, in Klartext. Gibt es nichts zu melden, antwortet der Lauf mit NICHTS.
    once: nach dem ersten Auslösen beenden (Standard bei Wächtern/einmaligen Aufträgen).
    alert: 'ring' = Handy klingelt (Jarvis ruft an), 'notify' = Benachrichtigung, 'auto' = Jarvis entscheidet.
    Vorher kurz mit dem Nutzer bestätigen, was genau überwacht/ausgeführt wird."""
    return _short(store.create(name, instruction, every_minutes, daily_at, weekdays, at, watch_url,
                               watch_contains, once, alert))


@mcp.tool()
def task_list() -> list[dict]:
    """Alle Daueraufträge mit Zeitplan und letztem Ergebnis."""
    return [_short(t) for t in store.all()]


@mcp.tool()
def task_pause(task_id: str, paused: bool = True) -> dict:
    """Pausiert einen Auftrag (paused=False setzt ihn fort)."""
    return _short(store.update(task_id, paused=paused))


@mcp.tool()
def task_delete(task_id: str) -> dict:
    """Löscht einen Auftrag."""
    if not store.delete(task_id):
        raise ValueError(f"Auftrag {task_id} nicht gefunden")
    return {"id": task_id, "status": "gelöscht"}


@mcp.tool()
def task_run_now(task_id: str) -> dict:
    """Führt einen Auftrag beim nächsten Durchlauf (innerhalb ~30 s) sofort aus."""
    import time

    return _short(store.update(task_id, next_run=time.time()))


if __name__ == "__main__":
    mcp.run()
