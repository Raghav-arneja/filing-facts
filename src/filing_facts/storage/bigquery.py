"""BigQuery run ledger. Batch load jobs only; streaming inserts are never used.

Idempotency comes from the load job id: BigQuery refuses a second job with the same id in
the same project, so a replayed success cannot append a second row even if two runs race.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from google.api_core.exceptions import Conflict
from google.cloud import bigquery

from filing_facts.storage.memory import dedupe_key
from filing_facts.storage.protocols import RunRecord

_JOB_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class BigQueryRunLog:
    def __init__(self, client: bigquery.Client, dataset: str, table: str, location: str) -> None:
        self._client = client
        self._table_id = f"{client.project}.{dataset}.{table}"
        self._location = location

    def has_succeeded(self, source_key: str) -> bool:
        query = (
            f"SELECT 1 FROM `{self._table_id}` "  # noqa: S608 - table id is config, not user input
            "WHERE source_key = @source_key AND status = 'succeeded' LIMIT 1"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("source_key", "STRING", source_key)]
        )
        rows = self._client.query(query, job_config=job_config, location=self._location).result()
        return next(iter(rows), None) is not None

    def record(self, record: RunRecord) -> bool:
        row: dict[str, Any] = asdict(record)
        row["started_at"] = record.started_at.isoformat()
        row["finished_at"] = record.finished_at.isoformat()
        job_id = _JOB_ID_SAFE.sub("_", dedupe_key(record))
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        try:
            job = self._client.load_table_from_json(
                [row],
                self._table_id,
                job_id=job_id,
                location=self._location,
                job_config=job_config,
            )
        except Conflict:
            return False
        job.result()
        return True
