#!/usr/bin/env python3
"""Command-line runner for the Visual Task Factory.

Runs stages 0-7 in order, or a selected range, and prints evidence for each rubric item.

Examples:
  python run_tests.py                          run all stages (0-7)
  python run_tests.py --stage 2-5 --dry-run    run stages 2-5 with no API calls
  python run_tests.py --task <task_id>         limit per-task stages to one task
  python run_tests.py --generate               run the generators first (writes tasks/), then the stages
  python run_tests.py --selftest               prove the runner works (demo generators, no keys, no network)
  python run_tests.py --package                secret scan + build the submission zip

Other entry points: python -m generate.generate_tasks | python -m generate.new_generator NAME
                    python -m validate.hand_check ID --by NAME | python -m validate.run_all
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import traceback
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from generate.registry import discover  # noqa: E402
from runner import (  # noqa: E402
    evaluate, format_check, load_tasks, preflight, score, scorecard, validate_tasks, variation,
)
from runner.config import Config, ConfigError  # noqa: E402
from runner.context import RunContext, StageAbort, StageResult  # noqa: E402
from runner.output import Output  # noqa: E402

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
    """Turn 'all', 'N', 'A-B', or a comma list like '0,2-4' into a sorted list of stage numbers."""
    last = max(STAGE_MODULES)
    if spec.strip().lower() == "all":
        return list(STAGE_MODULES)
    picked: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        try:
            if "-" in part:
                first_s, last_s = part.split("-", 1)
                first, end = int(first_s), int(last_s)
                if not 0 <= first <= end <= last:
                    raise argparse.ArgumentTypeError(f"stage range must be within 0-{last}: {part}")
                picked.update(range(first, end + 1))
            else:
                number = int(part)
                if not 0 <= number <= last:
                    raise argparse.ArgumentTypeError(f"stage must be within 0-{last}: {part}")
                picked.add(number)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a stage number or range: {part!r}") from None
    return sorted(picked)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visual Task Factory runner",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--stage", type=parse_stage_range, default=list(STAGE_MODULES),
                        help="stage number, range like 2-5, comma list, or 'all' (default). "
                             + ", ".join(f"{n}={m.NAME}" for n, m in STAGE_MODULES.items()))
    parser.add_argument("--task", nargs="+", metavar="ID", help="run per-task stages for these task ids only")
    parser.add_argument("--models", help="comma-separated model names (default: from results/run_config.json)")
    parser.add_argument("--dry-run", action="store_true", help="make no API calls")
    parser.add_argument("--verbose", action="store_true", help="print extra detail")
    parser.add_argument("--no-color", action="store_true", help="plain text output")
    parser.add_argument("--strict", action="store_true", help="treat every warning as a failure")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help=f"run config file (default: {CONFIG_PATH})")
    parser.add_argument("--generate", action="store_true", help="run the generators first, then the stages")
    parser.add_argument("--count", type=int, help="with --generate: how many tasks (default: task_count_target)")
    parser.add_argument("--generator", help="with --generate: only this generator name")
    parser.add_argument("--force", action="store_true", help="stage 4: ignore cached replies and call again")
    parser.add_argument("--text-only-baseline", action="store_true",
                        help="stage 2: ask a model each question WITHOUT the image (uses API budget)")
    parser.add_argument("--ignore-preflight", action="store_true", help="continue even if stage 0 has failures")
    parser.add_argument("--selftest", action="store_true", help="end-to-end test with built-in demo generators")
    parser.add_argument("--package", action="store_true", help="secret scan + build the submission zip")
    parser.add_argument("--team", help="with --package: team name (default: team_name in run_config.json)")
    parser.add_argument("--track", type=int, help="with --package: track number")
    parser.add_argument("--wrap-folder", action="store_true", help="with --package: put files in TeamName_TrackN/")
    parser.add_argument("--allow-unchecked", action="store_true", help="with --package: allow tasks not hand-checked")
    args = parser.parse_args(argv)
    args.models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
    return args


def load_config(path: Path) -> Config:
    """Load the run config. Raises ConfigError with a readable message if it cannot be read.

    A --config path that was given explicitly must exist; only the default file may be missing."""
    full = path if path.is_absolute() else ROOT / path
    if path != CONFIG_PATH and not full.exists():
        raise ConfigError(f"config file not found: {path}")
    return Config.load(ROOT, full)


def run_selected_stages(ctx: RunContext, stages: list[int]) -> bool:
    """Run each selected stage, printing its banner and summary. Returns True if the run was aborted."""
    out = ctx.out
    ctx.args.stages_selected = stages
    out.emit(out.c("Visual Task Factory", "bold", "cyan") + out.c(f"  -  stages: {', '.join(map(str, stages))}", "gray"))
    last = max(STAGE_MODULES)
    plan: list[tuple[str, str, object]] = []
    gen_step = ("G", "Generate tasks", None)
    for n in stages:
        plan.append((str(n), STAGE_MODULES[n].NAME, n))
        if n == 0 and getattr(ctx.args, "generate", False):
            plan.append(gen_step)  # generate right after preflight
    if getattr(ctx.args, "generate", False) and gen_step not in plan:
        plan.insert(0, gen_step)
    aborted = False
    for label, name, number in plan:
        out.banner(label, name, total=last)
        try:
            if number is None:
                from generate.generate_tasks import generate_all
                ctx.tasks, _ = generate_all(ctx.cfg, out, ctx.registry, count=ctx.args.count,
                                            only=ctx.args.generator)
                from generate.generate_tasks import _write_review_sheet
                _write_review_sheet(ctx.cfg, out)
                ctx.validation = ctx.records = ctx.rows = ctx.scoring = None  # downstream results are stale
                result = ctx.stage_result(name)
            else:
                result = STAGE_MODULES[number].run(ctx)
                ctx.stage_results[number] = result
        except StageAbort as exc:
            out.fail(f"stopping: {exc}")
            aborted = True
        except Exception:  # noqa: BLE001 - show the problem plainly, not a raw traceback wall
            tail = traceback.format_exc().strip().splitlines()[-6:]
            out.fail(f"stage {label} ({name}) crashed unexpectedly (a bug in the runner or in a generator)")
            for line in tail:
                out.detail(line)
            aborted = True
        out.end_stage()
        if aborted:
            break
        if number == 0 and not ctx.preflight_ok and not ctx.args.ignore_preflight:
            out.emit(out.c("Preflight failed. Fix the [FAIL] lines above (or pass --ignore-preflight), then run again.",
                           "red", "bold"))
            aborted = True
            break
    return aborted


def print_verdict(ctx: RunContext, aborted: bool) -> int:
    out = ctx.out
    totals = out.totals
    bad = totals["fail"] > 0 or aborted or (ctx.strict and totals["warn"] > 0)
    out.emit()
    out.emit(out.c("=" * 78, "cyan"))
    verdict = out.c("RESULT: PROBLEMS FOUND", "red", "bold") if bad else out.c("RESULT: OK", "green", "bold")
    out.emit(f" {verdict}   " + out.c(f"{totals['pass']} pass", "green") + " | " + out.c(f"{totals['warn']} warn", "yellow")
             + " | " + out.c(f"{totals['fail']} fail", "red") + " | " + out.c(f"{totals['skip']} skip", "gray")
             + (out.c("   (--strict: warnings count as failures)", "gray") if ctx.strict else ""))
    out.emit(out.c("=" * 78, "cyan"))
    return EXIT_FAILED if bad else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(ROOT)  # every relative path in the repo is relative to the repo root
    try:
        cfg = load_config(args.config)
    except (OSError, ConfigError) as exc:
        print(f"Cannot load config {args.config}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    preflight.load_dotenv(ROOT)

    log_path = None
    if not args.selftest and not os.environ.get("VTF_NO_LOG"):
        log_path = ROOT / LOG_DIR / f"run-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    out = Output(color=False if args.no_color else None, log_path=log_path, verbose=args.verbose)
    out.emit(out.c(f"=== {dt.datetime.now().isoformat(timespec='seconds')}  python run_tests.py "
                   + " ".join(argv if argv is not None else sys.argv[1:]) + " ===", "gray"))
    try:
        if args.selftest:
            from runner.selftest import run_selftest
            return run_selftest(out, strict=args.strict)
        if args.package:
            from runner import package
            out.banner("P", "Package", "secret scan + submission zip")
            zip_path = package.build_zip(cfg, out, args.team or cfg.get("team_name"),
                                         args.track or cfg.get("track_number"),
                                         wrap_folder=args.wrap_folder, allow_unchecked=args.allow_unchecked)
            out.end_stage()
            return EXIT_OK if zip_path else EXIT_FAILED
        ns = SimpleNamespace(**{k: v for k, v in vars(args).items()})
        ctx = RunContext(cfg=cfg, out=out, registry=discover(), args=ns)
        aborted = run_selected_stages(ctx, args.stage)
        code = print_verdict(ctx, aborted)
        out.info(f"Plain-text log: {log_path.relative_to(ROOT)}") if log_path else None
        return code
    finally:
        out.close()


if __name__ == "__main__":
    sys.exit(main())
