"""Stage 1: run the registered generators and write tasks/ (data, images, tasks.jsonl)."""
from __future__ import annotations

import datetime as dt
import json
import random
import traceback
from pathlib import Path

from runner.config import Config
from runner.schema import SCHEMA_VERSION, Task, load_tasks, save_tasks
from runner.output import UI
from generate.base import task_seed, to_jsonable
from generate.registry import Registry


def _short_error() -> str:
    lines = traceback.format_exc().strip().splitlines()
    return " | ".join(l.strip() for l in lines[-3:])


def _clean_previous(cfg: Config, ui: UI, only: str | None = None) -> None:
    """Delete only files a previous `generate` run created (listed in the old tasks.jsonl).

    With `only`, just that generator's files: other generators' tasks are kept untouched."""
    old, _ = load_tasks(cfg.tasks_file)
    removed = 0
    for t in old:
        if only and t.generator != only:
            continue
        for rel in (t.image_path, t.data_path):
            p = cfg.root / rel
            if p.exists():
                p.unlink()
                removed += 1
        for p in (cfg.tasks_dir / "source").glob(f"{t.id}.*"):  # diagram source copies (e.g. .mmd)
            p.unlink()
            removed += 1
    if removed:
        ui.info(f"removed {removed} file(s) from the previous generation run")


def generate_all(cfg: Config, ui: UI, registry: Registry, count: int | None = None,
                 only: str | None = None) -> tuple[list[Task], dict]:
    gens = [g for n, g in registry.generators.items() if only in (None, n)]
    if not gens:
        if only:
            ui.fail(f"no generator named {only!r}. Registered: {', '.join(registry.generators) or '(none)'}")
        elif registry.disabled:
            ui.fail("no usable generators: " + ", ".join(registry.disabled)
                    + " exist but are disabled. Implement the four required methods, then set "
                      "`enabled = True` in the file.")
        else:
            ui.fail("no usable generators. Add one with: python -m generate.new_generator my_chart_name")
        for problem in registry.problems:
            ui.detail(problem)
        return [], {"failures": [], "generated": 0}

    count = count or cfg.get("task_count_target", 10)
    base_seed = cfg.get("seed", 0)
    images_dir = cfg.tasks_dir / "images"
    data_dir = cfg.tasks_dir / "data"
    images_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    kept = [t for t in load_tasks(cfg.tasks_file)[0] if only and t.generator != only]
    _clean_previous(cfg, ui, only)

    ui.info(f"generating {count} task(s) across {len(gens)} generator(s): "
            + ", ".join(g.name for g in gens) + f"  (base seed {base_seed})")
    tasks: list[Task] = []
    failures: list[dict] = []
    per_gen: dict[str, int] = {}
    counters = {g.name: 0 for g in gens}

    for i in range(count):
        gen = gens[i % len(gens)]
        idx = counters[gen.name]
        counters[gen.name] += 1
        task_id = f"{gen.name}_{idx:03d}"
        seed = task_seed(base_seed, gen.name, idx)
        img_rel = f"{cfg.get('paths.tasks_dir')}/images/{task_id}.{gen.image_ext}"
        data_rel = f"{cfg.get('paths.tasks_dir')}/data/{task_id}.json"
        step = "generate_data"
        try:
            data = to_jsonable(gen.generate_data(random.Random(seed), idx))
            (cfg.root / data_rel).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            data = json.loads((cfg.root / data_rel).read_text(encoding="utf-8"))  # use what was SAVED
            if gen.source_key:
                src = data.get(gen.source_key)
                if not isinstance(src, str) or not src.strip():
                    raise ValueError(f"data[{gen.source_key!r}] must hold the diagram source text, got "
                                     f"{type(src).__name__}; put the source in the data dict under that key")
                (cfg.tasks_dir / "source").mkdir(parents=True, exist_ok=True)
                (cfg.tasks_dir / "source" / f"{task_id}.{gen.source_ext}").write_text(
                    src.rstrip("\n") + "\n", encoding="utf-8")
            step = "render"
            gen.render(data, cfg.root / img_rel)
            if not (cfg.root / img_rel).exists():
                raise FileNotFoundError(f"render() did not create {img_rel}")
            step = "compute_answer"
            answer = gen.coerce_answer(gen.compute_answer(data))
            step = "make_question"
            question = gen.build_question(data)
        except Exception:
            err = _short_error()
            failures.append({"task_id": task_id, "step": step, "error": err})
            ui.fail(f"{task_id}: failed in {step}()")
            ui.detail(err)
            continue
        tasks.append(Task(
            id=task_id, generator=gen.name, index=idx, seed=seed, visual_type=gen.visual_type,
            question_type=gen.question_type, image_path=img_rel, data_path=data_rel,
            question=question, answer=answer, answer_type=gen.answer_type,
            tolerance=gen.tolerance_for(data), real_world_examples=list(gen.real_world_examples),
            schema_version=SCHEMA_VERSION,
        ))
        per_gen[gen.name] = per_gen.get(gen.name, 0) + 1
        ui.pass_(f"{task_id}: data -> image -> answer -> question")
        ui.detail(f"answer = {answer!r} ({gen.answer_type}"
                  + (f", tolerance +/-{gen.tolerance_for(data):g}" if gen.tolerance_for(data) is not None else "") + ")")
        ui.verbose_detail(f"question: {question}")

    if kept:
        ui.info(f"kept {len(kept)} existing task(s) from other generators (only {only!r} was regenerated)")
    save_tasks(cfg.tasks_file, kept + tasks)
    report = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "requested": count, "generated": len(tasks), "failures": failures,
        "per_generator": per_gen, "seed": base_seed,
    }
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "generation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    ui.info(f"wrote {cfg.tasks_file.relative_to(cfg.root)} ({len(tasks)} task(s)) and generation_report.json")
    return kept + tasks, report


def _write_review_sheet(cfg: Config, ui: UI) -> None:
    """Refresh tasks/review.html and tasks/REVIEW.md (a human-readable view of the tasks). Never breaks generation."""
    try:
        from generate import review_sheet
        if review_sheet.build(cfg.root):
            ui.info("wrote tasks/review.html and tasks/REVIEW.md (pictures + questions + answers for human checking)")
    except Exception as exc:  # noqa: BLE001
        ui.warn(f"could not write the review sheet: {exc}")


def main(argv: list[str] | None = None) -> int:
    """python -m generate.generate_tasks [--count N] [--generator NAME] [--no-color]  (run from anywhere)."""
    import argparse
    import sys

    from runner.preflight import load_dotenv
    from generate.registry import discover

    parser = argparse.ArgumentParser(description="Run the registered generators and write tasks/")
    parser.add_argument("--count", type=int, help="how many tasks (default: task_count_target in run_config.json)")
    parser.add_argument("--generator", help="only this generator name")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    cfg = Config.load(root)
    load_dotenv(root)
    ui = UI(color=False if args.no_color else None)
    ui.banner("G", "Generate tasks", "run the generators -> tasks/")
    _, report = generate_all(cfg, ui, discover(), count=args.count, only=args.generator)
    _write_review_sheet(cfg, ui)
    ui.end_stage()
    return 1 if ui.totals["fail"] else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
