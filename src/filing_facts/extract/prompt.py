"""Prompts are versioned files in prompts/extract/. The version and a content hash travel
with every extraction row, so a prompt change is visible in the data and in the eval."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts" / "extract"
_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


@dataclass(frozen=True)
class Prompt:
    version: str
    sha256: str  # first 12 hex chars of the file's hash
    system: str
    user_template: str

    @property
    def id(self) -> str:
        return f"{self.version}-{self.sha256}"

    def render(self, text: str) -> str:
        return self.user_template.replace("{text}", text)


def load_prompt(version: str, directory: Path = PROMPTS_DIR) -> Prompt:
    path = directory / f"{version}.md"
    raw = path.read_text(encoding="utf-8")
    body = _FRONT.sub("", raw, count=1)
    if "# System" not in body or "# User" not in body:
        raise ValueError(f"{path.name}: expected '# System' and '# User' sections")
    system, user = body.split("# User", 1)
    system = system.replace("# System", "", 1).strip()
    user = user.strip()
    if "{text}" not in user:
        raise ValueError(f"{path.name}: user section must contain {{text}}")
    return Prompt(
        version=version,
        sha256=hashlib.sha256(raw.encode()).hexdigest()[:12],
        system=system,
        user_template=user,
    )


def available_versions(directory: Path = PROMPTS_DIR) -> list[str]:
    return sorted(p.stem for p in directory.glob("v*.md"))
