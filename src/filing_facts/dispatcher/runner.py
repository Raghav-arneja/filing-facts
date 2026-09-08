# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Starting Cloud Run jobs. A protocol so the dispatcher can be tested without GCP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


class JobRunner(Protocol):
    def is_running(self, job: str) -> bool:
        """Whether an execution of this job is in progress. The dispatcher serialises
        executions per job so a redelivered event cannot start a concurrent duplicate."""
        ...

    def run_job(self, job: str, args: list[str]) -> str:
        """Start one execution with the given container args. Returns the execution name."""
        ...


@dataclass
class FakeRunner:
    calls: list[tuple[str, list[str]]] = field(default_factory=list[tuple[str, list[str]]])
    fail: bool = False
    running: set[str] = field(default_factory=set[str])

    def is_running(self, job: str) -> bool:
        return job in self.running

    def run_job(self, job: str, args: list[str]) -> str:
        if self.fail:
            raise RuntimeError("simulated Cloud Run failure")
        self.calls.append((job, args))
        return f"{job}-fake-{len(self.calls)}"


class CloudRunJobRunner:
    def __init__(self, project: str, region: str) -> None:
        from google.cloud import run_v2

        self._client = run_v2.JobsClient()
        self._parent = f"projects/{project}/locations/{region}"

    def is_running(self, job: str) -> bool:
        from google.cloud import run_v2

        executions = run_v2.ExecutionsClient().list_executions(
            request=run_v2.ListExecutionsRequest(parent=f"{self._parent}/jobs/{job}", page_size=20)
        )
        return any(not e.completion_time for e in executions)

    def run_job(self, job: str, args: list[str]) -> str:
        from google.cloud import run_v2

        request = run_v2.RunJobRequest(
            name=f"{self._parent}/jobs/{job}",
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=args)]
            ),
        )
        operation = self._client.run_job(request=request)
        # Do not wait for completion: the push subscription's ack deadline is short, and the
        # job records its own outcome in the ledger. The operation name identifies the execution.
        name = operation.metadata.name if operation.metadata else "unknown"
        log.info("job_started", job=job, args=args, execution=name)
        return str(name)
