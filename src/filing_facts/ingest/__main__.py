"""CLI entrypoint. Identical in the container, on Cloud Run, and on a laptop.

python -m filing_facts.ingest                 # today's file, real GCS + BigQuery via ADC
python -m filing_facts.ingest --date 2026-09-01
python -m filing_facts.ingest --dry-run       # local filesystem backends under ./data
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import structlog

from filing_facts.config import Settings
from filing_facts.ingest.job import run
from filing_facts.logging import configure_logging
from filing_facts.storage.protocols import RawStore, RunLog

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


def _backends(settings: Settings, dry_run: bool) -> tuple[RawStore, RunLog]:
    if dry_run:
        from filing_facts.storage.local import JsonlRunLog, LocalRawStore

        root = Path(settings.local_data_dir)
        return LocalRawStore(root / "raw"), JsonlRunLog(root / "ingest_runs.jsonl")

    missing = [n for n in ("gcp_project", "raw_bucket") if not getattr(settings, n)]
    if missing:
        names = ", ".join(f"FF_{m.upper()}" for m in missing)
        raise SystemExit(f"missing required environment: {names} (or pass --dry-run)")

    from google.cloud import bigquery, storage

    from filing_facts.storage.bigquery import BigQueryRunLog
    from filing_facts.storage.gcs import GcsRawStore

    store = GcsRawStore(
        storage.Client(project=settings.gcp_project), settings.raw_bucket, settings.raw_prefix
    )
    runlog = BigQueryRunLog(
        bigquery.Client(project=settings.gcp_project, location=settings.bq_location),
        settings.bq_dataset,
        settings.bq_table,
        settings.bq_location,
    )
    return store, runlog


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    settings = Settings()
    store, runlog = _backends(settings, args.dry_run)
    log = structlog.get_logger(__name__)
    log.info("ingest_start", date=args.date.isoformat(), dry_run=args.dry_run)

    with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        outcome = run(settings, client=client, store=store, runlog=runlog, target_date=args.date)

    log.info("ingest_end", status=outcome.status)
    return 1 if outcome.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
