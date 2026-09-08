"""CLI entrypoint for the index job. Same in the container, on Cloud Run, and locally.

python -m filing_facts.index                       # embed pending documents at FF_INDEX_CAP
python -m filing_facts.index --dry-run --fake --cap 5
python -m filing_facts.index --search "companies with negative net assets" --k 5
"""

from __future__ import annotations

import argparse
import sys

import structlog

from filing_facts.config import Settings
from filing_facts.index.embedder import Embedder, FakeEmbedder
from filing_facts.index.job import run
from filing_facts.logging import configure_logging
from filing_facts.storage.factory import build_backends
from filing_facts.telemetry import configure_tracing, flush


def _embedder(settings: Settings, fake: bool) -> Embedder:
    if fake:
        return FakeEmbedder()
    from filing_facts.index.embedder import VertexEmbedder

    return VertexEmbedder(
        settings.embedding_model,
        project=settings.gcp_project,
        location=settings.vertex_location,
        dimensions=settings.embedding_dimensions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="filing_facts.index", description=__doc__)
    parser.add_argument("--cap", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fake", action="store_true", help="Fake embedder; no calls, no cost.")
    parser.add_argument("--search", help="Instead of indexing, search for this question.")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args(argv)
    configure_logging()
    settings = Settings()
    configure_tracing("filing-facts-index", settings.gcp_project, enabled=not args.dry_run)
    log = structlog.get_logger(__name__)
    embedder = _embedder(settings, args.fake)

    if args.search:
        from filing_facts.index.search import BigQuerySearchBackend, Searcher

        backend = BigQuerySearchBackend(
            settings.gcp_project, settings.bq_dataset, "filing_facts_staging", settings.bq_location
        )
        for hit in Searcher(embedder, backend).search(args.search, k=args.k):
            who = hit.company_name or hit.company_number
            where = f"[{hit.document_id}#{hit.chunk_index}]"
            print(f"{hit.distance:.3f}  {who}  {hit.period_end}  {where}")
            print("   " + hit.text[:200].replace("\n", " | "))
        return 0

    b = build_backends(settings, dry_run=args.dry_run)
    outcome = run(settings, sink=b.index_sink, embedder=embedder, cap=args.cap)
    log.info(
        "index_end",
        status=outcome.status,
        chunks=outcome.record.chunks,
        cost_usd=outcome.record.cost_usd,
    )
    flush()
    return 1 if outcome.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
