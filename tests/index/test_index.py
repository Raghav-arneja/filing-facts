from __future__ import annotations

from itertools import pairwise

from filing_facts.config import Settings
from filing_facts.extract.rows import DocumentText
from filing_facts.index.chunker import chunk_text
from filing_facts.index.embedder import FakeEmbedder
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
