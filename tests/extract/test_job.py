"""The extract job's invariants: nothing dropped, nothing paid for twice, batches resume."""

from __future__ import annotations

import json
from typing import Any

import pytest

from filing_facts.config import Settings
from filing_facts.extract.job import run
from filing_facts.extract.model import FakeModel, ModelCallError
from filing_facts.extract.prompt import load_prompt
from filing_facts.extract.rows import DocumentText
from filing_facts.parse.spool import Spool
from filing_facts.storage.memory import MemoryExtractSink
from tests.extract.conftest import good_answer

DOCS = [
    DocumentText(f"0000000{i}_20251231", "Accounts_Bulk_Data-2026-09-02.zip", f"doc {i}")
    for i in range(6)
]


@pytest.fixture
def settings() -> Settings:
    return Settings(extract_cap=10, extract_min_confidence=0.5, extract_threads=2)


@pytest.fixture
def sink() -> MemoryExtractSink:
    return MemoryExtractSink(DOCS)


def prompt():  # noqa: ANN201
    return load_prompt("v1")


def by_text(mapping: dict[str, Any]) -> FakeModel:
    """Answer per document text; anything not listed gets a good answer."""

    def answer(text: str) -> dict[str, Any] | str:
        out = mapping.get(text, good_answer())
        if isinstance(out, Exception):
            raise out
        return out

    return FakeModel(answer=answer)


def test_every_document_lands_in_extractions_or_quarantine(
    settings: Settings, sink: MemoryExtractSink
) -> None:
    model = by_text(
        {
            "doc 1": {"nope": 1},  # schema failure
            "doc 2": good_answer(overall_confidence=0.2),  # low confidence: kept and flagged
            "doc 3": ModelCallError("503 after retries"),
        }
    )
    out = run(settings, sink=sink, model=model, prompt=prompt())

    assert out.status == "succeeded"
    assert out.record.selected == 6
    assert out.record.extracted == 4  # docs 0, 2, 4, 5; doc 2 is kept despite low confidence
    assert out.record.quarantined == 3
    reasons = {q["document_id"]: q["reason"] for q in sink.quarantine}
    assert reasons["00000001_20251231"] == "SchemaValidationError"
    assert reasons["00000002_20251231"] == "LowConfidence"
    assert reasons["00000003_20251231"] == "ModelCallError"
    assert all(q["stage"] == "extract" and q["model"] == model.model_id for q in sink.quarantine)
    statuses = {e["document_id"]: e["status"] for e in sink.extractions}
    assert statuses["00000002_20251231"] == "low_confidence"
    assert sum(1 for s in statuses.values() if s == "ok") == 3
    assert (
        json.loads(sink.extractions[0]["extraction_json"])["equity"]["current"]["value"] == "51718"
    )


def test_replay_is_a_no_op_and_costs_nothing(settings: Settings, sink: MemoryExtractSink) -> None:
    model = by_text({})
    first = run(settings, sink=sink, model=model, prompt=prompt())
    calls = len(model.calls)
    second = run(settings, sink=sink, model=model, prompt=prompt())
    assert (first.status, second.status) == ("succeeded", "skipped_existing")
    assert len(model.calls) == calls, "no document may be paid for twice"
    assert [r.status for r in sink.runs] == ["started", "succeeded", "skipped_existing"]


def test_cap_widening_processes_only_new_documents(
    settings: Settings, sink: MemoryExtractSink
) -> None:
    model = by_text({})
    run(settings, sink=sink, model=model, prompt=prompt(), cap=2)
    assert len(sink.extractions) == 2
    out = run(settings, sink=sink, model=model, prompt=prompt(), cap=10)
    assert out.record.extracted == 4
    assert len({e["document_id"] for e in sink.extractions}) == 6


def test_a_second_model_is_independent(settings: Settings, sink: MemoryExtractSink) -> None:
    run(
        settings,
        sink=sink,
        model=FakeModel(model_id="fake-a", answer=lambda _t: good_answer()),
        prompt=prompt(),
    )
    out = run(
        settings,
        sink=sink,
        model=FakeModel(model_id="fake-b", answer=lambda _t: good_answer()),
        prompt=prompt(),
    )
    assert out.record.extracted == 6
    assert {e["model"] for e in sink.extractions} == {"fake-a", "fake-b"}


class CrashAfterQuarantine(MemoryExtractSink):
    def __init__(self) -> None:
        super().__init__(DOCS)
        self.crashes_left = 1

    def write_extractions(self, batch_id: str, spool: Spool) -> bool:
        if self.crashes_left:
            self.crashes_left -= 1
            raise ConnectionError("simulated crash before extractions load")
        return super().write_extractions(batch_id, spool)


def test_crash_resumes_the_pinned_batch_paying_only_for_unanswered_documents(
    settings: Settings,
) -> None:
    sink = CrashAfterQuarantine()
    model = by_text({"doc 1": {"nope": 1}})
    first = run(settings, sink=sink, model=model, prompt=prompt())
    assert first.status == "failed"
    assert len(sink.quarantine) == 1, "quarantine loaded before the crash"
    assert sink.extractions == []
    calls_after_crash = len(model.calls)

    second = run(
        settings, sink=sink, model=model, prompt=prompt(), cap=1
    )  # cap change must not matter
    assert second.status == "succeeded"
    assert second.record.batch_id == first.record.batch_id
    assert len(model.calls) == calls_after_crash + 5, (
        "only the five unanswered documents are re-called"
    )
    assert len(sink.extractions) == 5
    assert len(sink.quarantine) == 1
    assert second.record.selected == 5


def test_resume_quarantines_pinned_documents_that_vanished(settings: Settings) -> None:
    sink = CrashAfterQuarantine()
    model = by_text({})
    run(settings, sink=sink, model=model, prompt=prompt())  # fails after pinning six ids
    sink.source_documents = [
        d for d in sink.source_documents if d.document_id != "00000004_20251231"
    ]
    out = run(settings, sink=sink, model=model, prompt=prompt())
    assert out.status == "succeeded"
    reasons = {q["document_id"]: q["reason"] for q in sink.quarantine}
    assert reasons["00000004_20251231"] == "MissingDocument"
    assert out.record.selected == 6
    assert out.record.extracted + out.record.quarantined == 6


def test_a_real_model_without_a_price_is_refused(
    settings: Settings, sink: MemoryExtractSink
) -> None:
    out = run(settings, sink=sink, model=FakeModel(model_id="gemini-99-ultra"), prompt=prompt())
    assert out.status == "failed"
    assert "UnpricedModelError" in (out.record.error or "")
    assert sink.extractions == []
    assert [r.status for r in sink.runs] == ["failed"], "refused before any batch is pinned"


def test_fake_model_without_an_answer_is_a_recorded_failure_per_document(
    settings: Settings, sink: MemoryExtractSink
) -> None:
    out = run(settings, sink=sink, model=FakeModel(), prompt=prompt())
    assert out.status == "succeeded"
    assert out.record.quarantined == 6
    assert {q["reason"] for q in sink.quarantine} == {"ModelCallError"}
