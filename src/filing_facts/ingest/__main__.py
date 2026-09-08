"""CLI entrypoint. Identical in the container, on Cloud Run, and on a laptop.

python -m filing_facts.ingest                 # today's file, real GCS + BigQuery via ADC
python -m filing_facts.ingest --date 2026-09-01
python -m filing_facts.ingest --dry-run       # local filesystem backends under ./data
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import structlog

from filing_facts.config import Settings
from filing_facts.ingest.job import run
from filing_facts.logging import configure_logging
from filing_facts.storage.factory import build_backends

LONDON = ZoneInfo("Europe/London")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="filing_facts.ingest", description=__doc__)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=datetime.now(LONDON).date(),
        help="Publish date of the daily ZIP (YYYY-MM-DD). Defaults to today in Europe/London.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use local filesystem backends instead of GCS and BigQuery.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    settings = Settings()
    backends = build_backends(settings, dry_run=args.dry_run)
    store, runlog = backends.store, backends.runlog
    log = structlog.get_logger(__name__)
    log.info("ingest_start", date=args.date.isoformat(), dry_run=args.dry_run)

    with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        outcome = run(
            settings,
            client=client,
            store=store,
            runlog=runlog,
            target_date=args.date,
            publisher=backends.publisher,
        )

    log.info("ingest_end", status=outcome.status)
    return 1 if outcome.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
