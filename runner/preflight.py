"""Stage 0: preflight checks before any work starts.

Rubric: runnable submission.
Prints environment names and whether they are set. Never prints values.
"""

from pathlib import Path

from runner.context import RunContext, StageResult

NAME = "Preflight"
REQUIRED_PATHS = [Path("tasks/tasks.jsonl"), Path("results/run_config.json")]
REQUIRED_ENV_VARS = ["OPENROUTER_API_KEY"]


def check_python_version() -> bool:
    """Return True if Python is 3.12 or newer.

    TODO: implement the version check.
    """
    raise NotImplementedError("TODO: check Python version")


def check_paths(paths: list[Path]) -> list[Path]:
    """Return the required paths that are missing.

    TODO: implement the existence check.
    """
    raise NotImplementedError("TODO: check required paths")


def check_env_vars(names: list[str]) -> dict[str, bool]:
    """Return whether each variable is set. Never return or print values.

    TODO: implement the presence check.
    """
    raise NotImplementedError("TODO: check env var presence")


def run(ctx: RunContext) -> StageResult:
    """Run all preflight checks and print one line per check.

    TODO: implement the checks and count pass, fail, and warn.
    """
    raise NotImplementedError("TODO: preflight checks")
