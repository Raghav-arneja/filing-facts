"""The parse job's invariants: nothing dropped, nothing duplicated, replays resume the batch."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from filing_facts.config import Settings
from filing_facts.parse.job import pending_source_keys, run, select_members
from filing_facts.parse.rows import ParseRunRecord
from filing_facts.parse.spool import Spool
from filing_facts.storage.memory import MemoryParseSink, MemoryRawStore, MemoryRunLog
from tests.parse.conftest import FIXTURE_FILES

SOURCE = "Accounts_Bulk_Data-2026-09-02.zip"
GOOD = [p.name for p in FIXTURE_FILES]  # seven real filings
CIC = "Prod223_4298_07114506_20251231_CIC.zip"
BAD = "Prod223_4298_11111111_20251231.html"
README = "notes/README.txt"
MESSY = {CIC: b"PK\x05\x06" + b"\0" * 18, BAD: b"<html><p>unclosed</html>", README: b"not a filing"}


def build_zip(extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in FIXTURE_FILES:
            zf.writestr(p.name, p.read_bytes())
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def store() -> MemoryRawStore:
    return MemoryRawStore()


@pytest.fixture
def sink() -> MemoryParseSink:
    return MemoryParseSink()


@pytest.fixture
def settings() -> Settings:
    return Settings(parse_cap=100)


def seed(store: MemoryRawStore, data: bytes, tmp_path: Path) -> None:
    p = tmp_path / SOURCE
    p.write_bytes(data)
    store.put(SOURCE, p, "sha")


def statuses(sink: MemoryParseSink) -> list[str]:
    return [r.status for r in sink.runs]


def test_every_member_lands_in_documents_or_quarantine(
    settings: Settings, store: MemoryRawStore, sink: MemoryParseSink, tmp_path: Path
) -> None:
    seed(store, build_zip(MESSY), tmp_path)
    out = run(settings, store=store, sink=sink, source_key=SOURCE)

    assert out.status == "succeeded"
    assert out.record.selected == len(GOOD) + 3
    assert out.record.parsed == len(GOOD)
    assert out.record.quarantined == 3
    assert out.record.facts == len(sink.facts) > 0
    reasons = {q["member_name"]: q["reason"] for q in sink.quarantine}
    assert reasons[CIC] == "nested_archive"
    assert reasons[BAD] == "MalformedDocumentError"
    assert reasons[README] == "UnknownDocumentNameError"
    unknown_row = next(q for q in sink.quarantine if q["member_name"] == README)
    assert unknown_row["document_id"] is None
    doc_ids = {d["document_id"] for d in sink.documents}
    assert doc_ids.isdisjoint({q["document_id"] for q in sink.quarantine})
    assert all(f["document_id"] in doc_ids for f in sink.facts)


def test_documents_carry_text_and_fact_counts(
    settings: Settings, store: MemoryRawStore, sink: MemoryParseSink, tmp_path: Path
) -> None:
    seed(store, build_zip(), tmp_path)
    run(settings, store=store, sink=sink, source_key=SOURCE)
    doc = next(d for d in sink.documents if d["document_id"] == "09469075_20260228")
    assert doc["company_number"] == "09469075"
    assert doc["period_end"] == "2026-02-28"
    assert "HR INFLUENCE LIMITED" in doc["text"]
    assert doc["fact_count"] == sum(f["document_id"] == doc["document_id"] for f in sink.facts)
    equity = [
        f for f in sink.facts if f["document_id"] == doc["document_id"] and f["concept"] == "Equity"
    ]
    assert {f["value"] for f in equity} == {"51718", "49096"}
    assert all(f["instant"] for f in equity)


def test_scaled_values_never_use_exponent_notation(tmp_path: Path) -> None:
    from dataclasses import dataclass
    from decimal import Decimal

    from filing_facts.parse.transforms import transform_numeric

    @dataclass
    class One:
        value: Decimal | None

    v = transform_numeric("1,000", fmt="ixt:numdotdecimal", scale=3, sign=None)
    assert v == Decimal("1000000")
    assert str(v) == "1.000E+6", "Decimal itself would serialise badly"
    spool = Spool(tmp_path / "s.ndjson", SOURCE)
    spool.append(One(v))
    assert next(iter(spool.rows())) == {"value": "1000000"}


def test_replay_is_a_no_op_even_with_quarantined_and_unknown_members(
    settings: Settings, store: MemoryRawStore, sink: MemoryParseSink, tmp_path: Path
) -> None:
    seed(store, build_zip(MESSY), tmp_path)
    first = run(settings, store=store, sink=sink, source_key=SOURCE)
    snapshot = (len(sink.documents), len(sink.facts), len(sink.quarantine))
    second = run(settings, store=store, sink=sink, source_key=SOURCE)

    assert (first.status, second.status) == ("succeeded", "skipped_existing")
    assert (len(sink.documents), len(sink.facts), len(sink.quarantine)) == snapshot
    assert statuses(sink) == ["started", "succeeded", "skipped_existing"]


def test_selection_is_deterministic_and_cap_widening_only_adds() -> None:
    names = [p.name for p in FIXTURE_FILES] + [README, "zzz/other.txt"]
    small, unknown_small = select_members(names, 3)
    small_again, _ = select_members(list(reversed(names)), 3)
    large, unknown_large = select_members(names, 5)
    assert [s.member for s in small] == [s.member for s in small_again]
    assert [s.member for s in large[:3]] == [s.member for s in small]
    assert unknown_small == [README, "zzz/other.txt"][:3]
    assert unknown_large == [README, "zzz/other.txt"]
    assert select_members(names, 1)[1] == [README], "unknown names are capped too"


def test_raising_the_cap_parses_only_the_new_documents(
    settings: Settings, store: MemoryRawStore, sink: MemoryParseSink, tmp_path: Path
) -> None:
    seed(store, build_zip(), tmp_path)
    run(settings, store=store, sink=sink, source_key=SOURCE, cap=3)
    assert len(sink.documents) == 3
    out = run(settings, store=store, sink=sink, source_key=SOURCE, cap=5)
    assert out.record.parsed == 2
    assert len(sink.documents) == 5
    assert len({d["document_id"] for d in sink.documents}) == 5


class CrashAfterFacts(MemoryParseSink):
    """Simulates the job dying after the facts load and before the documents load."""

    def __init__(self) -> None:
        super().__init__()
        self.crashes_left = 1

    def write_documents(self, batch_id: str, spool: Spool) -> bool:
        if self.crashes_left:
            self.crashes_left -= 1
            raise ConnectionError("simulated crash before documents load")
        return super().write_documents(batch_id, spool)


def test_crash_between_writes_resumes_the_same_batch_without_duplicates(
    settings: Settings, store: MemoryRawStore, tmp_path: Path
) -> None:
    """Quarantined members are included on purpose: that was the case that double-loaded."""
    sink = CrashAfterFacts()
    seed(store, build_zip(MESSY), tmp_path)
    first = run(settings, store=store, sink=sink, source_key=SOURCE)
    assert first.status == "failed"
    assert "simulated crash" in (first.record.error or "")
    facts_after_crash = len(sink.facts)
    assert facts_after_crash > 0
    assert sink.documents == []
    assert len(sink.quarantine) == 3

    second = run(
        settings, store=store, sink=sink, source_key=SOURCE, cap=2
    )  # cap change must not matter
    assert second.status == "succeeded"
    assert second.record.batch_id == first.record.batch_id, "replay resumes the pinned batch"
    assert len(sink.facts) == facts_after_crash, "facts batch must not be loaded twice"
    assert len(sink.quarantine) == 3
    assert len(sink.documents) == len(GOOD)
    assert statuses(sink) == ["started", "failed", "succeeded"]


class CrashOnSuccessRecord(MemoryParseSink):
    def __init__(self) -> None:
        super().__init__()
        self.crashes_left = 1

    def record_run(self, record: ParseRunRecord) -> bool:
        if record.status == "succeeded" and self.crashes_left:
            self.crashes_left -= 1
            raise ConnectionError("ledger write failed")
        return super().record_run(record)


def test_lost_success_record_reaches_a_terminal_state(
    settings: Settings, store: MemoryRawStore, tmp_path: Path
) -> None:
    sink = CrashOnSuccessRecord()
    runlog = MemoryRunLog()
    from datetime import UTC, datetime

    from filing_facts.storage.protocols import RunRecord

    now = datetime.now(UTC)
    runlog.record(RunRecord("r", SOURCE, "u", "succeeded", now, now))
    seed(store, build_zip(), tmp_path)

    first = run(settings, store=store, sink=sink, source_key=SOURCE)
    assert first.status == "failed"
    assert pending_source_keys(runlog, sink, settings.parse_cap) == [SOURCE]
    second = run(settings, store=store, sink=sink, source_key=SOURCE)
    assert second.status == "succeeded"
    assert len(sink.documents) == len(GOOD)
    assert pending_source_keys(runlog, sink, settings.parse_cap) == []


def test_missing_zip_is_a_recorded_failure(
    settings: Settings, store: MemoryRawStore, sink: MemoryParseSink
) -> None:
    out = run(settings, store=store, sink=sink, source_key="Accounts_Bulk_Data-1999-01-01.zip")
    assert out.status == "failed"
    assert statuses(sink) == ["failed"]


def test_pending_keys_respect_the_cap(sink: MemoryParseSink) -> None:
    from datetime import UTC, datetime

    from filing_facts.storage.protocols import RunRecord

    runlog = MemoryRunLog()
    now = datetime.now(UTC)
    for key, status in [("a.zip", "succeeded"), ("b.zip", "failed"), ("c.zip", "succeeded")]:
        runlog.record(RunRecord(key, key, "u", status, now, now))  # type: ignore[arg-type]
    sink.record_run(ParseRunRecord("r1", "a.zip", "b1", "succeeded", 500, 5, 5, 0, 9, now, now))
    sink.record_run(
        ParseRunRecord("r2", "c.zip", None, "skipped_existing", 500, 0, 0, 0, 0, now, now)
    )

    assert pending_source_keys(runlog, sink, 500) == []
    assert pending_source_keys(runlog, sink, 2000) == ["a.zip", "c.zip"], (
        "raising the cap re-queues"
    )


def test_released_quarantine_rows_make_members_pending_again(
    settings: Settings, store: MemoryRawStore, sink: MemoryParseSink, tmp_path: Path
) -> None:
    """A backfill stamps released_at instead of deleting; the member is then reprocessed."""
    seed(store, build_zip(MESSY), tmp_path)
    run(settings, store=store, sink=sink, source_key=SOURCE)
    assert run(settings, store=store, sink=sink, source_key=SOURCE).status == "skipped_existing"
    for q in sink.quarantine:
        if q["member_name"] == BAD:
            q["released_at"] = "2026-09-08T00:00:00+00:00"
    assert sink.released_sources() == {SOURCE}
    runlog = MemoryRunLog()
    from datetime import UTC, datetime

    from filing_facts.storage.protocols import RunRecord

    now = datetime.now(UTC)
    runlog.record(RunRecord("r", SOURCE, "u", "succeeded", now, now))
    assert pending_source_keys(runlog, sink, settings.parse_cap) == [SOURCE], "released -> pending"

    out = run(settings, store=store, sink=sink, source_key=SOURCE)
    assert out.status == "succeeded"
    assert out.record.selected == 1, "only the released member is reprocessed"
    assert sum(1 for q in sink.quarantine if q["member_name"] == BAD) == 2, "history kept"
