"""Split a filing's plain text into passages for embedding.

Accounts text is line-oriented (one table row per line), so splits land on line breaks and
never mid-row. Chunks overlap by a few lines so a fact at a boundary is retrievable from
either side. Chunk ids are stable per document and index, so re-indexing is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    start_line: int
    end_line: int  # exclusive


def chunk_text(text: str, *, max_chars: int = 1500, overlap_lines: int = 3) -> list[Chunk]:
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return []
    chunks: list[Chunk] = []
    start = 0
    while start < len(lines):
        end = start
        size = 0
        while end < len(lines) and (size + len(lines[end]) + 1 <= max_chars or end == start):
            size += len(lines[end]) + 1
            end += 1
        chunks.append(Chunk(len(chunks), "\n".join(lines[start:end]), start, end))
        if end >= len(lines):
            break
        start = max(end - overlap_lines, start + 1)
    return chunks
