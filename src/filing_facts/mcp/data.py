"""Read side of the MCP tools: a Protocol, a BigQuery implementation over the dbt staging
and eval views, and an in-memory one for tests. Tools never build SQL themselves."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

Row = dict[str, Any]


def _plain(value: Any) -> Any:
    """BigQuery NUMERIC arrives as Decimal, which the tool result would render as a string."""
    return float(value) if isinstance(value, Decimal) else value


class FilingData(Protocol):
    def document(self, document_id: str) -> Row | None: ...

    def facts(self, document_id: str) -> list[Row]: ...

    def extraction(
        self, document_id: str, model: str | None, prompt_id: str | None
    ) -> list[Row]: ...

    def model_metrics(self) -> list[Row]: ...

    def status(self) -> Row: ...


class MemoryFilingData:
    def __init__(
        self,
        documents: list[Row],
        facts: list[Row],
        extraction_values: list[Row],
        metrics: list[Row],
        status: Row,
    ) -> None:
        self._documents = {str(d["document_id"]): d for d in documents}
        self._facts = facts
        self._values = extraction_values
        self._metrics = metrics
        self._status = status

    def document(self, document_id: str) -> Row | None:
        return self._documents.get(document_id)

    def facts(self, document_id: str) -> list[Row]:
        return [f for f in self._facts if f["document_id"] == document_id]

    def extraction(self, document_id: str, model: str | None, prompt_id: str | None) -> list[Row]:
        return [
            v
            for v in self._values
            if v["document_id"] == document_id
            and (model is None or v["model"] == model)
            and (prompt_id is None or str(v["prompt_id"]).startswith(prompt_id))
        ]

    def model_metrics(self) -> list[Row]:
        return list(self._metrics)

    def status(self) -> Row:
        return dict(self._status)


class BigQueryFilingData:
    def __init__(self, project: str, raw_dataset: str, staging_dataset: str, location: str) -> None:
        from google.cloud import bigquery

        self._client = bigquery.Client(project=project, location=location)
        self._location = location
        self._raw = f"{project}.{raw_dataset}"
        self._stg = f"{project}.{staging_dataset}"

    def _query(self, sql: str, **params: Any) -> list[Row]:
        from google.cloud import bigquery

        typed = [
            bigquery.ScalarQueryParameter(k, "STRING", v)
            for k, v in params.items()
            if v is not None
        ]
        job = self._client.query(
            sql, job_config=bigquery.QueryJobConfig(query_parameters=typed), location=self._location
        )
        rows: list[Any] = list(job.result())
        return [{k: _plain(v) for k, v in dict(r).items()} for r in rows]

    def document(self, document_id: str) -> Row | None:
        rows = self._query(
            f"SELECT document_id, company_number, CAST(period_end AS STRING) AS period_end, "  # noqa: S608
            f"source_key, text_chars, fact_count, CAST(parsed_at AS STRING) AS parsed_at "
            f"FROM `{self._stg}.stg_documents` WHERE document_id = @document_id",
            document_id=document_id,
        )
        return rows[0] if rows else None

    def facts(self, document_id: str) -> list[Row]:
        return self._query(
            f"SELECT concept, CAST(period_start AS STRING) AS period_start, "  # noqa: S608
            f"CAST(period_end AS STRING) AS period_end, CAST(instant AS STRING) AS instant, "
            f"value, unit, dimensions, inconsistent FROM `{self._stg}.stg_facts` "
            f"WHERE document_id = @document_id AND is_numeric AND NOT hidden_everywhere "
            f"ORDER BY concept, period_end, instant LIMIT 200",
            document_id=document_id,
        )

    def extraction(self, document_id: str, model: str | None, prompt_id: str | None) -> list[Row]:
        # Optional filters are added to the SQL only when given: BigQuery rejects a query
        # that names a parameter it was not sent, and a NULL-valued one is not sent either.
        params: dict[str, str] = {"document_id": document_id}
        where = "WHERE v.document_id = @document_id "
        if model is not None:
            params["model"] = model
            where += "AND v.model = @model "
        if prompt_id is not None:
            params["prompt_id"] = prompt_id
            where += "AND STARTS_WITH(v.prompt_id, @prompt_id) "
        return self._query(
            f"SELECT v.model, v.prompt_id, v.field, v.concept, v.period, v.value, v.stated, "  # noqa: S608
            f"v.confidence, v.evidence, s.truth, s.outcome, s.error_type "
            f"FROM `{self._stg}.stg_extraction_values` v "
            f"LEFT JOIN `{self._stg}.eval_scores` s "
            f"  USING (document_id, model, prompt_id, field, period) "
            f"{where}ORDER BY v.model, v.prompt_id, v.field",
            **params,
        )

    def model_metrics(self) -> list[Row]:
        return self._query(
            f"SELECT model, prompt_id, concept, verifiable, correct, wrong, missed, "  # noqa: S608
            f"unsupported, tag_error, precision, recall, documents, usd_per_1000 "
            f"FROM `{self._stg}.eval_metrics` ORDER BY model, prompt_id, concept"
        )

    def status(self) -> Row:
        rows = self._query(
            f"SELECT "  # noqa: S608
            f"(SELECT COUNT(*) FROM `{self._stg}.stg_documents`) AS documents, "
            f"(SELECT COUNT(*) FROM `{self._stg}.stg_facts`) AS facts, "
            f"(SELECT COUNT(*) FROM `{self._stg}.stg_extractions`) AS extractions, "
            f"(SELECT COUNT(DISTINCT document_id) FROM `{self._raw}.chunks`) AS indexed_docs, "
            f"(SELECT COUNT(*) FROM `{self._raw}.chunks`) AS chunks, "
            f"(SELECT COUNT(*) FROM `{self._stg}.stg_quarantine` WHERE NOT released) "
            f"  AS quarantined, "
            f"(SELECT CAST(MAX(finished_at) AS STRING) FROM `{self._stg}.stg_parse_runs` "
            f"  WHERE status = 'succeeded') AS last_parse, "
            f"(SELECT CAST(MAX(finished_at) AS STRING) FROM `{self._stg}.stg_extract_runs` "
            f"  WHERE status = 'succeeded') AS last_extract, "
            f"(SELECT CAST(MAX(finished_at) AS STRING) FROM `{self._raw}.index_runs` "
            f"  WHERE status = 'succeeded') AS last_index, "
            f"(SELECT ROUND(SUM(cost_usd), 2) FROM `{self._stg}.stg_extract_runs`) "
            f"  AS extract_spend_usd, "
            f"(SELECT ROUND(SUM(cost_usd), 2) FROM `{self._raw}.index_runs`) AS index_spend_usd"
        )
        return rows[0]
