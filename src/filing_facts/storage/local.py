"""Filesystem backends for --dry-run: run the whole job without a GCP project."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from filing_facts.storage.memory import dedupe_key
from filing_facts.storage.protocols import AlreadyExistsError, RunRecord


class LocalRawStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def uri_for(self, key: str) -> str:
        return self._path(key).resolve().as_uri()

    def put(self, key: str, path: Path, sha256: str) -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            existing = target.with_suffix(target.suffix + ".sha256")
            raise AlreadyExistsError(
                key, existing.read_text().strip() if existing.exists() else None
            ) from exc
        with os.fdopen(fd, "wb") as out, path.open("rb") as src:
            shutil.copyfileobj(src, out)
        target.with_suffix(target.suffix + ".sha256").write_text(sha256 + "\n")
        return self.uri_for(key)


class JsonlRunLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def _rows(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def has_succeeded(self, source_key: str) -> bool:
        return any(
            r["source_key"] == source_key and r["status"] == "succeeded" for r in self._rows()
        )

    def record(self, record: RunRecord) -> bool:
        key = dedupe_key(record)
        if any(r.get("_dedupe_key") == key for r in self._rows()):
            return False
        row: dict[str, object] = {**asdict(record), "_dedupe_key": key}
        row["started_at"] = record.started_at.isoformat()
        row["finished_at"] = record.finished_at.isoformat()
        with self.path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        return True
