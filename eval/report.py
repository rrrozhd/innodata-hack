"""Build the results report from results/responses.csv.

Placeholder only. The report contents and output format are not decided yet.
"""

from pathlib import Path

RESPONSES_PATH = Path("results/responses.csv")


def build_report(responses_path: Path = RESPONSES_PATH, out_path: Path | None = None) -> None:
    """Summarize per-model accuracy and write the report.

    TODO: decide the report format and implement the summary.
    """
    raise NotImplementedError("TODO: build report")


if __name__ == "__main__":
    build_report()
