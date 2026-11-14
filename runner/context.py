"""Shared state passed to every stage of the runner.

`RunContext` carries the config, the output object, and whatever earlier stages produced (loaded
tasks, validation results, model replies, scores), so a stage can be run on its own: it fills in
what it needs from disk when an earlier stage was not run in this invocation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from generate.registry import Registry
from runner.config import Config, ModelSpec
from runner.output import UI
from runner.schema import Task, load_tasks


class StageAbort(Exception):
    """Raised by a stage to stop the run with a clear message (reported as [FAIL])."""


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
    cfg: Config
    out: UI
    registry: Registry
    args: SimpleNamespace
    client: Any = None
    tasks: list[Task] | None = None
    validation: Any = None  # validate.run_all.ValidationReport
    records: list | None = None
    rows: list | None = None
    scoring: dict | None = None
    variation: dict | None = None
    preflight_ok: bool | None = None
    format_check: bool | None = None
    files_written: list[str] = field(default_factory=list)
    stage_results: dict[int, StageResult] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)

    # ---- convenience views (names the original runner used) ---------------------------------
    @property
    def ui(self) -> UI:
        return self.out

    @property
    def config(self) -> dict:
        return self.cfg.data

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self.args, "dry_run", False))

    @property
    def verbose(self) -> bool:
        return bool(getattr(self.args, "verbose", False))

    @property
    def task_filter(self) -> list[str] | None:
        return getattr(self.args, "task", None)

    @property
    def strict(self) -> bool:
        return bool(getattr(self.args, "strict", False) or self.cfg.get("validation.strict"))

    # ---- helpers ----------------------------------------------------------------------------
    def stage_result(self, name: str) -> StageResult:
        """Counts for the stage that is running now (read from the output tally)."""
        counts = self.out.current.counts if self.out.current else {"pass": 0, "fail": 0, "warn": 0}
        return StageResult(name, counts["pass"], counts["fail"], counts["warn"])

    def ensure_tasks(self) -> list[Task]:
        if self.tasks is None:
            tasks, problems = load_tasks(self.cfg.tasks_file)
            self.tasks = tasks
            if problems:
                self.out.warn(f"{len(problems)} problem(s) while reading tasks.jsonl (stage 1 lists them)")
        return self.tasks

    def selected_tasks(self) -> list[Task]:
        only = self.task_filter
        return [t for t in self.ensure_tasks() if not only or t.id in only]

    def ensure_validation(self):
        if self.validation is None:
            from validate.run_all import validate_tasks
            self.out.info("running validation quietly first (evaluation only uses valid tasks)")
            with self.out.quiet():
                self.validation = validate_tasks(self.cfg, self.out, self.selected_tasks(), self.registry,
                                                 strict=self.strict)
        return self.validation

    def models(self) -> list[ModelSpec]:
        wanted = getattr(self.args, "models", None)
        return [m for m in self.cfg.models() if not wanted or m.label in wanted]

    def get_client(self):
        if self.client is not None:
            return self.client
        from eval.client import OpenRouterClient
        env = self.cfg.get("eval.api_key_env", "OPENROUTER_API_KEY")
        key = os.environ.get(env)
        if not key:
            raise StageAbort(f"environment variable {env} is not set; cannot call models")
        self.client = OpenRouterClient(
            self.cfg.get("eval.base_url"), key, timeout=self.cfg.get("eval.request_timeout_s", 300),
            max_retries=self.cfg.get("eval.max_api_retries", 3), backoff=self.cfg.get("eval.retry_backoff_s", 5))
        return self.client
