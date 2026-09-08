from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.client import Client

from filing_facts.config import Settings
from filing_facts.extract.rows import DocumentText
from filing_facts.index.embedder import FakeEmbedder
from filing_facts.index.job import run
from filing_facts.index.search import MemorySearchBackend, Searcher
from filing_facts.mcp.answer import Asker, FakeGenerator, render_passages
from filing_facts.mcp.data import MemoryFilingData
from filing_facts.mcp.server import build_server
from filing_facts.storage.memory import MemoryIndexSink

DOC = "00000002_20251231"
DOCS = [
    DocumentText(
        "00000001_20251231", "s.zip", "Cash at bank | 19,784 | 12,210\nNet assets | 15,315 | 8,553"
    ),
    DocumentText(DOC, "s.zip", "Average number of employees | 3 | 3\nThe company is dormant"),
]
DOCUMENTS = [
    {"document_id": d.document_id, "company_number": d.document_id[:8], "period_end": "2025-12-31"}
    for d in DOCS
]
FACTS = [{"document_id": DOC, "concept": "AverageNumberEmployeesDuringPeriod", "value": 3.0}]
VALUES = [
    {
        "document_id": DOC,
        "model": "m1",
        "prompt_id": "v2",
        "field": "employees",
        "value": 3,
        "outcome": "correct",
    },
    {
        "document_id": DOC,
        "model": "m2",
        "prompt_id": "v1",
        "field": "employees",
        "value": 30,
        "outcome": "wrong",
    },
]
METRICS = [{"model": "m1", "prompt_id": "v2", "concept": "all", "recall": 0.95}]
STATUS = {"documents": 2, "chunks": 4, "quarantined": 0}


def make_server() -> Any:
    sink, emb = MemoryIndexSink(DOCS), FakeEmbedder()
    run(Settings(index_cap=10, chunk_chars=80, chunk_overlap_lines=0), sink=sink, embedder=emb)
    searcher = Searcher(
        emb, MemorySearchBackend(sink.chunks, {d["document_id"]: d for d in DOCUMENTS})
    )
    data = MemoryFilingData(DOCUMENTS, FACTS, VALUES, METRICS, STATUS)
    return build_server(data, searcher, Asker(searcher, FakeGenerator()))


async def call(name: str, **args: Any) -> Any:
    async with Client(make_server()) as client:
        result = await client.call_tool(name, args)
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(getattr(result.content[0], "text", "null"))


@pytest.mark.anyio
async def test_lists_the_six_tools() -> None:
    async with Client(make_server()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {
        "search_filings",
        "ask",
        "get_facts",
        "get_extraction",
        "compare_models",
        "pipeline_status",
    }


@pytest.mark.anyio
async def test_search_returns_hits_with_citation_keys() -> None:
    out = await call("search_filings", question="average number of employees", k=2)
    hits = out["result"]
    assert hits[0]["document_id"] == DOC
    assert hits[0]["key"] == f"{DOC}#{hits[0]['chunk_index']}"


@pytest.mark.anyio
async def test_ask_grounds_the_answer_in_a_retrieved_passage() -> None:
    out = await call("ask", question="average number of employees", k=2)
    assert out["citations"], "the fake generator cites the first passage it was given"
    assert out["citations"][0]["document_id"] == DOC
    assert out["citations"][0]["key"] in out["answer"]
    assert out["unsupported_citations"] == []
    assert out["cost_usd"] == 0.0
    assert out["model"] == "fake-generator"


@pytest.mark.anyio
async def test_facts_and_extraction_join_on_the_document() -> None:
    facts = await call("get_facts", document_id=DOC)
    assert facts["document"]["company_number"] == "00000002"
    assert facts["facts"][0]["concept"] == "AverageNumberEmployeesDuringPeriod"
    both = await call("get_extraction", document_id=DOC)
    assert {v["model"] for v in both["values"]} == {"m1", "m2"}
    one = await call("get_extraction", document_id=DOC, model="m2")
    assert [v["outcome"] for v in one["values"]] == ["wrong"]
    missing = await call("get_facts", document_id="nope")
    assert "error" in missing


@pytest.mark.anyio
async def test_metrics_and_status_pass_through() -> None:
    assert (await call("compare_models"))["result"] == METRICS
    assert (await call("pipeline_status"))["documents"] == 2


def test_passages_carry_the_key_the_prompt_asks_the_model_to_cite() -> None:
    from filing_facts.index.search import Hit

    text = render_passages(
        [Hit(DOC, 1, "00000002", "Acme Ltd", "2025-12-31", "Net assets | 1", 0.1)]
    )
    assert text.startswith(f"[{DOC}#1] Acme Ltd, period ending 2025-12-31\nNet assets | 1")
