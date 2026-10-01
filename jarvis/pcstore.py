"""Registrierte PCs (PC-Agenten) und ihre Aufgaben-Warteschlange."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .actions import ActionQueue
from .pc import PC_ACTION_TYPES

ONLINE_SECONDS = 90  # Agent fragt alle ≤ 25 s nach – länger still = offline


class PcStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.devices_file = self.dir / "pc_devices.json"
        self.queue = ActionQueue(self.dir / "pc_actions.json", PC_ACTION_TYPES, ttl=120)

    def _load(self) -> dict:
        try:
            return json.loads(self.devices_file.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.devices_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, self.devices_file)

    def register(self, info: dict) -> dict:
        data = self._load()
        name = str(info.get("device") or "pc")[:60]
        data[name] = {"system": str(info.get("system", ""))[:80], "commands": list(info.get("commands", []))[:50],
                      "version": str(info.get("version", "")), "last_seen": time.time()}
        self._save(data)
        return data[name]

    def touch(self, name: str) -> None:
        data = self._load()
        if name in data:
            data[name]["last_seen"] = time.time()
            self._save(data)

    def any_registered(self) -> bool:
        return bool(self._load())

    def devices(self) -> list[dict]:
        now = time.time()
        return [{"name": n, **d, "online": now - d.get("last_seen", 0) < ONLINE_SECONDS,
                 "last_seen_min": int((now - d.get("last_seen", 0)) / 60)}
                for n, d in sorted(self._load().items(), key=lambda x: -x[1].get("last_seen", 0))]

    def resolve(self, name: str = "") -> str:
        devs = self.devices()
        if not devs:
            raise RuntimeError("Kein PC verbunden. Auf dem PC den Jarvis-PC-Agent starten (siehe README → Laptop steuern).")
        if name:
            n = name.lower()
            for d in devs:
                if n == d["name"].lower() or n in d["name"].lower():
                    return d["name"]
            raise ValueError(f"PC '{name}' unbekannt. Verbunden: {', '.join(d['name'] for d in devs)}")
        online = [d for d in devs if d["online"]]
        if not online:
            raise RuntimeError(f"Kein PC online (zuletzt: {devs[0]['name']} vor {devs[0]['last_seen_min']} Min.). "
                               "Ist der PC an und der Agent gestartet?")
        return online[0]["name"]
