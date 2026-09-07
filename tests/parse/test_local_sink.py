from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filing_facts.parse.rows import ParseRunRecord
from filing_facts.parse.spool import Spool
from filing_facts.storage.local import JsonlParseSink

SRC = "Accounts_Bulk_Data-2026-09-02.zip"


@dataclass
class Row:
    member_name: str
    source_key: str
    document_id: str | None


def _spool(tmp: Path, name: str, rows: list[Row]) -> Spool:
    s = Spool(tmp / name, SRC)
    for r in rows:
        s.append(r)
    s.close()
    return s


def test_batch_files_are_atomic_and_per_source(tmp_path: Path) -> None:
    sink = JsonlParseSink(tmp_path / "parsed")
    rows = [Row("a.html", SRC, "a"), Row("b.html", SRC, "b")]
    assert sink.write_documents("b1", _spool(tmp_path, "d.ndjson", rows)) is True
    assert sink.write_documents("b1", _spool(tmp_path, "d2.ndjson", rows)) is False, "batch dedupe"
    assert sink.processed_members(SRC) == {"a.html", "b.html"}
    assert sink.processed_members("other.zip") == set()
    assert not list((tmp_path / "parsed").rglob("*.tmp")), "no partial files left behind"


def test_open_batch_and_caps(tmp_path: Path) -> None:
    sink = JsonlParseSink(tmp_path / "parsed")
    now = datetime.now(UTC)
    assert sink.open_batch(SRC) is None
    started = ParseRunRecord(
        "r1", SRC, "b1", "started", 500, 2, 0, 0, 0, now, now, members=["a", "b"]
    )
    assert sink.record_run(started) is True
    assert sink.open_batch(SRC) == ("b1", ["a", "b"])
    sink.record_run(ParseRunRecord("r1", SRC, "b1", "succeeded", 500, 2, 2, 0, 9, now, now))
    assert sink.open_batch(SRC) is None
    assert sink.succeeded_caps() == {SRC: 500}
