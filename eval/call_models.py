"""Call the evaluated models through OpenRouter and save raw responses.

Settings come from results/run_config.json (temperature 0, high reasoning,
a fixed token limit). The API key is read from the OPENROUTER_API_KEY
environment variable only. Raw replies are saved under results/raw/.

Placeholder only. Model slugs and limits are not set yet.
"""

from pathlib import Path
from typing import Any

RUN_CONFIG_PATH = Path("results/run_config.json")
RAW_DIR = Path("results/raw")


def load_run_config(path: Path = RUN_CONFIG_PATH) -> dict[str, Any]:
    """Load the run configuration.

    TODO: validate required fields once they are confirmed.
    """
    raise NotImplementedError("TODO: load run config")


def call_model(
    model_slug: str,
    prompt: str,
    image_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Send one task to one model and return the raw reply and metadata.

    TODO: implement the OpenRouter request (temperature 0, reasoning, max tokens).
    """
    raise NotImplementedError("TODO: call model via OpenRouter")


def save_raw_response(task_id: str, model_slug: str, reply: dict[str, Any]) -> Path:
    """Write one raw reply to results/raw/ and return its path.

    TODO: decide the file naming scheme.
    """
    raise NotImplementedError("TODO: save raw response")


def main() -> None:
    """Run every task against every configured model.

    TODO: implement the loop over tasks and models.
    """
    raise NotImplementedError("TODO: run all models over all tasks")


if __name__ == "__main__":
    main()
