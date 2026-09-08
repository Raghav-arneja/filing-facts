# Tools are registered by decorator and never called by name here.
# pyright: reportUnusedFunction=false
"""The MCP server: six tools over the pipeline's outputs. Built as a function of its
backends so tests run it in memory through the SDK's client; __main__ wires BigQuery."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.mcpserver import MCPServer

from filing_facts.index.search import Searcher
from filing_facts.mcp.answer import Asker
from filing_facts.mcp.data import FilingData

INSTRUCTIONS = """Filing Facts: UK Companies House accounts, parsed, extracted by an LLM and
scored against the XBRL tags. Document ids are <company number>_<period end YYYYMMDD>.
Start with search_filings or ask for open questions; get_facts gives the XBRL ground truth
for one filing and get_extraction shows what the model read versus that truth."""


def build_server(data: FilingData, searcher: Searcher, asker: Asker) -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer("filing-facts", instructions=INSTRUCTIONS)

    @server.tool(
        description="Semantic search over the text of every filing. Returns the nearest "
        "passages with company, period and a citation key."
    )
    def search_filings(question: str, k: int = 5) -> list[dict[str, Any]]:
        hits = searcher.search(question, k=max(1, min(k, 20)))
        return [asdict(h) | {"key": f"{h.document_id}#{h.chunk_index}"} for h in hits]

    @server.tool(
        description="Answer a question from the filings with citations. Costs a fraction of "
        "a cent (Gemini Flash-Lite); the response says exactly how much."
    )
    def ask(question: str, k: int = 6) -> dict[str, Any]:
        return asdict(asker.ask(question, k=max(1, min(k, 12))))

    @server.tool(
        description="XBRL ground truth for one filing: every numeric fact the filer tagged, "
        "with period, unit and dimensions."
    )
    def get_facts(document_id: str) -> dict[str, Any]:
        doc = data.document(document_id)
        if doc is None:
            return {"error": f"no document {document_id}"}
        return {"document": doc, "facts": data.facts(document_id)}

    @server.tool(
        description="What the LLM extracted for one filing, next to the tagged truth and the "
        "harness verdict per field. Filter by model, or by prompt version such as 'v2' "
        "(prompt ids are the version plus a content hash)."
    )
    def get_extraction(
        document_id: str, model: str | None = None, prompt_id: str | None = None
    ) -> dict[str, Any]:
        rows = data.extraction(document_id, model, prompt_id)
        if not rows and data.document(document_id) is None:
            return {"error": f"no document {document_id}"}
        return {"document_id": document_id, "values": rows}

    @server.tool(
        description="Precision, recall, error types and cost per 1,000 filings for every "
        "model and prompt the harness has scored, by concept."
    )
    def compare_models() -> list[dict[str, Any]]:
        return data.model_metrics()

    @server.tool(
        description="Counts and last successful run per stage, open quarantine, and total "
        "model spend."
    )
    def pipeline_status() -> dict[str, Any]:
        return data.status()

    return server
