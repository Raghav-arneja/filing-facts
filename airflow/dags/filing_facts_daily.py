"""The full pipeline as one DAG: ingest -> parse -> extract, each a Cloud Run Job.

Cloud Scheduler triggers ingest and parse every publication morning by itself, so this DAG
has no schedule of its own. It exists to run the chain end to end on demand, to run the
extract step (which spends money and is never scheduled), and to be the same DAG code that
would run on Cloud Composer if the project ever needed a managed control plane.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.models.param import Param
from airflow.providers.google.cloud.operators.cloud_run import CloudRunExecuteJobOperator
from airflow.sdk import DAG

PROJECT = os.environ.get("FF_GCP_PROJECT", "filing-facts-gb")
REGION = os.environ.get("FF_REGION", "europe-west2")

with DAG(
    dag_id="filing_facts_daily",
    description="Ingest today's Companies House ZIP, parse it, extract facts with an LLM.",
    schedule=None,
    start_date=datetime(2026, 9, 1),
    catchup=False,
    max_active_runs=1,
    tags=["filing-facts", "stage-3"],
    params={
        "extract_model": Param("gemini-3.1-flash-lite", type="string"),
        "extract_cap": Param(300, type="integer", minimum=1, maximum=2000),
        "prompt": Param("v2", type="string"),
    },
) as dag:
    ingest = CloudRunExecuteJobOperator(
        task_id="ingest",
        project_id=PROJECT,
        region=REGION,
        job_name="ingest",
    )
    parse = CloudRunExecuteJobOperator(
        task_id="parse",
        project_id=PROJECT,
        region=REGION,
        job_name="parse",
    )
    extract = CloudRunExecuteJobOperator(
        task_id="extract",
        project_id=PROJECT,
        region=REGION,
        job_name="extract",
        overrides={
            "container_overrides": [
                {
                    "args": [
                        "--model",
                        "{{ params.extract_model }}",
                        "--prompt",
                        "{{ params.prompt }}",
                        "--cap",
                        "{{ params.extract_cap }}",
                    ]
                }
            ]
        },
    )
    ingest >> parse >> extract
