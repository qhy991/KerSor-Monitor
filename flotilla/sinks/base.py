from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ProjectSnapshot:
    tasks: list[dict] = field(default_factory=list)
    project_id: str | None = None  # which project this snapshot is for (per-project SSE)


class StateSink(Protocol):
    name: str

    def render(self, snapshot: ProjectSnapshot) -> None: ...
