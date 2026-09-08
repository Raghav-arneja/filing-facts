"""CLI entrypoint for the parse job. Same in the container, on Cloud Run, and locally.

python -m filing_facts.parse                          # every ingested ZIP pending at this cap
python -m filing_facts.parse --source-key Accounts_Bulk_Data-2026-09-02.zip
python -m filing_facts.parse --dry-run --cap 20       # local filesystem backends under ./data
"""

from __future__ import annotations

import argparse
import sys

import structlog

from filing_facts.config import Settings
from filing_facts.logging import configure_logging
from filing_facts.parse.job import pending_source_keys, run
from filing_facts.storage.factory import build_backends


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="filing_facts.parse", description=__doc__)
    parser.add_argument("--source-key", help="Parse this ZIP only. Default: all pending.")
    parser.add_argument("--cap", type=int, help="Override FF_PARSE_CAP for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Local filesystem backends.")
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="With --source-key: purge and reprocess that source with the current parser.",
    )
    args = parser.parse_args(argv)
    if args.reparse and not args.source_key:
        parser.error("--reparse requires --source-key")
    return args


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    settings = Settings()
    cap = settings.parse_cap if args.cap is None else args.cap
    b = build_backends(settings, dry_run=args.dry_run)
    log = structlog.get_logger(__name__)

    keys = [args.source_key] if args.source_key else pending_source_keys(b.runlog, b.sink, cap)
    log.info("parse_start", keys=keys, cap=cap, dry_run=args.dry_run)
    failed = 0
    for key in keys:
        outcome = run(
            settings,
            store=b.store,
            sink=b.sink,
            source_key=key,
            cap=cap,
            reparse=args.reparse,
            publisher=b.publisher,
        )
        failed += outcome.status == "failed"
    log.info("parse_end", keys=len(keys), failed=failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
