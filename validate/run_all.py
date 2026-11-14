"""Runs every validity check on every task and prints a colored, per-task report."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from validate import hand_check as handcheck
from runner.config import Config
from runner.schema import Task, task_fingerprint
from runner.output import UI
from generate.base import CheckResult
from generate.registry import Registry
from validate import check_answer, check_data, check_metadata, check_needs_image, check_render
from validate.context import CheckContext

# Rubric letters used by the runner's stage 2 (a-h):
#   a data loads  b image renders  c reference recomputed  d render matches data
#   e question needs the image  f tolerance stated  g examples cited  h hand-checked
RUBRIC_LETTERS = {
    "data_loads": "a", "data_nonempty": "a", "data_reproducible": "a",
    "image_exists": "b", "image_valid": "b", "render_reproducible": "b",
    "answer_recomputes": "c", "render_matches_data": "d",
    "question_needs_image": "e", "answer_not_in_question": "e", "generator_needs_image_hook": "e",
    "text_only_baseline": "e", "tolerance_stated": "f", "examples_cited": "g", "hand_checked": "h",
}
RUBRIC_LABELS = {"a": "structured data loads", "b": "image renders from the data",
                 "c": "reference answer recomputed in code", "d": "rendering matches the data",
                 "e": "question requires the image", "f": "numeric tolerance stated in the prompt",
                 "g": "real-world examples cited", "h": "hand-checked by a human"}

TASKS_PATH = Path(__file__).resolve().parent.parent / "tasks" / "tasks.jsonl"

CHECK_MODULES = (check_metadata, check_data, check_render, check_answer, check_needs_image)


@dataclass
class ValidationReport:
    results: dict[str, list[tuple[str, CheckResult]]] = field(default_factory=dict)
    valid_ids: list[str] = field(default_factory=list)
    invalid_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {"pass": 0, "fail": 0, "warn": 0, "skip": 0})

    def status_of(self, task_id: str, check_name: str) -> str | None:
        for name, res in self.results.get(task_id, []):
            if name == check_name:
                return res.status
        return None

    def letter_summary(self) -> dict[str, dict[str, int]]:
        """Per rubric letter (a-h): how many tasks pass every check carrying that letter."""
        out = {}
        for letter in RUBRIC_LABELS:
            names = [n for n, l in RUBRIC_LETTERS.items() if l == letter]
            ok = 0
            for tid, checks in self.results.items():
                mine = [r.status for n, r in checks if n in names]
                if mine and "fail" not in mine and "warn" not in mine:
                    ok += 1
            out[letter] = {"pass": ok, "total": len(self.results)}
        return out

    def count_status(self, check_name: str, status: str) -> int:
        return sum(1 for tid in self.results if self.status_of(tid, check_name) == status)

    def to_json(self) -> dict:
        return {
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "valid": self.valid_ids, "invalid": self.invalid_ids, "counts": self.counts,
            "tasks": {
                tid: [{"check": n, "status": r.status, "message": r.message} for n, r in checks]
                for tid, checks in self.results.items()
            },
        }


def validate_tasks(cfg: Config, ui: UI, tasks: list[Task], registry: Registry, *, strict: bool = False,
                   client=None, baseline_model=None) -> ValidationReport:
    from eval.extract_answer import extract_final_answer
    from eval.score import is_correct

    report = ValidationReport()
    ledger = handcheck.load(cfg.tasks_dir)
    for task in tasks:
        gen = registry.generators.get(task.generator)
        ctx = CheckContext(cfg=cfg, task=task, generator=gen, ledger=ledger,
                           fingerprint=task_fingerprint(cfg.root, task))
        ui.emit()
        ui.emit(ui.c(f"{task.id}", "bold") + ui.c(f"   ({task.visual_type} / {task.question_type})", "gray"))
        checks: list[tuple[str, CheckResult]] = []
        for module in CHECK_MODULES:
            try:
                checks.extend(module.run(ctx))
            except Exception as exc:  # noqa: BLE001 - a buggy check must not hide the other checks
                checks.append((module.__name__.split(".")[-1], CheckResult.fail(
                    f"check crashed: {type(exc).__name__}: {exc}", where=f"validate/{module.__name__.split('.')[-1]}.py")))
        if client is not None and baseline_model is not None:
            try:
                checks.append(("text_only_baseline", check_needs_image.text_only_baseline(
                    cfg, task, client, baseline_model, extract_final_answer, is_correct)))
            except Exception as exc:  # noqa: BLE001
                checks.append(("text_only_baseline", CheckResult.warn(f"baseline call failed: {exc}")))

        has_fail = False
        for name, res in checks:
            status = res.status
            if strict and status == "warn":
                status = "fail"
                res = CheckResult(status, res.message + "  (warning treated as failure: --strict)",
                                  res.expected, res.actual, res.where)
            report.counts[status] += 1
            if status == "fail":
                has_fail = True
            letter = RUBRIC_LETTERS.get(name)
            ui.status(status, f"{('(' + letter + ')') if letter else '   '} {name:<26} {res.message}", indent=2)
            if status in ("fail", "warn"):
                if res.expected is not None or res.actual is not None:
                    ui.expected_actual(res.expected, res.actual, res.where, indent=11)
                elif res.where:
                    ui.detail(f"look at: {res.where}", indent=11)
        report.results[task.id] = checks
        (report.invalid_ids if has_fail else report.valid_ids).append(task.id)
        verdict = ui.c("INVALID (excluded from evaluation)", "red", "bold") if has_fail else ui.c("valid", "green", "bold")
        ui.emit(f"  -> {task.id}: {verdict}")

    path = cfg.results_dir / "validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    """python -m validate.run_all [--no-color] [--strict] [--task ID ...]  (run from anywhere)."""
    import argparse
    import sys
    from pathlib import Path

    from generate.registry import discover
    from runner.schema import load_tasks

    parser = argparse.ArgumentParser(description="Validate every task in tasks/tasks.jsonl")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--task", nargs="+", metavar="ID")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    cfg = Config.load(root)
    ui = UI(color=False if args.no_color else None)
    tasks, problems = load_tasks(cfg.tasks_file)
    for problem in problems:
        ui.fail(problem)
    tasks = [t for t in tasks if not args.task or t.id in args.task]
    if not tasks:
        ui.fail("no tasks to validate (tasks/tasks.jsonl has no real tasks; run: python -m generate.generate_tasks)")
        return 1
    report = validate_tasks(cfg, ui, tasks, discover(), strict=args.strict)
    ui.emit()
    ui.info(f"{len(report.valid_ids)} valid, {len(report.invalid_ids)} invalid (details: results/validation.json)")
    return 1 if report.invalid_ids or problems else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())


def load_tasks(path: Path = TASKS_PATH) -> list[Task]:
    """Load the real tasks from a JSONL file (the placeholder record is skipped)."""
    from runner.schema import load_tasks as _load
    return _load(Path(path))[0]


def run_checks(task) -> dict:
    """Original one-task API: the reference answer, render result and needs-image result for one task."""
    from validate.check_answer import compute_reference_answer
    from validate.check_needs_image import needs_image
    from validate.check_render import check_render
    root = Path(__file__).resolve().parent.parent
    image = getattr(task, "image_path", None) or task["image_path"]
    return {"reference_answer": compute_reference_answer(task), "render": check_render(task, root / image),
            "needs_image": needs_image(task)}
