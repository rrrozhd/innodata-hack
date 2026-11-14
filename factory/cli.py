"""Command line for the whole pipeline.

    python -m factory fresh --seed 7                 build, validate, evaluate and score one new task
    python -m factory reproduce                      rebuild the reported tasks from seeds, then evaluate and score
    python -m factory generate ...                   build a task set
    python -m factory validate | selftest | eval | curate | gallery | handcheck | check | zip
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from . import GENERATOR_VERSION
from .build import BuildError, build_task, load_task_set, write_task_set
from .core import registry

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS = "anthropic/claude-haiku-5.5,openai/gpt-6-luna"


def _ints(spec: str) -> list:
    out = []
    for part in str(spec).split(","):
        if "-" in part.strip()[1:]:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def _settings(a):
    from eval.client import Settings
    return Settings(base_url=a.base_url, key_env=a.key_env, max_tokens=a.max_tokens)


def _add_model_args(p, default_models=DEFAULT_MODELS):
    p.add_argument("--models", default=default_models,
                   help="comma-separated model IDs; mock:oracle, mock:blind and mock:shortcut:<id> run offline")
    p.add_argument("--base-url", default="https://openrouter.ai/api/v1", help="OpenAI-compatible endpoint")
    p.add_argument("--key-env", default="OPENROUTER_API_KEY", help="environment variable holding the API key")
    p.add_argument("--max-tokens", type=int, default=64000)
    p.add_argument("--workers", type=int, default=4)


def _build_many(spec: list) -> list:
    tasks = []
    for s in spec:
        try:
            t = build_task(s["program"], s["seed"], s["level"], s.get("variant", ""))
        except BuildError as e:
            print(f"  SKIPPED  {e}")
            continue
        r = t["record"]
        note = f" after {len(t['rejected'])} rejected attempt(s)" if t["rejected"] else ""
        print(f"  built    {r['task_id']}  answer {r['answer']}{note}")
        tasks.append(t)
    return tasks


def cmd_generate(a) -> int:
    variants = a.variants.split(",") if a.variants else [""]
    spec = [{"program": p, "seed": s, "level": lv, "variant": v}
            for p in a.program.split(",") for lv in _ints(a.levels) for s in _ints(a.seeds) for v in variants]
    tasks = _build_many(spec)
    write_task_set(Path(a.out), tasks)
    print(f"{len(tasks)} of {len(spec)} tasks valid and written to {a.out}")
    return 0 if len(tasks) == len(spec) else 1


def cmd_validate(a) -> int:
    from validate.run_validate import main
    return main(["--tasks", a.tasks])


def cmd_selftest(a) -> int:
    from validate.selftest import main
    return main(["--tasks", a.tasks, "--limit", str(a.limit)])


def _evaluate(tasks_dir, results_dir, a) -> int:
    from eval import report
    from eval.run_eval import evaluate
    out = evaluate(tasks_dir, results_dir, [m for m in a.models.split(",") if m], _settings(a), a.workers)
    print(report.markdown(out["summary"]))
    print(f"results written to {results_dir}")
    if not out["config"]["complete"]:
        print("RUN INCOMPLETE: " + "; ".join(out["config"]["incomplete_rows"][:5]), file=sys.stderr)
        return 1
    return 0


def cmd_eval(a) -> int:
    return _evaluate(a.tasks, a.results, a)


def cmd_fresh(a) -> int:
    """Build one fresh task from a seed, validate it, evaluate the models on it and score them."""
    names = sorted(registry.programs())
    program = names[a.seed % len(names)] if a.program == "auto" else a.program
    try:
        t = build_task(program, a.seed, a.level, a.variant)
    except BuildError as e:
        print(f"no valid task for this seed: {e}")
        return 1
    name = f"fresh_s{a.seed}"
    tasks_dir, results_dir = Path(a.out) / "tasks" / name, Path(a.out) / "results" / name
    write_task_set(tasks_dir, [t])
    r = t["record"]
    print(f"task      {r['task_id']}\nimage     {tasks_dir / r['image']}\nanswer    {r['answer']}\n")
    print(r["question"] + "\n")
    for c in t["report"]["checks"]:
        print(f"  {c['status']:7s} {c['name']:24s} {c['detail']}")
    print()
    return _evaluate(tasks_dir, results_dir, a)


def cmd_rebuild(a) -> int:
    """Rebuild a task folder in place from the seeds in its manifest."""
    import shutil
    folder = Path(a.tasks)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    tasks = _build_many(manifest["tasks"])
    for sub_dir in ("images", "data", "checks"):
        shutil.rmtree(folder / sub_dir, ignore_errors=True)
    extra = {k: v for k, v in manifest.items() if k not in ("tasks", "generator_version")}
    write_task_set(folder, tasks, manifest["tasks"])
    if extra:  # keep the selection record written by `curate`
        m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        m.update(extra)
        with open(folder / "manifest.json", "w", encoding="utf-8", newline=chr(10)) as fh:
            fh.write(json.dumps(m, indent=1, sort_keys=True) + chr(10))
    print(f"{len(tasks)} of {len(manifest['tasks'])} tasks rebuilt in {folder}")
    return 0 if len(tasks) == len(manifest["tasks"]) else 1


def cmd_reproduce(a) -> int:
    """Rebuild the reported tasks from their seeds, compare with the shipped set, then evaluate and score."""
    shipped = Path(a.tasks)
    manifest = json.loads((shipped / "manifest.json").read_text(encoding="utf-8"))
    if manifest["generator_version"] != GENERATOR_VERSION:
        print(f"warning: tasks were built with generator {manifest['generator_version']}, this is "
              f"{GENERATOR_VERSION}")
    print(f"rebuilding {len(manifest['tasks'])} tasks from seeds")
    tasks = _build_many(manifest["tasks"])
    out_tasks = Path(a.out) / "tasks" / shipped.name
    write_task_set(out_tasks, tasks, manifest["tasks"])
    old = {rec["task_id"]: rec for rec, _ in load_task_set(shipped)}
    same = same_file = 0
    for t in tasks:
        new, ref = t["record"], old.get(t["record"]["task_id"])
        diffs = [k for k in ("question", "answer", "pixel_sha256") if ref is None or new[k] != ref[k]]
        same += not diffs
        same_file += ref is not None and new["image_sha256"] == ref["image_sha256"]
        if diffs:
            print(f"  DIFFERS  {new['task_id']}: {', '.join(diffs) if ref else 'not in the shipped set'}")
    identical = same == len(old) == len(tasks)
    print(f"{same} of {len(old)} rebuilt tasks are identical to the shipped ones (same image pixels, question "
          f"and reference answer); {same_file} PNG files are also byte-identical")
    print()
    code = _evaluate(out_tasks, Path(a.out) / "results" / shipped.name, a)
    return code if identical else 1


def cmd_curate(a) -> int:
    from eval.curate import main
    return main(["--tasks", a.tasks, "--results", a.results, "--out", a.out, "--n", str(a.n)]
                + (["--require-handcheck"] if a.require_handcheck else []))


def cmd_gallery(a) -> int:
    from .gallery import write_gallery
    path = write_gallery(Path(a.tasks), Path(a.results) if a.results else None)
    print(f"gallery written to {path}")
    return 0


def cmd_handcheck(a) -> int:
    from eval.handcheck import main
    return main(["--tasks", a.tasks])


SKIP_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "out", ".claude", ".vscode",
              ".idea", "archive"}
SKIP_NAMES = {"gallery.html", "handcheck.json"}


def _write_zip(target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if (p.is_dir() or set(rel.parts) & SKIP_PARTS or p.suffix == ".zip" or p.name.startswith(".env")
                    or p.name in SKIP_NAMES):
                continue
            zf.write(p, rel.as_posix())


def _checker(path: Path, extra=()) -> int:
    # On Windows the unmodified checker mixes backslash and forward-slash paths; tools/check_posix.py
    # runs the same checker with POSIX path handling, as on the judges' machines.
    script = "check_posix.py" if sys.platform.startswith("win") else "check_submission.py"
    return subprocess.call([sys.executable, str(ROOT / "tools" / script), str(path), *extra])


def cmd_check(a) -> int:
    """Run the organizers' checker. A folder is zipped to a temporary file first: on Windows the checker
    compares backslash paths with forward-slash names when it is given a folder, and reports false failures."""
    path = Path(a.path)
    if path.is_file():
        return _checker(path)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "Check_Track2.zip"
        _write_zip(target)
        return _checker(target)


def cmd_zip(a) -> int:
    cfg = ROOT / "results" / "final" / "run_config.json"
    if cfg.exists() and json.loads(cfg.read_text(encoding="utf-8")).get("placeholder") and not a.allow_placeholder:
        print("refusing to zip: results/final holds placeholder results from mock backends, not model results")
        return 1
    target = ROOT / f"{a.team}_Track2.zip"
    _write_zip(target)
    print(f"wrote {target}")
    return _checker(target)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="factory", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    programs = sorted(registry.programs())

    p = sub.add_parser("fresh", help="build, validate, evaluate and score one fresh task")
    p.add_argument("--seed", type=int, required=True, help="any integer; the same seed always gives the same task")
    p.add_argument("--program", default="auto", choices=["auto"] + programs,
                   help="question type; auto picks one from the seed")
    p.add_argument("--level", type=int, default=4, choices=range(1, 6))
    p.add_argument("--variant", default="")
    p.add_argument("--out", default="out")
    _add_model_args(p)
    p.set_defaults(fn=cmd_fresh)

    p = sub.add_parser("rebuild", help="rebuild a task folder in place from its manifest (run this inside "
                                       "Docker to make the canonical reported set)")
    p.add_argument("--tasks", default=str(ROOT / "tasks" / "final"))
    p.set_defaults(fn=cmd_rebuild)

    p = sub.add_parser("reproduce", help="rebuild the reported tasks from seeds, evaluate and score")
    p.add_argument("--tasks", default=str(ROOT / "tasks" / "final"))
    p.add_argument("--out", default="out")
    _add_model_args(p)
    p.set_defaults(fn=cmd_reproduce)

    p = sub.add_parser("generate", help="build a task set")
    p.add_argument("--out", required=True)
    p.add_argument("--program", default=programs[0], help="comma-separated program names")
    p.add_argument("--levels", default="1,2,3,4,5")
    p.add_argument("--seeds", required=True, help="for example 1000-1009 or 1000,1004")
    p.add_argument("--variants", default="", help="for example edge_armed,edge_disarmed")
    p.set_defaults(fn=cmd_generate)

    p = sub.add_parser("validate", help="validate every task in a folder")
    p.add_argument("--tasks", required=True)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("selftest", help="corrupt valid tasks and confirm the validators reject them")
    p.add_argument("--tasks", required=True)
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(fn=cmd_selftest)

    p = sub.add_parser("eval", help="evaluate models on a task folder")
    p.add_argument("--tasks", required=True)
    p.add_argument("--results", required=True)
    _add_model_args(p)
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("curate", help="select the final showcase from an evaluated task set")
    p.add_argument("--tasks", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--out", default=str(ROOT / "tasks" / "final"))
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--require-handcheck", action="store_true")
    p.set_defaults(fn=cmd_curate)

    p = sub.add_parser("gallery", help="write an HTML page showing every task in a folder")
    p.add_argument("--tasks", required=True)
    p.add_argument("--results", default="")
    p.set_defaults(fn=cmd_gallery)

    p = sub.add_parser("handcheck", help="answer each task yourself before seeing the stored answer")
    p.add_argument("--tasks", required=True)
    p.set_defaults(fn=cmd_handcheck)

    p = sub.add_parser("check", help="run the organizers' submission format checker")
    p.add_argument("path", nargs="?", default=str(ROOT))
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("zip", help="package the submission and run the checker on it")
    p.add_argument("--team", required=True)
    p.add_argument("--allow-placeholder", action="store_true")
    p.set_defaults(fn=cmd_zip)

    a = ap.parse_args(argv)
    return a.fn(a)
