"""Shared state passed to every stage of the runner."""

from dataclasses import dataclass, field
from typing import Any

from runner.output import Output


@dataclass
class StageResult:
    name: str
    passed: int = 0
    failed: int = 0
    warned: int = 0
    not_implemented: bool = False

    @property
    def ok(self) -> bool:
        return not self.not_implemented and self.failed == 0


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def add(self, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost_usd


@dataclass
class RunContext:
    config: dict[str, Any]
    out: Output
    models: list[str]
    task_filter: str | None
    dry_run: bool
    verbose: bool
    tasks: list[dict[str, Any]] = field(default_factory=list)
    valid_task_ids: list[str] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stage_results: dict[int, StageResult] = field(default_factory=dict)
