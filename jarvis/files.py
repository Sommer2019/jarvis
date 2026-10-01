"""Dateiablage von Jarvis: Dateien vom PC, vom Handy (Teilen) oder für den Versand aufs Handy.

Die Dateien liegen im Workspace unter `dateien/`, damit Jarvis sie direkt lesen kann
(Text, PDFs, Bilder). Nach 14 Tagen werden sie automatisch gelöscht.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import BinaryIO

KEEP_DAYS = 14
MAX_BYTES = 200 * 1024 * 1024


def clean_name(name: str) -> str:
    name = Path(name or "datei").name
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .") or "datei"
    return name[:120]


class FileStore:
    def __init__(self, workspace: Path, data_dir: Path):
        self.dir = Path(workspace) / "dateien"
        self.meta_file = Path(data_dir) / "files.json"

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.meta_file.read_text())
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.meta_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.meta_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1))
        os.replace(tmp, self.meta_file)

    def cleanup(self) -> None:
        cutoff = time.time() - KEEP_DAYS * 86400
        keep = []
        for f in self._load():
            if f["created"] < cutoff:
                shutil.rmtree(self.dir / f["id"], ignore_errors=True)
            else:
                keep.append(f)
        self._save(keep)

    def save(self, stream: BinaryIO, name: str, source: str = "") -> dict:
        self.cleanup()
        fid = uuid.uuid4().hex[:10]
        name = clean_name(name)
        folder = self.dir / fid
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        size = 0
        with open(target, "wb") as out:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    out.close()
                    shutil.rmtree(folder, ignore_errors=True)
                    raise ValueError("Datei zu groß (max. 200 MB)")
                out.write(chunk)
        meta = {"id": fid, "name": name, "size_kb": round(size / 1024, 1),
                "mime": mimetypes.guess_type(name)[0] or "application/octet-stream",
                "source": source[:200], "created": time.time(),
                "workspace_path": f"dateien/{fid}/{name}"}
        items = self._load()
        items.append(meta)
        self._save(items)
        return meta

    def get(self, file_id: str) -> dict | None:
        return next((f for f in self._load() if f["id"] == file_id), None)

    def path(self, meta: dict) -> Path:
        return self.dir / meta["id"] / meta["name"]

    def list(self) -> list[dict]:
        return sorted(self._load(), key=lambda f: -f["created"])
