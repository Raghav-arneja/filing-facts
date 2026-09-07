# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""BigQuery run ledger. Batch load jobs only; streaming inserts are never used.

Idempotency comes from the load job id: BigQuery refuses a second job with the same id in
the same project, so a replayed success cannot append a second row even if two runs race.
"""

from __future__ import annotations

import json
import re
from typing import Any

from google.api_core.exceptions import Conflict
from google.cloud import bigquery

from filing_facts.extract.rows import DocumentText, ExtractRunRecord, extract_run_key
from filing_facts.parse.rows import ParseRunRecord, parse_run_key, to_row
from filing_facts.parse.spool import Spool
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

    def succeeded_keys(self) -> list[str]:
        query = (
            f"SELECT source_key FROM `{self._table_id}` "  # noqa: S608
            "WHERE status = 'succeeded' GROUP BY source_key ORDER BY MIN(finished_at)"
        )
        rows = self._client.query(query, location=self._location).result()
        return [str(r["source_key"]) for r in rows]

    def record(self, record: RunRecord) -> bool:
        row = to_row(record)
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


_MAX_RETRY_SUFFIX = 5


class LoadFailedError(Exception):
    """A load job with our deterministic id exists and ended in error, and retries ran out."""


def _load_file(
    client: bigquery.Client, table_id: str, location: str, job_id: str, path: str
) -> bool:
    """Batch load from an NDJSON file under a deterministic job id.

    A job id is reserved by BigQuery whatever the job's outcome, so a Conflict is not proof
    that the data landed. On Conflict the prior job is inspected: done without error means
    already loaded (return False); still running means wait for it; ended in error means
    retry under a `-r<n>` suffix so a fixed replay is not blocked forever.
    """
    base = _JOB_ID_SAFE.sub("_", job_id)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    for attempt in range(_MAX_RETRY_SUFFIX + 1):
        this_id = base if attempt == 0 else f"{base}-r{attempt}"
        try:
            with open(path, "rb") as fh:
                job = client.load_table_from_file(
                    fh, table_id, job_id=this_id, location=location, job_config=job_config
                )
        except Conflict:
            prior = client.get_job(this_id, location=location)
            if prior.state != "DONE":
                prior.result()
                return False
            if prior.error_result is None:
                return False
            continue  # that attempt failed; try the next suffix
        job.result()
        return True
    raise LoadFailedError(f"{base}: {_MAX_RETRY_SUFFIX} failed prior attempts")


class BigQueryParseSink:
    def __init__(
        self,
        client: bigquery.Client,
        dataset: str,
        location: str,
        *,
        documents: str,
        facts: str,
        quarantine: str,
        parse_runs: str,
    ) -> None:
        self._client = client
        self._location = location
        base = f"{client.project}.{dataset}"
        self._tables = {
            "documents": f"{base}.{documents}",
            "facts": f"{base}.{facts}",
            "quarantine": f"{base}.{quarantine}",
            "parse_runs": f"{base}.{parse_runs}",
        }

    def _query(self, sql: str, **params: str) -> list[Any]:
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in params.items()
            ]
        )
        return list(
            self._client.query(sql, job_config=job_config, location=self._location).result()
        )

    def processed_members(self, source_key: str) -> set[str]:
        t = self._tables
        sql = (
            f"SELECT member_name FROM `{t['documents']}` WHERE source_key = @k "  # noqa: S608
            f"UNION DISTINCT SELECT member_name FROM `{t['quarantine']}` WHERE source_key = @k"
        )
        return {str(r["member_name"]) for r in self._query(sql, k=source_key)}

    def open_batch(self, source_key: str) -> tuple[str, list[str]] | None:
        t = self._tables["parse_runs"]
        sql = (
            f"SELECT batch_id, members FROM `{t}` s "  # noqa: S608
            "WHERE source_key = @k AND status = 'started' AND NOT EXISTS ("
            f"  SELECT 1 FROM `{t}` d WHERE d.source_key = @k AND d.status = 'succeeded' "
            "  AND d.batch_id = s.batch_id) "
            "ORDER BY started_at DESC LIMIT 1"
        )
        rows = self._query(sql, k=source_key)
        if not rows:
            return None
        return str(rows[0]["batch_id"]), [str(m) for m in rows[0]["members"]]

    def succeeded_caps(self) -> dict[str, int]:
        t = self._tables["parse_runs"]
        sql = (
            f"SELECT source_key, MAX(cap) AS cap FROM `{t}` "  # noqa: S608
            "WHERE status IN ('succeeded', 'skipped_existing') GROUP BY source_key"
        )
        return {str(r["source_key"]): int(r["cap"]) for r in self._query(sql)}

    def _write(self, table: str, batch_id: str, spool: Spool) -> bool:
        spool.close()
        if spool.count == 0:
            return True
        return _load_file(
            self._client,
            self._tables[table],
            self._location,
            f"parse-{table}-{batch_id}",
            str(spool.path),
        )

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool:
        return self._write("quarantine", batch_id, spool)

    def write_facts(self, batch_id: str, spool: Spool) -> bool:
        return self._write("facts", batch_id, spool)

    def write_documents(self, batch_id: str, spool: Spool) -> bool:
        return self._write("documents", batch_id, spool)

    def record_run(self, record: ParseRunRecord) -> bool:
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        job_id = _JOB_ID_SAFE.sub("_", parse_run_key(record))
        try:
            job = self._client.load_table_from_json(
                [json.loads(json.dumps(to_row(record)))],
                self._tables["parse_runs"],
                job_id=job_id,
                location=self._location,
                job_config=job_config,
            )
        except Conflict:
            return False
        job.result()
        return True


class BigQueryExtractSink:
    def __init__(
        self,
        client: bigquery.Client,
        dataset: str,
        location: str,
        *,
        documents: str,
        quarantine: str,
        extractions: str,
        extract_runs: str,
    ) -> None:
        self._client = client
        self._location = location
        base = f"{client.project}.{dataset}"
        self._t = {
            "documents": f"{base}.{documents}",
            "quarantine": f"{base}.{quarantine}",
            "extractions": f"{base}.{extractions}",
            "extract_runs": f"{base}.{extract_runs}",
        }

    def _query(self, sql: str, params: list[Any]) -> list[Any]:
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        return list(
            self._client.query(sql, job_config=job_config, location=self._location).result()
        )

    def pending_documents(self, model: str, prompt_id: str, cap: int) -> list[DocumentText]:
        t = self._t
        sql = (
            f"SELECT d.document_id, d.source_key, d.text FROM `{t['documents']}` d "  # noqa: S608
            f"WHERE NOT EXISTS (SELECT 1 FROM `{t['extractions']}` e "
            "  WHERE e.document_id = d.document_id AND e.model = @model AND e.prompt_id = @prompt) "
            f"AND NOT EXISTS (SELECT 1 FROM `{t['quarantine']}` q "
            "  WHERE q.document_id = d.document_id AND q.stage = 'extract' "
            "  AND q.model = @model AND q.prompt_id = @prompt) "
            "ORDER BY TO_HEX(SHA256(d.document_id)) LIMIT @cap"
        )
        rows = self._query(
            sql,
            [
                bigquery.ScalarQueryParameter("model", "STRING", model),
                bigquery.ScalarQueryParameter("prompt", "STRING", prompt_id),
                bigquery.ScalarQueryParameter("cap", "INT64", cap),
            ],
        )
        return [
            DocumentText(str(r["document_id"]), str(r["source_key"]), str(r["text"])) for r in rows
        ]

    def processed_ids(self, model: str, prompt_id: str) -> set[str]:
        t = self._t
        sql = (
            f"SELECT document_id FROM `{t['extractions']}` "  # noqa: S608
            "WHERE model = @model AND prompt_id = @prompt "
            f"UNION DISTINCT SELECT document_id FROM `{t['quarantine']}` "
            "WHERE stage = 'extract' AND model = @model AND prompt_id = @prompt"
        )
        rows = self._query(
            sql,
            [
                bigquery.ScalarQueryParameter("model", "STRING", model),
                bigquery.ScalarQueryParameter("prompt", "STRING", prompt_id),
            ],
        )
        return {str(r["document_id"]) for r in rows}

    def documents_by_id(self, ids: list[str]) -> list[DocumentText]:
        sql = (
            f"SELECT document_id, source_key, text FROM `{self._t['documents']}` "  # noqa: S608
            "WHERE document_id IN UNNEST(@ids)"
        )
        rows = self._query(sql, [bigquery.ArrayQueryParameter("ids", "STRING", ids)])
        return [
            DocumentText(str(r["document_id"]), str(r["source_key"]), str(r["text"])) for r in rows
        ]

    def open_batch(self, model: str, prompt_id: str) -> tuple[str, list[str]] | None:
        t = self._t["extract_runs"]
        sql = (
            f"SELECT batch_id, document_ids FROM `{t}` s "  # noqa: S608
            "WHERE model = @model AND prompt_id = @prompt AND status = 'started' AND NOT EXISTS ("
            f"  SELECT 1 FROM `{t}` d WHERE d.model = @model AND d.prompt_id = @prompt "
            "  AND d.status = 'succeeded' AND d.batch_id = s.batch_id) "
            "ORDER BY started_at DESC LIMIT 1"
        )
        rows = self._query(
            sql,
            [
                bigquery.ScalarQueryParameter("model", "STRING", model),
                bigquery.ScalarQueryParameter("prompt", "STRING", prompt_id),
            ],
        )
        if not rows:
            return None
        return str(rows[0]["batch_id"]), [str(i) for i in rows[0]["document_ids"]]

    def _write(self, table: str, batch_id: str, spool: Spool) -> bool:
        spool.close()
        if spool.count == 0:
            return True
        job = f"extract-{table}-{spool.model}-{spool.prompt_id}-{batch_id}-{spool.attempt}"
        return _load_file(self._client, self._t[table], self._location, job, str(spool.path))

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool:
        return self._write("quarantine", batch_id, spool)

    def write_extractions(self, batch_id: str, spool: Spool) -> bool:
        return self._write("extractions", batch_id, spool)

    def record_run(self, record: ExtractRunRecord) -> bool:
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        job_id = _JOB_ID_SAFE.sub("_", extract_run_key(record))
        try:
            job = self._client.load_table_from_json(
                [json.loads(json.dumps(to_row(record)))],
                self._t["extract_runs"],
                job_id=job_id,
                location=self._location,
                job_config=job_config,
            )
        except Conflict:
            return False
        job.result()
        return True
