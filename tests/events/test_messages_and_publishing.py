from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from filing_facts.config import Settings
from filing_facts.events.messages import LifecycleEvent
from filing_facts.events.publisher import MemoryPublisher, NullPublisher
from filing_facts.parse.job import run as parse_run
from filing_facts.storage.memory import MemoryParseSink, MemoryRawStore
from tests.parse.test_job import SOURCE, build_zip, seed


def test_lifecycle_event_rejects_unknown_fields_and_versions() -> None:
    now = datetime.now(UTC)
    LifecycleEvent(event="ingested", source_key="a.zip", run_id="r", occurred_at=now)
    with pytest.raises(ValidationError):
        LifecycleEvent(event="ingested", source_key="a.zip", run_id="r", occurred_at=now, extra=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        LifecycleEvent.model_validate(
            {
                "version": 2,
                "event": "ingested",
                "source_key": "a",
                "run_id": "r",
                "occurred_at": now,
            }
        )
    with pytest.raises(ValidationError):
        LifecycleEvent(event="ingested", source_key="", run_id="r", occurred_at=now)


def test_parse_publishes_one_document_event_per_document_and_one_lifecycle_event(
    tmp_path: Path,
) -> None:
    store, sink, pub = MemoryRawStore(), MemoryParseSink(), MemoryPublisher()
    seed(store, build_zip(), tmp_path)
    out = parse_run(
        Settings(parse_cap=100), store=store, sink=sink, source_key=SOURCE, publisher=pub
    )
    assert out.status == "succeeded"
    assert len(pub.documents) == len(sink.documents)
    assert {d.document_id for d in pub.documents} == {d["document_id"] for d in sink.documents}
    assert [e.event for e in pub.lifecycle] == ["parsed"]
    assert pub.lifecycle[0].batch_id == out.record.batch_id


def test_replay_publishes_nothing(tmp_path: Path) -> None:
    store, sink, pub = MemoryRawStore(), MemoryParseSink(), MemoryPublisher()
    seed(store, build_zip(), tmp_path)
    parse_run(Settings(parse_cap=100), store=store, sink=sink, source_key=SOURCE, publisher=pub)
    parse_run(Settings(parse_cap=100), store=store, sink=sink, source_key=SOURCE, publisher=pub)
    assert len(pub.lifecycle) == 1, "a skipped replay is not a new outcome"


def test_null_publisher_counts_without_publishing() -> None:
    assert NullPublisher().publish_documents([]) == 0


def test_extracted_events_need_no_source_key_but_ingested_and_parsed_do() -> None:
    now = datetime.now(UTC)
    LifecycleEvent(
        event="extracted", run_id="r", occurred_at=now, batch_id="b", model="m", prompt_id="p"
    )
    with pytest.raises(ValidationError, match="source_key"):
        LifecycleEvent(event="parsed", run_id="r", occurred_at=now, batch_id="b")
    with pytest.raises(ValidationError, match="batch_id"):
        LifecycleEvent(event="extracted", run_id="r", occurred_at=now)


class ExplodingPublisher(MemoryPublisher):
    def publish_lifecycle(self, event: LifecycleEvent) -> None:
        raise RuntimeError("pubsub down")


def test_publish_failure_never_changes_a_succeeded_outcome(tmp_path: Path) -> None:
    store, sink = MemoryRawStore(), MemoryParseSink()
    seed(store, build_zip(), tmp_path)
    out = parse_run(
        Settings(parse_cap=100),
        store=store,
        sink=sink,
        source_key=SOURCE,
        publisher=ExplodingPublisher(),
    )
    assert out.status == "succeeded"
    assert [r.status for r in sink.runs] == ["started", "succeeded"], "no failed row appended"


def test_half_configured_topics_are_refused() -> None:
    from filing_facts.storage.factory import _publisher  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(SystemExit):
        _publisher(Settings(lifecycle_topic="a", documents_topic=""))
