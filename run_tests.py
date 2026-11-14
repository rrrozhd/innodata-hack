#!/usr/bin/env python3
"""Command-line runner for the Visual Task Factory.

Runs stages 0-7 in order, or a selected range, and prints evidence for each rubric item.

Examples:
  python run_tests.py                          run all stages
  python run_tests.py --stage 2-5 --dry-run    run stages 2-5 with no API calls
  python run_tests.py --task <task_id>         limit per-task stages to one task
"""

import argparse
import datetime
import json
import sys
from pathlib import Path
from types import ModuleType

from runner import (
    evaluate,
    format_check,
    load_tasks,
    preflight,
    score,
    scorecard,
    validate_tasks,
    variation,
)
from runner.context import RunContext, StageResult
from runner.output import Output, color_enabled

STAGE_MODULES: dict[int, ModuleType] = {
    0: preflight,
    1: load_tasks,
    2: validate_tasks,
    3: variation,
    4: evaluate,
    5: score,
    6: format_check,
    7: scorecard,
}
CONFIG_PATH = Path("results/run_config.json")
LOG_DIR = Path("results/logs")
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_INPUT = 2


def parse_stage_range(spec: str) -> list[int]:
    """Turn 'all', 'N', or 'A-B' into a sorted list of stage numbers."""
    last = max(STAGE_MODULES)
    if spec == "all":
        return list(STAGE_MODULES)
    if "-" in spec:
        first_s, last_s = spec.split("-", 1)
        first, end = int(first_s), int(last_s)
        if not 0 <= first <= end <= last:
            raise argparse.ArgumentTypeError(f"stage range must be within 0-{last}: {spec}")
        return list(range(first, end + 1))
    number = int(spec)
    if not 0 <= number <= last:
        raise argparse.ArgumentTypeError(f"stage must be within 0-{last}: {spec}")
    return [number]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visual Task Factory runner")
    parser.add_argument("--stage", type=parse_stage_range, default=list(STAGE_MODULES),
                        help="stage number, range like 2-5, or 'all' (default)")
    parser.add_argument("--task", help="run per-task stages for this task id only")
    parser.add_argument("--models", help="comma-separated model names (default: from config)")
    parser.add_argument("--dry-run", action="store_true", help="make no API calls")
    parser.add_argument("--verbose", action="store_true", help="print extra detail")
    parser.add_argument("--no-color", action="store_true", help="plain text output")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help=f"run config file (default: {CONFIG_PATH})")
    return parser.parse_args(argv)


def load_config(path: Path) -> dict:
    """Load the run config. Exits with a message if it cannot be read."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def run_selected_stages(ctx: RunContext, stages: list[int]) -> None:
    """Run each selected stage, printing its banner and summary.

    A stage that is not implemented yet is reported as a warning and counted as not ok.
    """
    for number in stages:
        module = STAGE_MODULES[number]
        name = module.NAME
        ctx.out.banner(number, name)
        try:
            result = module.run(ctx)
        except NotImplementedError as exc:
            ctx.out.warn_line(f"stage {number} not implemented: {exc}")
            ctx.stage_results[number] = StageResult(name=name, not_implemented=True)
            continue
        ctx.stage_results[number] = result
        ctx.out.summary(name, result.passed, result.failed, result.warned)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot load config {args.config}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"run-{timestamp}.log"
    out = Output(color=color_enabled(args.no_color), log_path=log_path)
    models = args.models.split(",") if args.models else [m["name"] for m in config.get("models", [])]
    ctx = RunContext(
        config=config,
        out=out,
        models=models,
        task_filter=args.task,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        run_selected_stages(ctx, args.stage)
    finally:
        out.line()
        out.info_line(f"Plain-text log: {log_path}")
        out.close()

    all_ok = all(result.ok for result in ctx.stage_results.values())
    return EXIT_OK if all_ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
