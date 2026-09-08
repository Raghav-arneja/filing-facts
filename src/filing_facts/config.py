"""Runtime settings. Every value comes from the environment, prefixed FF_."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings for the ingest job. Set via FF_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="FF_", extra="ignore")

    gcp_project: str = Field(default="", description="GCP project id. Required unless --dry-run.")
    raw_bucket: str = Field(
        default="", description="GCS bucket for raw files. Required unless --dry-run."
    )
    bq_dataset: str = "filing_facts_raw"
    bq_table: str = "ingest_runs"
    bq_location: str = "europe-west2"
    source_base_url: str = "https://download.companieshouse.gov.uk"
    raw_prefix: str = "companies_house/accounts_daily"
    max_bytes: int = Field(
        default=750_000_000,
        description="Refuse downloads larger than this. Cloud Run disk is memory-backed.",
    )
    http_timeout_seconds: float = 120.0
    local_data_dir: str = "data"

    # Stage 2: parse
    parse_cap: int = Field(
        default=500,
        description="Max filings parsed per daily ZIP. Deterministic selection; raise to widen.",
    )
    documents_table: str = "documents"
    facts_table: str = "facts"
    quarantine_table: str = "quarantine"
    parse_runs_table: str = "parse_runs"

    # Stage 3: extract
    extract_model: str = "gemini-3.1-flash-lite"
    prompt_version: str = "v2"
    vertex_location: str = (
        "global"  # the current Flash line is served from the global endpoint only
    )
    extract_cap: int = Field(default=300, description="Documents per run per (model, prompt).")
    extract_min_confidence: float = 0.5
    extract_threads: int = 4
    extractions_table: str = "extractions"
    extract_runs_table: str = "extract_runs"
