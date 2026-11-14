"""Hand-check ledger (tasks/hand_checks.json).

The rules say every task must be hand-checked. A person records that with:
    python -m validate.hand_check TASK_ID --by "Your Name" --notes "what you looked at"
The ledger stores a fingerprint of the task (question, answer, data file, image file). If any of
those change afterwards, the check goes stale and validation warns until it is redone.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def ledger_path(tasks_dir: Path) -> Path:
    return tasks_dir / "hand_checks.json"


def load(tasks_dir: Path) -> dict:
    p = ledger_path(tasks_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def record(tasks_dir: Path, task_id: str, by: str, notes: str, fingerprint: str) -> dict:
    ledger = load(tasks_dir)
    ledger[task_id] = {
        "by": by,
        "notes": notes,
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "fingerprint": fingerprint,
    }
    p = ledger_path(tasks_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    return ledger[task_id]


def status(ledger: dict, task_id: str, fingerprint: str) -> tuple[str, dict | None]:
    """Returns ("ok" | "stale" | "missing", entry-or-None)."""
    entry = ledger.get(task_id)
    if not entry:
        return "missing", None
    if entry.get("fingerprint") != fingerprint:
        return "stale", entry
    return "ok", entry



def main(argv: list[str] | None = None) -> int:
    """python -m validate.hand_check ID [ID ...] --by NAME [--notes TEXT] [--yes]  (run from anywhere)."""
    import argparse
    import sys

    from runner.config import Config
    from runner.output import UI
    from runner.schema import load_tasks, task_fingerprint

    parser = argparse.ArgumentParser(description="Record that a human hand-checked task(s)")
    parser.add_argument("ids", nargs="+", metavar="ID", help="task ids, or 'all'")
    parser.add_argument("--by", required=True, help="your name")
    parser.add_argument("--notes", default="", help="what you checked")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation question")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    cfg = Config.load(root)
    ui = UI(color=False if args.no_color else None)
    tasks, _ = load_tasks(cfg.tasks_file)
    by_id = {t.id: t for t in tasks}
    ids = list(by_id) if args.ids == ["all"] else args.ids
    missing = [i for i in ids if i not in by_id]
    if missing:
        ui.fail(f"unknown task id(s): {', '.join(missing)}. Known: {', '.join(by_id) or '(none: generate tasks first)'}")
        return 1
    for tid in ids:
        t = by_id[tid]
        ui.heading(f"hand-check {tid}")
        ui.kv("image", cfg.root / t.image_path)
        ui.kv("data", cfg.root / t.data_path)
        ui.kv("question", t.question)
        ui.kv("answer", f"{t.answer!r} ({t.answer_type}" + (f", +/-{t.tolerance:g}" if t.tolerance else "") + ")")
        ui.detail("Open the image. Is the question clear and answerable only from the image? Is the stored "
                  "answer actually right when you read it off the picture? Does the picture match the data?", indent=2)
        if not args.yes and sys.stdin.isatty():
            reply = input("  Have you checked all of that yourself? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                ui.skip(f"{tid}: not recorded")
                continue
        entry = record(cfg.tasks_dir, tid, args.by, args.notes, task_fingerprint(cfg.root, t))
        ui.pass_(f"{tid}: recorded hand-check by {entry['by']} ({entry['date']})")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
