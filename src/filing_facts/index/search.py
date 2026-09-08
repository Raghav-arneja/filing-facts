"""Search the chunks by meaning: embed the question as a query, then BigQuery VECTOR_SEARCH.

Brute-force search at this corpus size (a few thousand chunks) returns in under a second and
needs no index; a vector index becomes worthwhile above a few hundred thousand rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from filing_facts.index.embedder import Embedder


@dataclass(frozen=True)
class Hit:
    document_id: str
    chunk_index: int
    company_number: str
    company_name: str | None
    period_end: str
    text: str
    distance: float


class SearchBackend(Protocol):
    def nearest(self, vector: list[float], embedding_model: str, k: int) -> list[Hit]: ...


class Searcher:
    def __init__(self, embedder: Embedder, backend: SearchBackend) -> None:
        self._embedder = embedder
        self._backend = backend

    def search(self, question: str, k: int = 8) -> list[Hit]:
        vector = self._embedder.embed([question], "RETRIEVAL_QUERY")[0].vector
        return self._backend.nearest(vector, self._embedder.model_id, k)


class MemorySearchBackend:
    """Cosine distance over rows the memory or local sinks hold; for tests and dry runs."""

    def __init__(self, chunks: list[dict[str, Any]], documents: dict[str, dict[str, Any]]) -> None:
        self._chunks = chunks
        self._documents = documents

    def nearest(self, vector: list[float], embedding_model: str, k: int) -> list[Hit]:
        import math

        def cosine_distance(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return 1.0 - dot / (na * nb)

        scored = [
            (cosine_distance(vector, [float(v) for v in c["embedding"]]), c)
            for c in self._chunks
            if c["embedding_model"] == embedding_model
        ]
        scored.sort(key=lambda s: s[0])
        hits: list[Hit] = []
        for dist, c in scored[:k]:
            d = self._documents.get(str(c["document_id"]), {})
            hits.append(
                Hit(
                    document_id=str(c["document_id"]),
                    chunk_index=int(c["chunk_index"]),
                    company_number=str(d.get("company_number", "")),
                    company_name=d.get("company_name"),
                    period_end=str(d.get("period_end", "")),
                    text=str(c["text"]),
                    distance=dist,
                )
            )
        return hits


class BigQuerySearchBackend:
    def __init__(self, project: str, dataset: str, staging_dataset: str, location: str) -> None:
        from google.cloud import bigquery

        self._client = bigquery.Client(project=project, location=location)
        self._location = location
        self._chunks = f"{project}.{dataset}.chunks"
        self._documents = f"{project}.{staging_dataset}.stg_documents"
        self._extractions = f"{project}.{staging_dataset}.stg_extractions"

    def nearest(self, vector: list[float], embedding_model: str, k: int) -> list[Hit]:
        from google.cloud import bigquery

        sql = f"""
        WITH hits AS (
          SELECT base.document_id, base.chunk_index, base.text, distance
          FROM VECTOR_SEARCH(
            (SELECT document_id, chunk_index, text, embedding
             FROM `{self._chunks}` WHERE embedding_model = @model),
            'embedding', (SELECT @vector AS embedding), top_k => @k, distance_type => 'COSINE')
        ),
        names AS (
          SELECT document_id, ANY_VALUE(company_name) AS company_name
          FROM `{self._extractions}` GROUP BY document_id
        )
        SELECT h.document_id, h.chunk_index, h.text, h.distance,
               d.company_number, CAST(d.period_end AS STRING) AS period_end, n.company_name
        FROM hits h
        JOIN `{self._documents}` d USING (document_id)
        LEFT JOIN names n USING (document_id)
        ORDER BY h.distance
        """  # noqa: S608 - identifiers are configuration; values are parameters
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("model", "STRING", embedding_model),
                bigquery.ArrayQueryParameter("vector", "FLOAT64", vector),
                bigquery.ScalarQueryParameter("k", "INT64", k),
            ]
        )
        rows: list[Any] = list(
            self._client.query(sql, job_config=job_config, location=self._location).result()
        )
        return [
            Hit(
                document_id=str(r["document_id"]),
                chunk_index=int(r["chunk_index"]),
                company_number=str(r["company_number"]),
                company_name=r["company_name"],
                period_end=str(r["period_end"]),
                text=str(r["text"]),
                distance=float(r["distance"]),
            )
            for r in rows
        ]
