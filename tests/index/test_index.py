from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from types import SimpleNamespace

import pytest

from filing_facts.config import Settings
from filing_facts.extract.rows import DocumentText
from filing_facts.index.chunker import chunk_text
from filing_facts.index.embedder import Embedding, FakeEmbedder, TaskType
from filing_facts.index.job import run
from filing_facts.index.search import MemorySearchBackend, Searcher
from filing_facts.storage.memory import MemoryIndexSink


def test_chunks_split_on_lines_with_overlap_and_stable_indexes() -> None:
    text = "\n".join(f"Row {i} | {i * 100} | {i * 90}" for i in range(40))
    chunks = chunk_text(text, max_chars=120, overlap_lines=2)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= 120 for c in chunks)
    assert all("\n" not in c.text[:1] for c in chunks)
    for a, b in pairwise(chunks):
        assert b.start_line == a.end_line - 2, "two lines of overlap"
    assert chunks[-1].end_line == 40
    assert chunk_text("   \n\n") == []
    assert len(chunk_text("x" * 5000, max_chars=100)) == 1, "one oversize line is still one chunk"


DOCS = [
    DocumentText(
        "00000001_20251231",
        "s.zip",
        "Cash at bank | 19,784 | 12,210\n"
        "Creditors: amounts falling due within one year | 7,128 | 7,105\n"
        "Net assets | 15,315 | 8,553",
    ),
    DocumentText(
        "00000002_20251231",
        "s.zip",
        "Average number of employees | 3 | 3\nDirectors: Alice Example\nThe company is dormant",
    ),
    DocumentText(
        "00000003_20251231",
        "s.zip",
        "Fixed assets | 2,204 | 1,500\nTotal assets less current liabilities | 51,718 | 49,096",
    ),
]


def test_index_job_embeds_every_chunk_and_is_idempotent() -> None:
    sink, emb = MemoryIndexSink(DOCS), FakeEmbedder()
    settings = Settings(index_cap=10, chunk_chars=80, chunk_overlap_lines=1)
    out = run(settings, sink=sink, embedder=emb)
    assert out.status == "succeeded"
    assert out.record.indexed == 3
    assert out.record.chunks == len(sink.chunks) > 3
    assert all(len(c["embedding"]) == emb.dimensions for c in sink.chunks)
    assert out.record.tokens == sum(c["tokens"] for c in sink.chunks)
    again = run(settings, sink=sink, embedder=emb)
    assert again.status == "skipped_existing"
    assert len(sink.chunks) == out.record.chunks


def test_search_returns_the_passage_that_shares_the_words() -> None:
    sink, emb = MemoryIndexSink(DOCS), FakeEmbedder()
    run(Settings(index_cap=10, chunk_chars=80, chunk_overlap_lines=0), sink=sink, embedder=emb)
    docs = {
        d.document_id: {"company_number": d.document_id[:8], "period_end": "2025-12-31"}
        for d in DOCS
    }
    hits = Searcher(emb, MemorySearchBackend(sink.chunks, docs)).search(
        "average number of employees", k=2
    )
    assert hits[0].document_id == "00000002_20251231"
    assert "employees" in hits[0].text
    assert hits[0].distance <= hits[1].distance


def test_unpriced_real_embedding_model_is_refused() -> None:
    class Odd(FakeEmbedder):
        @property
        def model_id(self) -> str:
            return "mystery-embed"

    out = run(Settings(), sink=MemoryIndexSink(DOCS), embedder=Odd())
    assert out.status == "failed"
    assert "unpriced" in (out.record.error or "")


class FlakyEmbedder(FakeEmbedder):
    """Fails on the third document's chunks, once."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def embed(self, texts: Sequence[str], task: TaskType) -> list[Embedding]:
        self.calls += 1
        if self.calls == 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return super().embed(texts, task)


def test_crash_keeps_flushed_parts_and_resume_pays_only_for_the_rest() -> None:
    sink, emb = MemoryIndexSink(DOCS), FlakyEmbedder()
    settings = Settings(index_cap=10, chunk_chars=80, chunk_overlap_lines=0, index_flush_docs=1)
    first = run(settings, sink=sink, embedder=emb)
    assert first.status == "failed"
    assert first.record.indexed == 2, "the two documents before the crash were counted"
    assert first.record.tokens > 0, "spend before the failure is recorded, not zeroed"
    assert sink.processed_ids(emb.model_id) == {"00000001_20251231", "00000002_20251231"}
    second = run(settings, sink=sink, embedder=emb)
    assert second.status == "succeeded"
    assert second.record.batch_id == first.record.batch_id, "the pinned batch was resumed"
    assert second.record.indexed == 1, "only the document that failed was embedded again"
    assert len({(c["document_id"], c["chunk_index"]) for c in sink.chunks}) == len(sink.chunks)
    assert run(settings, sink=sink, embedder=emb).status == "skipped_existing"


def test_vertex_embedder_retries_rate_limits_then_gives_up() -> None:
    from google.genai import errors

    from filing_facts.index.embedder import VertexEmbedder

    class Boom:
        def __init__(self, fail: int) -> None:
            self.left, self.calls = fail, 0

        def embed_content(self, **_: object) -> object:
            self.calls += 1
            if self.left:
                self.left -= 1
                raise errors.APIError(429, {"error": {"message": "slow down"}})
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2], statistics=None)])

    sleeps: list[float] = []
    e = VertexEmbedder.__new__(VertexEmbedder)
    e._model_id, e._dims, e._threads, e._max_attempts, e._sleep = "m", 2, 1, 3, sleeps.append  # pyright: ignore[reportPrivateUsage]
    boom = Boom(fail=2)
    e._client = SimpleNamespace(models=boom)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert e.embed(["x"], "RETRIEVAL_QUERY")[0].vector == [0.1, 0.2]
    assert sleeps == [1.0, 2.0]
    e._client = SimpleNamespace(models=Boom(fail=5))  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    with pytest.raises(errors.APIError):
        e.embed(["x"], "RETRIEVAL_QUERY")
