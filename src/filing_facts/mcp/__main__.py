"""Run the MCP server over stdio against BigQuery and Vertex.

claude mcp add filing-facts -- uv run --directory /path/to/filing-facts python -m filing_facts.mcp
"""

from __future__ import annotations

import argparse
import logging
import sys

from filing_facts.config import Settings
from filing_facts.index.embedder import VertexEmbedder
from filing_facts.index.search import BigQuerySearchBackend, Searcher
from filing_facts.mcp.answer import Asker, GeminiGenerator
from filing_facts.mcp.data import BigQueryFilingData
from filing_facts.mcp.server import build_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="filing_facts.mcp", description=__doc__)
    parser.add_argument("--ask", metavar="QUESTION", help="Answer one question on stdout and exit.")
    parser.add_argument("--k", type=int, default=6, help="Passages to retrieve for --ask.")
    args = parser.parse_args(argv)
    # stdout is the protocol channel; anything chatty goes to stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)  # AFC advice on every call
    settings = Settings()
    if not settings.gcp_project:
        print(
            "FF_GCP_PROJECT is not set; pass it with `claude mcp add -e FF_GCP_PROJECT=...`",
            file=sys.stderr,
        )
        return 2
    embedder = VertexEmbedder(
        settings.embedding_model,
        project=settings.gcp_project,
        location=settings.vertex_location,
        dimensions=settings.embedding_dimensions,
    )
    searcher = Searcher(
        embedder,
        BigQuerySearchBackend(
            settings.gcp_project,
            settings.bq_dataset,
            settings.bq_staging_dataset,
            settings.bq_location,
        ),
    )
    generator = GeminiGenerator(
        settings.extract_model,
        project=settings.gcp_project,
        location=settings.vertex_location,
        labels={"app": "filing-facts", "stage": "6"},
    )
    data = BigQueryFilingData(
        settings.gcp_project, settings.bq_dataset, settings.bq_staging_dataset, settings.bq_location
    )
    asker = Asker(searcher, generator)
    if args.ask:
        answer = asker.ask(args.ask, k=args.k)
        print(answer.answer)
        print()
        for c in answer.citations:
            print(f"[{c['key']}] {c['company_name'] or c['company_number']}, {c['period_end']}")
        print(
            f"\n{answer.model}: {answer.input_tokens:,} in / {answer.output_tokens:,} out, "
            f"USD {answer.cost_usd:.5f}, {answer.latency_ms / 1000:.1f} s"
        )
        return 0
    build_server(data, searcher, asker).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
