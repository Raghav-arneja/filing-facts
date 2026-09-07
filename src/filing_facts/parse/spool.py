"""Rows spooled to newline-delimited JSON on disk as they are produced.

The job never holds a batch in memory: a full day is ~670k fact rows, and holding them plus
their JSON serialisation peaks well above the 1 GiB Cloud Run job. A spool is one compact
copy, and BigQuery loads it straight from the file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from filing_facts.parse.rows import to_row


class Spool:
    def __init__(self, path: Path, source_key: str) -> None:
        self.path = path
        self.source_key = source_key
        self.count = 0
        self._fh = path.open("w", encoding="utf-8")

    def append(self, row: Any) -> None:
        self._fh.write(json.dumps(to_row(row), ensure_ascii=False) + "\n")
        self.count += 1

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def rows(self) -> Iterator[dict[str, Any]]:
        self.close()
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
