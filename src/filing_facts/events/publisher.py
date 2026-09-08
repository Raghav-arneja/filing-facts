# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Publishing boundary. Jobs publish through this; tests use the memory implementation and
dry runs the null one. Publishing happens after the ledger's success row: a lost event can
be replayed from the ledger, but an event for something the ledger does not record cannot
be trusted."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from filing_facts.events.messages import DocumentEvent, LifecycleEvent

log = structlog.get_logger(__name__)


def publish_after_success(
    publisher: EventPublisher | None,
    lifecycle: LifecycleEvent,
    documents: Iterable[DocumentEvent] = (),
) -> bool:
    """Publish once the ledger already says the run succeeded. Never raises: the ledger is
    the source of truth and a lost event is replayable from it, so a publish failure is
    logged loudly and the run's outcome stands. Returns whether everything was published."""
    pub = publisher or NullPublisher()
    try:
        pub.publish_documents(documents)
        pub.publish_lifecycle(lifecycle)
        return True
    except Exception as exc:
        log.error(
            "event_publish_failed",
            kind=lifecycle.event,
            source_key=lifecycle.source_key,
            run_id=lifecycle.run_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False


class EventPublisher(Protocol):
    def publish_lifecycle(self, event: LifecycleEvent) -> None: ...

    def publish_documents(self, events: Iterable[DocumentEvent]) -> int: ...


@dataclass
class MemoryPublisher:
    lifecycle: list[LifecycleEvent] = field(default_factory=list[LifecycleEvent])
    documents: list[DocumentEvent] = field(default_factory=list[DocumentEvent])

    def publish_lifecycle(self, event: LifecycleEvent) -> None:
        self.lifecycle.append(event)

    def publish_documents(self, events: Iterable[DocumentEvent]) -> int:
        before = len(self.documents)
        self.documents.extend(events)
        return len(self.documents) - before


class NullPublisher:
    """Dry runs and environments with no topics configured: log and move on."""

    def publish_lifecycle(self, event: LifecycleEvent) -> None:
        log.info("event_not_published", kind=event.event, source_key=event.source_key)

    def publish_documents(self, events: Iterable[DocumentEvent]) -> int:
        n = sum(1 for _ in events)
        log.info("document_events_not_published", count=n)
        return n


class PubSubPublisher:
    """Google Pub/Sub. Lifecycle messages carry the event kind as an attribute so a
    subscription can filter without decoding; document messages are batched by the client."""

    def __init__(self, project: str, lifecycle_topic: str, documents_topic: str) -> None:
        from google.cloud import pubsub_v1

        self._client = pubsub_v1.PublisherClient()
        self._lifecycle = self._client.topic_path(project, lifecycle_topic)
        self._documents = self._client.topic_path(project, documents_topic)

    def publish_lifecycle(self, event: LifecycleEvent) -> None:
        attributes = {"event": event.event, "version": str(event.version)}
        if event.source_key:
            attributes["source_key"] = event.source_key
        future = self._client.publish(
            self._lifecycle, event.model_dump_json().encode(), **attributes
        )
        future.result(timeout=30)
        log.info("event_published", kind=event.event, source_key=event.source_key)

    def publish_documents(self, events: Iterable[DocumentEvent]) -> int:
        futures = [
            self._client.publish(self._documents, e.model_dump_json().encode(), version="1")
            for e in events
        ]
        for f in futures:
            f.result(timeout=60)
        log.info("document_events_published", count=len(futures))
        return len(futures)
