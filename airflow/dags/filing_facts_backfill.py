"""Backfill: reprocess only the quarantined documents of one stage after a fix.

Quarantine rows mark documents as processed. A backfill does not delete them, which would
erase the history of what failed and why; it stamps `released_at`, after which the row no
longer counts as processed and both jobs put released documents first. The parse job is
then rerun once per affected source with `--source-key`, because the scheduled selection
would otherwise skip sources already parsed at the current cap; the extract job is rerun
for the model and prompt whose rows were released.

Low-confidence extractions are not backfilled here: they kept their answer, so the way to
get a better one is a new prompt version, which is a fresh pass by construction.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import cast

from airflow.models.param import Param
from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook
from airflow.providers.google.cloud.hooks.pubsub import PubSubHook
from airflow.providers.google.cloud.operators.cloud_run import CloudRunExecuteJobOperator
from airflow.sdk import DAG, task

PROJECT = os.environ.get("FF_GCP_PROJECT", "filing-facts-gb")
REGION = os.environ.get("FF_REGION", "europe-west2")
DATASET = os.environ.get("FF_BQ_DATASET", "filing_facts_raw")
LIFECYCLE_TOPIC = "filing-facts-lifecycle"
DEAD_LETTER_SUBSCRIPTION = "filing-facts-lifecycle-dead-letter-pull"
QUARANTINE = f"`{PROJECT}.{DATASET}.quarantine`"

RELEASE_SQL = f"""
UPDATE {QUARANTINE}
SET released_at = CURRENT_TIMESTAMP()
WHERE stage = @stage
  AND released_at IS NULL
  AND reason != 'LowConfidence'
  AND (@reason = '' OR reason = @reason)
  AND (@model = '' OR model = @model)
  AND (@prompt = '' OR prompt_id LIKE CONCAT(@prompt, '-%'))
"""  # noqa: S608 - identifiers are constants above; values are query parameters


def _param(name: str, value: str) -> dict[str, object]:
    return {
        "name": name,
        "parameterType": {"type": "STRING"},
        "parameterValue": {"value": value},
    }


def _run_overrides(args: list[str]) -> dict[str, object]:
    return {"container_overrides": [{"args": args}]}


with DAG(
    dag_id="filing_facts_backfill",
    description="Release quarantined documents for one stage, then rerun that stage for them.",
    schedule=None,
    start_date=datetime(2026, 9, 1),
    catchup=False,
    max_active_runs=1,
    tags=["filing-facts", "backfill"],
    params={
        "stage": Param("extract", type="string", enum=["parse", "extract"]),
        "reason": Param("", type="string", description="Quarantine reason, or empty for all."),
        "model": Param("gemini-3.1-flash-lite", type="string", description="Extract stage only."),
        "prompt": Param("v2", type="string", description="Extract stage only: prompt version."),
        "extract_cap": Param(300, type="integer", minimum=1, maximum=2000),
        "dead_letters": Param(
            "none",
            type="string",
            enum=["none", "replay", "purge"],
            description=(
                "replay: republish dead-lettered lifecycle events after a fix. "
                "purge: acknowledge them without replay, for poison messages. Both are logged."
            ),
        ),
    },
) as dag:

    @task(task_id="release_quarantined")
    def release_quarantined(**context: object) -> int:
        """Stamp released_at on the matching rows. Returns how many rows were released.

        A task rather than BigQueryInsertJobOperator: that operator reads the run's logical
        date in execute(), and manual runs in Airflow 3 have none.
        """
        params = cast(dict[str, object], context["params"])
        stage = str(params["stage"])
        extract = stage == "extract"
        job = BigQueryHook(location=REGION).insert_job(
            project_id=PROJECT,
            location=REGION,
            configuration={
                "query": {
                    "query": RELEASE_SQL,
                    "useLegacySql": False,
                    "queryParameters": [
                        _param("stage", stage),
                        _param("reason", str(params["reason"])),
                        _param("model", str(params["model"]) if extract else ""),
                        _param("prompt", str(params["prompt"]) if extract else ""),
                    ],
                }
            },
        )
        job.result()
        return int(job.num_dml_affected_rows or 0)

    release = release_quarantined()

    @task
    def released_parse_sources(**context: object) -> list[dict[str, object]]:
        """Sources with released parse rows, each as a --source-key override for the parse job."""
        params = cast(dict[str, object], context["params"])
        if params["stage"] != "parse":
            return []
        hook = BigQueryHook(location=REGION, use_legacy_sql=False)
        rows = hook.get_records(
            f"SELECT DISTINCT source_key FROM {QUARANTINE} "  # noqa: S608
            "WHERE stage = 'parse' AND released_at IS NOT NULL",
        )
        return [_run_overrides(["--source-key", str(r[0])]) for r in rows]

    @task(task_id="handle_dead_letters")
    def handle_dead_letters(**context: object) -> dict[str, int]:
        """Drain the lifecycle dead-letter subscription: replay to the topic, or purge.

        A dead letter is a message the dispatcher rejected five times. Replaying it only makes
        sense after the cause is fixed; a poison message replayed unfixed comes straight back.
        Every message handled is logged with its id and attributes, so nothing vanishes.
        """
        params = cast(dict[str, object], context["params"])
        mode = str(params["dead_letters"])
        counts = {"replayed": 0, "purged": 0}
        if mode == "none":
            return counts
        hook = PubSubHook()
        while True:
            # A pull that returns immediately may come back empty while messages wait; a
            # blocking pull waits for the server's deadline and is the reliable form.
            pulled = hook.pull(
                project_id=PROJECT,
                subscription=DEAD_LETTER_SUBSCRIPTION,
                max_messages=50,
                return_immediately=False,
            )
            if not pulled:
                break
            for received in pulled:
                msg = received.message
                attrs = dict(msg.attributes)
                print(f"dead letter {msg.message_id} attributes={attrs} mode={mode}")
                if mode == "replay":
                    hook.publish(
                        project_id=PROJECT,
                        topic=LIFECYCLE_TOPIC,
                        messages=[{"data": msg.data, "attributes": {**attrs, "replayed": "1"}}],
                    )
                    counts["replayed"] += 1
                else:
                    counts["purged"] += 1
            hook.acknowledge(
                project_id=PROJECT,
                subscription=DEAD_LETTER_SUBSCRIPTION,
                ack_ids=[r.ack_id for r in pulled],
            )
        return counts

    @task.branch
    def which_stage(**context: object) -> str:
        params = cast(dict[str, object], context["params"])
        return "released_parse_sources" if params["stage"] == "parse" else "rerun_extract"

    rerun_extract = CloudRunExecuteJobOperator(
        task_id="rerun_extract",
        project_id=PROJECT,
        region=REGION,
        job_name="extract",
        overrides=_run_overrides(
            [
                "--model",
                "{{ params.model }}",
                "--prompt",
                "{{ params.prompt }}",
                "--cap",
                "{{ params.extract_cap }}",
            ]
        ),
    )

    sources = released_parse_sources()
    rerun_parse = CloudRunExecuteJobOperator.partial(
        task_id="rerun_parse",
        project_id=PROJECT,
        region=REGION,
        job_name="parse",
    ).expand(overrides=sources)

    branch = which_stage()
    dead_letters = handle_dead_letters()
    dead_letters >> release >> branch
    branch >> rerun_extract
    branch >> sources
