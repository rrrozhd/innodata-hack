"""Stage 3: variation report (rubric: meaningful variation)."""
from __future__ import annotations

import difflib
import hashlib
from collections import Counter

from runner.config import Config
from runner.context import RunContext, StageResult
from runner.schema import Task
from runner.output import UI

NAME = "Variation report"


def describe_task(task: Task) -> dict[str, str]:
    """Visual type, data shape (answer type), question type, generator and question text for one task."""
    return {"visual_type": task.visual_type, "data_shape": task.answer_type,
            "question_type": task.question_type, "generator": task.generator, "question": task.question}


def find_near_duplicates(descriptions: dict[str, dict[str, str]], threshold: float) -> list[tuple[str, str, float]]:
    """Pairs of task ids with the same question type and question text at least `threshold` similar."""
    ids = list(descriptions)
    pairs = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = descriptions[ids[i]], descriptions[ids[j]]
            if a["question_type"] != b["question_type"]:
                continue
            ratio = difflib.SequenceMatcher(None, a["question"], b["question"]).ratio()
            if ratio >= threshold:
                pairs.append((ids[i], ids[j], ratio))
    return pairs


def analyze(cfg: Config, ui: UI, tasks: list[Task]) -> dict:
    n = len(tasks)
    if not n:
        ui.warn("no tasks to analyze")
        return {"ok": False, "evidence": ["no tasks"], "visual_types": 0, "generators": 0}
    vcfg = cfg.get("variation", {})
    evidence: list[str] = []
    ok = True

    def show(title: str, counter: Counter) -> None:
        ui.heading(title)
        ui.table(["value", "tasks", "share"], [[k, str(v), f"{v / n:.0%}"] for k, v in counter.most_common()])
        ui.blank()

    by_gen = Counter(t.generator for t in tasks)
    by_vt = Counter(t.visual_type for t in tasks)
    by_qt = Counter(t.question_type for t in tasks)
    by_at = Counter(t.answer_type for t in tasks)
    show("By generator", by_gen)
    show("By visual type", by_vt)
    show("By question type", by_qt)
    show("By answer type", by_at)

    min_vt = vcfg.get("min_distinct_visual_types", 2)
    if len(by_vt) >= min_vt:
        ui.pass_(f"{len(by_vt)} distinct visual type(s) (minimum {min_vt})")
    else:
        ok = False
        ui.warn(f"only {len(by_vt)} distinct visual type(s); aim for at least {min_vt} for meaningful variation")
    top_share = by_gen.most_common(1)[0][1] / n
    max_share = vcfg.get("max_share_single_generator", 0.7)
    if top_share <= max_share:
        ui.pass_(f"no single generator makes more than {max_share:.0%} of the tasks (largest: {top_share:.0%})")
    else:
        ok = False
        ui.warn(f"one generator makes {top_share:.0%} of the tasks (limit {max_share:.0%}); add another "
                "generator or vary this one's layout/question")

    # Identical underlying data?
    hashes: dict[str, list[str]] = {}
    for t in tasks:
        p = cfg.root / t.data_path
        if p.exists():
            hashes.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(t.id)
    dupes = [ids for ids in hashes.values() if len(ids) > 1]
    if dupes:
        ok = False
        ui.warn(f"{len(dupes)} group(s) of tasks share IDENTICAL data: " + "; ".join(", ".join(d) for d in dupes[:3]))
    else:
        ui.pass_("every task has different underlying data")

    # Near-identical questions within one question type (templates are similar by nature)
    thr = vcfg.get("near_duplicate_ratio", 0.9) if "near_duplicate_ratio" in vcfg else cfg.get("validation.near_duplicate_ratio", 0.9)
    near = [(a, b) for a, b, _ in find_near_duplicates({t.id: describe_task(t) for t in tasks}, thr)]
    if near:
        ui.warn(f"{len(near)} task pair(s) have near-identical question text (>= {thr:.0%} similar), e.g. "
                + ", ".join(f"{a}/{b}" for a, b in near[:3]))
        ui.detail("Same-template questions are fine if the picture differs a lot, but vary the wording, the "
                  "thing being asked, or the layout so the set is meaningfully varied.")
    else:
        ui.pass_("no near-identical question texts")

    answers = Counter(str(t.answer) for t in tasks)
    common, count = answers.most_common(1)[0]
    if n >= 4 and count / n >= 0.5:
        ok = False
        ui.warn(f"{count}/{n} tasks have the same answer ({common!r}); models could guess it")
    else:
        ui.pass_("answers are varied")

    evidence = [f"{len(by_gen)} generator(s), {len(by_vt)} visual type(s), {len(by_qt)} question type(s) across {n} tasks"]
    return {"ok": ok, "evidence": evidence, "visual_types": len(by_vt), "generators": len(by_gen)}


def run(ctx: RunContext) -> StageResult:
    """Print the variation summary and any near-duplicate warnings."""
    from runner.context import StageAbort
    tasks = ctx.selected_tasks()
    if not tasks:
        raise StageAbort("no tasks loaded; run the generators first (python -m generate.generate_tasks)")
    ctx.variation = analyze(ctx.cfg, ctx.out, tasks)
    return ctx.stage_result(NAME)
