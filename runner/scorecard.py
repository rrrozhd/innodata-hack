"""Stage 7: rubric scorecard, overall pass or fail, files written, and exit status.

Prints one line per rubric item with the evidence this run produced.
The run's exit code is computed in run_tests.py from the stage results.
"""

from runner.context import RunContext, StageResult

NAME = "Final summary"

# Weights are percentages of the 35-point visual track.
RUBRIC: list[tuple[str, int, str]] = [
    ("Realistic/useful visuals", 20, "stage 2 checks g and h"),
    ("Reliable generation", 20, "stage 1, stage 2 checks a and b"),
    ("Fair/verifiable tasks", 15, "stage 2 checks c, d, e, and f"),
    ("Task difficulty", 15, "stage 5"),
    ("Meaningful variation", 10, "stage 3"),
    ("Model evaluation", 10, "stage 4"),
    ("Runnable submission", 10, "stage 0, stage 6, and this run's exit code"),
]


def build_scorecard(ctx: RunContext) -> list[tuple[str, int, str]]:
    """Return one line per rubric item with the evidence from this run.

    TODO: pull counts and results from ctx.stage_results.
    """
    raise NotImplementedError("TODO: build scorecard")


def list_files_written(ctx: RunContext) -> list[str]:
    """Return the log, raw replies, and responses written by this run.

    TODO: track written paths in RunContext.
    """
    raise NotImplementedError("TODO: list files written")


def run(ctx: RunContext) -> StageResult:
    """Print the scorecard, the overall result, and the files written.

    TODO: implement the printout.
    """
    raise NotImplementedError("TODO: final summary")
