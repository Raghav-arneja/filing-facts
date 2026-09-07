"""Compute the pipeline cost per run and per month from measured inputs and list prices.

Usage: uv run python scripts/estimate_cost.py [--bytes N] [--seconds S] > docs/cost.md

Prices are europe-west2 list prices in USD, converted at a fixed rate stated in the output.
Update PRICES and PRICE_DATE when they change; the README links to the generated file
rather than repeating any figure by hand.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from filing_facts.config import Settings

PRICE_DATE = date(2026, 9, 2)
GBP_PER_USD = 0.78

# USD list prices, europe-west2 (London). Sources: cloud.google.com/{run,storage,bigquery}/pricing
PRICES = {
    "run_vcpu_second": 0.0000240,
    "run_gib_second": 0.0000025,
    "run_free_vcpu_seconds": 180_000,
    "run_free_gib_seconds": 360_000,
    "gcs_gib_month": 0.023,
    "gcs_class_a_op": 0.0000050,  # per operation, charged per 1,000 as $0.005
    "bq_active_gib_month": 0.020,
    "bq_free_storage_gib": 10,
    "artifact_gib_month": 0.10,
    "artifact_free_gib": 0.5,
    "scheduler_job_month": 0.10,
    "scheduler_free_jobs": 3,
}

# Measured, not guessed. Bytes: mean of four daily ZIP Content-Length headers observed
# 2026-08-28 to 2026-09-02 (77.5, 163.1, 191.4, 148.7 MB). Seconds: a local --dry-run of the
# 77.5 MB 2026-09-02 file took 2 s; 60 s is a deliberate 30x ceiling for Cloud Run's 1 vCPU
# hashing plus CRC-checking a 200 MB file. Replace with the measured Cloud Run figure once
# the job has run there. Override with flags.
DEFAULT_BYTES = 145_200_000
DEFAULT_SECONDS = 60
RUNS_PER_MONTH = 22  # Tue-Sat publication days
IMAGE_BYTES = 180_000_000
VCPU = 1.0
MEMORY_GIB = 1.0

# Stage 2 parse job, measured on Cloud Run 2026-09-07: three ZIPs at cap 500 in 90 s wall
# time, 1,499 documents and 97,190 fact rows loaded. Per-ZIP figures below.
PARSE_SECONDS_PER_ZIP = 30
# Row sizes: mean LENGTH(TO_JSON_STRING(row)) in BigQuery on 2026-09-07 over the first
# 1,499 documents, 97,190 fact rows, 3 ingest_runs rows and 6 parse_runs rows (a pinned
# batch row carries ~500 member names, hence the size). The cap is read from the app's
# settings so this script cannot disagree with the deployed default.
DOC_ROW_BYTES = 5_445
FACT_ROW_BYTES = 450
FACTS_PER_DOC = 65
INGEST_RUN_ROW_BYTES = 507
PARSE_RUN_ROW_BYTES = 9_826
PARSE_RUN_ROWS_PER_ZIP = 2  # started + succeeded
FULL_DAY_FILINGS = 10_288  # members in the 2026-09-02 ZIP
SCHEDULER_JOBS = 2


def gib(n: int | float) -> float:
    return n / (1 << 30)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bytes", type=int, default=DEFAULT_BYTES)
    parser.add_argument("--seconds", type=int, default=DEFAULT_SECONDS)
    parser.add_argument("--runs-per-month", type=int, default=RUNS_PER_MONTH)
    parser.add_argument("--parse-seconds", type=int, default=PARSE_SECONDS_PER_ZIP)
    parser.add_argument("--parse-cap", type=int, default=Settings().parse_cap)
    args = parser.parse_args(argv)

    runs = args.runs_per_month
    seconds_per_day = args.seconds + args.parse_seconds
    vcpu_s = VCPU * seconds_per_day * runs
    gib_s = MEMORY_GIB * seconds_per_day * runs
    run_cost = (
        max(0.0, vcpu_s - PRICES["run_free_vcpu_seconds"]) * PRICES["run_vcpu_second"]
        + max(0.0, gib_s - PRICES["run_free_gib_seconds"]) * PRICES["run_gib_second"]
    )

    stored_gib = gib(args.bytes) * runs  # month-end storage, files retained indefinitely
    gcs_cost = stored_gib * PRICES["gcs_gib_month"] + runs * 3 * PRICES["gcs_class_a_op"]
    parse_bytes_per_day = args.parse_cap * (DOC_ROW_BYTES + FACTS_PER_DOC * FACT_ROW_BYTES)
    ledger_bytes_per_day = INGEST_RUN_ROW_BYTES + PARSE_RUN_ROWS_PER_ZIP * PARSE_RUN_ROW_BYTES
    bq_rows_gib = gib(runs * (ledger_bytes_per_day + parse_bytes_per_day))
    full_day_multiple = FULL_DAY_FILINGS / args.parse_cap
    bq_cost = max(0.0, bq_rows_gib - PRICES["bq_free_storage_gib"]) * PRICES["bq_active_gib_month"]
    ar_free = PRICES["artifact_free_gib"]
    ar_cost = max(0.0, gib(IMAGE_BYTES) - ar_free) * PRICES["artifact_gib_month"]
    sched_cost = (
        max(0, SCHEDULER_JOBS - PRICES["scheduler_free_jobs"]) * PRICES["scheduler_job_month"]
    )

    lines = [
        ("Cloud Run Jobs compute (ingest + parse)", run_cost, "inside free tier"),
        ("Cloud Storage (raw ZIPs, cumulative)", gcs_cost, f"{stored_gib:.2f} GiB after one month"),
        (
            "BigQuery storage (ledgers, documents, facts)",
            bq_cost,
            f"{bq_rows_gib * 1024:.0f} MiB after one month; free tier is 10 GiB",
        ),
        ("BigQuery queries (dbt views and tests)", 0.0, "inside the 1 TiB/month free tier"),
        ("Artifact Registry (one image)", ar_cost, "inside free tier"),
        (
            f"Cloud Scheduler ({SCHEDULER_JOBS} jobs)",
            sched_cost,
            f"{PRICES['scheduler_free_jobs']} free per billing account",
        ),
    ]
    total_usd = sum(c for _, c, _ in lines)
    per_run_usd = total_usd / runs

    out = [
        "# Running cost (Stages 1 and 2)",
        "",
        f"Generated by `scripts/estimate_cost.py` on {date.today().isoformat()} from list prices "
        f"dated {PRICE_DATE.isoformat()} at {GBP_PER_USD} GBP/USD. Regenerate with `make cost`.",
        "",
        "Inputs (measured, see script header):",
        "",
        f"- Daily ZIP size: {args.bytes / 1e6:.1f} MB",
        f"- Ingest job wall time: {args.seconds} s at {VCPU:g} vCPU / {MEMORY_GIB:g} GiB",
        f"- Parse job wall time: {args.parse_seconds} s per ZIP at cap {args.parse_cap}",
        f"- Runs per month: {runs}",
        "",
        "| Component | USD / month | GBP / month | Note |",
        "|---|---:|---:|---|",
    ]
    for name, usd, note in lines:
        out.append(f"| {name} | {usd:.4f} | {usd * GBP_PER_USD:.4f} | {note} |")
    out += [
        f"| **Total** | **{total_usd:.4f}** | **{total_usd * GBP_PER_USD:.4f}** | |",
        "",
        f"Cost per run: USD {per_run_usd:.5f} (GBP {per_run_usd * GBP_PER_USD:.5f}).",
        "",
        "Storage is the only line that grows. Each month of daily ZIPs adds about "
        f"{stored_gib:.2f} GiB to Cloud Storage, so that line roughly doubles every month "
        "until a lifecycle rule or compaction is added; neither exists yet. BigQuery grows by "
        f"about {bq_rows_gib * 1024:.0f} MiB a month at a cap of {args.parse_cap} filings per "
        f"day; a full day of {FULL_DAY_FILINGS:,} filings would multiply that by about "
        f"{full_day_multiple:.0f} and still sit inside the "
        f"{PRICES['bq_free_storage_gib']} GiB free tier for several months. Nothing here runs "
        "when the jobs are idle.",
    ]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
