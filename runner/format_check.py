"""Stage 6: run the organizer's format checker, if one is configured.

Rubric: runnable submission.
The command comes from results/run_config.json -> format_checker {"enabled": true, "command": "..."}.
{root} in the command is replaced by the repo root. Skipped with a warning while it is disabled.
"""
from __future__ import annotations

import shlex
import subprocess

from runner.context import RunContext, StageResult

NAME = "Format checker"


def run_checker(command: str, ctx: RunContext) -> int:
    """Run the checker command from the repo root and return its exit code (prints the output tail)."""
    proc = subprocess.run(shlex.split(command.replace("{root}", str(ctx.cfg.root))), cwd=ctx.cfg.root,
                          capture_output=True, text=True, timeout=300)
    for line in (proc.stdout + proc.stderr).strip().splitlines()[-15:]:
        ctx.out.detail(line)
    return proc.returncode


def run(ctx: RunContext) -> StageResult:
    """Run the configured checker and report its result."""
    out = ctx.out
    command = (ctx.cfg.get("submission.format_checker_command", "") or "").strip()
    if not command:
        out.warn("organizer format checker not configured. Set results/run_config.json -> format_checker "
                 '{"enabled": true, "command": "python /path/to/checker.py {root}"}')
        return ctx.stage_result(NAME)
    try:
        code = run_checker(command, ctx)
    except Exception as exc:  # noqa: BLE001
        ctx.format_check = False
        out.fail(f"could not run the format checker: {exc}")
        return ctx.stage_result(NAME)
    ctx.format_check = code == 0
    (out.pass_ if ctx.format_check else out.fail)(
        f"organizer format checker {'passed' if ctx.format_check else 'FAILED'} (exit code {code})")
    return ctx.stage_result(NAME)
