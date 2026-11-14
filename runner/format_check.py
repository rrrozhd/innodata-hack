"""Stage 6: run the organizer's format checker, if one is configured.

Rubric: runnable submission.
The checker is a command-line tool. Its command comes from run_config.json ("format_checker").
Skipped with a warning when it is disabled.
"""

from runner.context import RunContext, StageResult

NAME = "Format checker"


def run_checker(command: str, ctx: RunContext) -> int:
    """Run the checker command and return its exit code.

    TODO: confirm the checker's invocation and output format.
    """
    raise NotImplementedError("TODO: run format checker")


def run(ctx: RunContext) -> StageResult:
    """Run the configured checker and report its result.

    TODO: read the command from ctx.config["format_checker"] and handle the disabled case.
    """
    raise NotImplementedError("TODO: format checker stage")
