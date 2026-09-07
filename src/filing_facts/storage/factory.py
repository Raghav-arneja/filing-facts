"""Build the storage backends once, for every CLI. Dry-run swaps GCP for the filesystem."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from filing_facts.config import Settings
from filing_facts.storage.protocols import ExtractSink, ParseSink, RawStore, RunLog


@dataclass(frozen=True)
class Backends:
    store: RawStore
    runlog: RunLog
    sink: ParseSink
    extract_sink: ExtractSink


def build_backends(settings: Settings, *, dry_run: bool) -> Backends:
    if dry_run:
        from filing_facts.storage.local import (
            JsonlExtractSink,
            JsonlParseSink,
            JsonlRunLog,
            LocalRawStore,
        )

        root = Path(settings.local_data_dir)
        return Backends(
            store=LocalRawStore(root / "raw"),
            runlog=JsonlRunLog(root / "ingest_runs.jsonl"),
            sink=JsonlParseSink(root / "parsed"),
            extract_sink=JsonlExtractSink(root / "parsed", root / "extract"),
        )

    missing = [n for n in ("gcp_project", "raw_bucket") if not getattr(settings, n)]
    if missing:
        names = ", ".join(f"FF_{m.upper()}" for m in missing)
        raise SystemExit(f"missing required environment: {names} (or pass --dry-run)")

    from google.cloud import bigquery, storage

    from filing_facts.storage.bigquery import BigQueryExtractSink, BigQueryParseSink, BigQueryRunLog
    from filing_facts.storage.gcs import GcsRawStore

    bq = bigquery.Client(project=settings.gcp_project, location=settings.bq_location)
    return Backends(
        store=GcsRawStore(
            storage.Client(project=settings.gcp_project), settings.raw_bucket, settings.raw_prefix
        ),
        runlog=BigQueryRunLog(bq, settings.bq_dataset, settings.bq_table, settings.bq_location),
        sink=BigQueryParseSink(
            bq,
            settings.bq_dataset,
            settings.bq_location,
            documents=settings.documents_table,
            facts=settings.facts_table,
            quarantine=settings.quarantine_table,
            parse_runs=settings.parse_runs_table,
        ),
        extract_sink=BigQueryExtractSink(
            bq,
            settings.bq_dataset,
            settings.bq_location,
            documents=settings.documents_table,
            quarantine=settings.quarantine_table,
            extractions=settings.extractions_table,
            extract_runs=settings.extract_runs_table,
        ),
    )
