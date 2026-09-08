"""Filesystem backends for --dry-run: run the whole job without a GCP project."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from filing_facts.extract.rows import DocumentText, ExtractRunRecord, extract_run_key
from filing_facts.index.rows import IndexRunRecord, index_run_key
from filing_facts.parse.rows import ParseRunRecord, parse_run_key, to_row
from filing_facts.parse.spool import Spool
from filing_facts.storage.memory import dedupe_key, stable_order
from filing_facts.storage.protocols import AlreadyExistsError, RunRecord


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


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

    def fetch(self, key: str, dest: Path) -> None:
        src = self._path(key)
        if not src.exists():
            raise KeyError(key)
        shutil.copyfile(src, dest)

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

    def _rows(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)

    def has_succeeded(self, source_key: str) -> bool:
        return any(
            r["source_key"] == source_key and r["status"] == "succeeded" for r in self._rows()
        )

    def succeeded_keys(self) -> list[str]:
        return [str(r["source_key"]) for r in self._rows() if r["status"] == "succeeded"]

    def record(self, record: RunRecord) -> bool:
        key = dedupe_key(record)
        if any(r.get("_dedupe_key") == key for r in self._rows()):
            return False
        row: dict[str, Any] = {**to_row(record), "_dedupe_key": key}
        with self.path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        return True


class JsonlParseSink:
    """One directory per source key; one file per (table, batch), written atomically.

    Layout: <root>/<source_key>/<table>/<batch_id>.jsonl. A file is renamed into place only
    after every row is written, so a crash can never leave a batch marked done but empty.
    Lookups for one source read only that source's files.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _table(self, source_key: str, table: str) -> Path:
        return self.root / source_key / table

    def _read_table(self, source_key: str, table: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for f in sorted(self._table(source_key, table).glob("*.jsonl")):
            rows.extend(read_jsonl(f))
        return rows

    def _write(self, source_key: str, table: str, name: str, spool: Spool) -> bool:
        target = self._table(source_key, table) / f"{name}.jsonl"
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        spool.close()
        tmp = target.with_suffix(".jsonl.tmp")
        shutil.copyfile(spool.path, tmp)
        os.replace(tmp, target)  # atomic on POSIX
        return True

    def processed_members(self, source_key: str) -> set[str]:
        rows = self._read_table(source_key, "documents") + [
            q for q in self._read_table(source_key, "quarantine") if not q.get("released_at")
        ]
        return {str(r["member_name"]) for r in rows}

    def released_sources(self) -> set[str]:
        out: set[str] = set()
        for src in self.root.iterdir():
            if src.is_dir() and any(
                q.get("released_at") for q in self._read_table(src.name, "quarantine")
            ):
                out.add(src.name)
        return out

    def purge_source(self, source_key: str) -> dict[str, int]:
        removed: dict[str, int] = {}
        for table in ("documents", "facts", "quarantine"):
            removed[table] = len(self._read_table(source_key, table))
            shutil.rmtree(self._table(source_key, table), ignore_errors=True)
        return removed

    def _runs(self, source_key: str) -> list[dict[str, Any]]:
        return self._read_table(source_key, "parse_runs")

    def open_batch(self, source_key: str) -> tuple[str, list[str]] | None:
        runs = self._runs(source_key)
        finished = {r["batch_id"] for r in runs if r["status"] == "succeeded"}
        started = [r for r in runs if r["status"] == "started" and r["batch_id"] not in finished]
        if not started:
            return None
        last = max(started, key=lambda r: datetime.fromisoformat(str(r["started_at"])))
        return str(last["batch_id"]), [str(m) for m in last["members"]]

    def succeeded_caps(self) -> dict[str, int]:
        caps: dict[str, int] = {}
        for src in self.root.iterdir():
            if not src.is_dir():
                continue
            for r in self._runs(src.name):
                if r["status"] in ("succeeded", "skipped_existing"):
                    caps[src.name] = max(caps.get(src.name, -1), int(r["cap"]))
        return caps

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool:
        return self._write(self._source_of(spool), "quarantine", batch_id, spool)

    def write_facts(self, batch_id: str, spool: Spool) -> bool:
        return self._write(self._source_of(spool), "facts", batch_id, spool)

    def write_documents(self, batch_id: str, spool: Spool) -> bool:
        return self._write(self._source_of(spool), "documents", batch_id, spool)

    @staticmethod
    def _source_of(spool: Spool) -> str:
        return spool.source_key

    def record_run(self, record: ParseRunRecord) -> bool:
        target = self._table(record.source_key, "parse_runs") / f"{parse_run_key(record)}.jsonl"
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".jsonl.tmp")
        tmp.write_text(json.dumps(to_row(record)) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return True


class JsonlExtractSink:
    """Reads documents from the local parse output; writes to <root>/<model>/<prompt>/<table>/."""

    def __init__(self, parsed_root: Path, root: Path) -> None:
        self.parsed_root = parsed_root
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _all_documents(self) -> list[DocumentText]:
        out: list[DocumentText] = []
        for f in sorted(self.parsed_root.glob("*/documents/*.jsonl")):
            for r in read_jsonl(f):
                out.append(
                    DocumentText(str(r["document_id"]), str(r["source_key"]), str(r["text"]))
                )
        return out

    def _dir(self, model: str, prompt_id: str, table: str) -> Path:
        return self.root / model / prompt_id / table

    def _read(self, model: str, prompt_id: str, table: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for f in sorted(self._dir(model, prompt_id, table).glob("*.jsonl")):
            rows.extend(read_jsonl(f))
        return rows

    def processed_ids(self, model: str, prompt_id: str) -> set[str]:
        done = {str(r["document_id"]) for r in self._read(model, prompt_id, "extractions")}
        done |= {
            str(r["document_id"])
            for r in self._read(model, prompt_id, "quarantine")
            if not r.get("released_at")
        }
        return done

    def pending_documents(self, model: str, prompt_id: str, cap: int) -> list[DocumentText]:
        done = self.processed_ids(model, prompt_id)
        released = {
            str(r["document_id"])
            for r in self._read(model, prompt_id, "quarantine")
            if r.get("released_at")
        } - done
        todo = [d for d in self._all_documents() if d.document_id not in done]
        todo.sort(key=lambda d: (d.document_id not in released, stable_order(d.document_id)))
        return todo[:cap]

    def documents_by_id(self, ids: list[str]) -> list[DocumentText]:
        wanted = set(ids)
        return [d for d in self._all_documents() if d.document_id in wanted]

    def open_batch(self, model: str, prompt_id: str) -> tuple[str, list[str]] | None:
        runs = self._read(model, prompt_id, "extract_runs")
        finished = {r["batch_id"] for r in runs if r["status"] == "succeeded"}
        started = [r for r in runs if r["status"] == "started" and r["batch_id"] not in finished]
        if not started:
            return None
        last = max(started, key=lambda r: datetime.fromisoformat(str(r["started_at"])))
        return str(last["batch_id"]), [str(i) for i in last["document_ids"]]

    def _write(self, model: str, prompt_id: str, table: str, name: str, spool: Spool) -> bool:
        target = self._dir(model, prompt_id, table) / f"{name}.jsonl"
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        spool.close()
        tmp = target.with_suffix(".jsonl.tmp")
        shutil.copyfile(spool.path, tmp)
        os.replace(tmp, target)
        return True

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool:
        return self._write(
            spool.model, spool.prompt_id, "quarantine", f"{batch_id}-{spool.attempt}", spool
        )

    def write_extractions(self, batch_id: str, spool: Spool) -> bool:
        return self._write(
            spool.model, spool.prompt_id, "extractions", f"{batch_id}-{spool.attempt}", spool
        )

    def record_run(self, record: ExtractRunRecord) -> bool:
        target = (
            self._dir(record.model, record.prompt_id, "extract_runs")
            / f"{extract_run_key(record)}.jsonl"
        )
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".jsonl.tmp")
        tmp.write_text(json.dumps(to_row(record)) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return True


class JsonlIndexSink:
    """Reads documents from the local parse output; writes chunks under <root>/<model>/."""

    def __init__(self, parsed_root: Path, root: Path) -> None:
        self.parsed_root = parsed_root
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _all_documents(self) -> list[DocumentText]:
        out: list[DocumentText] = []
        for f in sorted(self.parsed_root.glob("*/documents/*.jsonl")):
            for r in read_jsonl(f):
                out.append(
                    DocumentText(str(r["document_id"]), str(r["source_key"]), str(r["text"]))
                )
        return out

    def _read(self, model: str, table: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for f in sorted((self.root / model / table).glob("*.jsonl")):
            rows.extend(read_jsonl(f))
        return rows

    def processed_ids(self, embedding_model: str) -> set[str]:
        return {str(r["document_id"]) for r in self._read(embedding_model, "chunks")}

    def pending_documents(self, embedding_model: str, cap: int) -> list[DocumentText]:
        done = self.processed_ids(embedding_model)
        todo = [d for d in self._all_documents() if d.document_id not in done]
        todo.sort(key=lambda d: stable_order(d.document_id))
        return todo[:cap]

    def documents_by_id(self, ids: list[str]) -> list[DocumentText]:
        wanted = set(ids)
        return [d for d in self._all_documents() if d.document_id in wanted]

    def open_batch(self, embedding_model: str) -> tuple[str, list[str]] | None:
        runs = self._read(embedding_model, "index_runs")
        finished = {r["batch_id"] for r in runs if r["status"] == "succeeded"}
        started = [r for r in runs if r["status"] == "started" and r["batch_id"] not in finished]
        if not started:
            return None
        last = max(started, key=lambda r: datetime.fromisoformat(str(r["started_at"])))
        return str(last["batch_id"]), [str(i) for i in last["document_ids"]]

    def _write(self, model: str, table: str, name: str, spool: Spool) -> bool:
        target = self.root / model / table / f"{name}.jsonl"
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        spool.close()
        tmp = target.with_suffix(".jsonl.tmp")
        shutil.copyfile(spool.path, tmp)
        os.replace(tmp, target)
        return True

    def write_chunks(self, batch_id: str, spool: Spool) -> bool:
        return self._write(spool.model, "chunks", f"{batch_id}-{spool.attempt}", spool)

    def record_run(self, record: IndexRunRecord) -> bool:
        target = (
            self.root / record.embedding_model / "index_runs" / f"{index_run_key(record)}.jsonl"
        )
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".jsonl.tmp")
        tmp.write_text(json.dumps(to_row(record)) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return True
