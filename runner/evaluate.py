"""Stage 4: run both models on every valid task.

Rubric: model evaluation.
Settings come from results/run_config.json: temperature 0, high reasoning, and a fixed token limit.
Invalid replies (API errors, token cutoffs) are marked invalid and rerun, not counted.
Dry-run makes no API calls.
"""

from typing import Any

from runner.context import RunContext, StageResult

NAME = "Model evaluation"
MAX_RETRIES = 2


def is_invalid_reply(reply: dict[str, Any]) -> str | None:
    """Return the invalid reason (api_error, token_cutoff) or None if the reply is valid.

    TODO: read the finish reason and error fields from the OpenRouter response.
    """
    raise NotImplementedError("TODO: detect invalid replies")


def call_model(task_id: str, model: str, ctx: RunContext) -> dict[str, Any]:
    """Send one task to one model and return the reply with token counts and cost.

    TODO: implement the OpenRouter request. Must be skipped when ctx.dry_run is True.
    """
    raise NotImplementedError("TODO: call model")


def call_with_retries(task_id: str, model: str, ctx: RunContext) -> dict[str, Any]:
    """Call the model, rerunning up to MAX_RETRIES times while the reply is invalid.

    TODO: record every attempt in the saved raw output.
    """
    raise NotImplementedError("TODO: call with retries")


def save_raw(task_id: str, model: str, reply: dict[str, Any], ctx: RunContext) -> str:
    """Write one raw reply under results/raw/ and return its path.

    TODO: decide the file naming scheme.
    """
    raise NotImplementedError("TODO: save raw reply")


def run(ctx: RunContext) -> StageResult:
    """Run every valid task against every model, printing progress and the running cost.

    TODO: implement the loop, progress lines, and dry-run output.
    """
    raise NotImplementedError("TODO: model evaluation")
