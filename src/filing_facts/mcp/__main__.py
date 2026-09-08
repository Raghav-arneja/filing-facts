"""Run the MCP server over stdio against BigQuery and Vertex.

claude mcp add filing-facts -- uv run --directory /path/to/filing-facts python -m filing_facts.mcp
"""

from __future__ import annotations

import logging
import sys

from filing_facts.config import Settings
from filing_facts.index.embedder import VertexEmbedder
from filing_facts.index.search import BigQuerySearchBackend, Searcher
from filing_facts.mcp.answer import Asker, GeminiGenerator
from filing_facts.mcp.data import BigQueryFilingData
from filing_facts.mcp.server import build_server


def main() -> int:
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
    build_server(data, searcher, Asker(searcher, generator)).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
