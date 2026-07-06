from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

@dataclass
class ProjectSnapshot:
    tasks: list[dict] = field(default_factory=list)

class StateSink(Protocol):
    name: str
    def render(self, snapshot: ProjectSnapshot) -> None: ...
