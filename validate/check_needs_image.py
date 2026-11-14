"""'Needs the image' checks (step 5): the question text must not hand over the data.

1. Leak check (always): how much of the saved data (numbers/labels) appears in the question text?
2. Generator hook (optional): generator.question_needs_image().
3. Text-only baseline (opt-in, costs API budget): ask a model the question with NO image; a
   correct answer is a warning sign that the question is answerable without the picture.
"""
from __future__ import annotations

import re
from typing import Any

from generate.base import CheckResult
from validate.context import CheckContext

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _leaves(obj: Any) -> list[Any]:
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _leaves(v)]
    if isinstance(obj, (list, tuple)):
        return [x for v in obj for x in _leaves(v)]
    return [obj]


def _distinct_values(data: Any) -> tuple[set[float], set[str]]:
    nums: set[float] = set()
    strs: set[str] = set()
    for leaf in _leaves(data):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if isinstance(leaf, (int, float)):
            if len(f"{leaf:g}") >= 3:  # ignore tiny numbers like 2 or 5 that appear in any question
                nums.add(float(leaf))
        elif isinstance(leaf, str) and len(leaf.strip()) >= 3:
            strs.add(leaf.strip().lower())
    return nums, strs


def leak_ratio(data: Any, question: str) -> tuple[float | None, list[str]]:
    nums, strs = _distinct_values(data)
    total = len(nums) + len(strs)
    if total == 0:
        return None, []
    q_nums = [float(x) for x in _NUM.findall(question)]
    q_low = question.lower()
    found = [f"{n:g}" for n in nums if any(abs(n - q) < 1e-9 for q in q_nums)]
    found += [s for s in strs if s in q_low]
    return len(found) / total, found


def run(ctx: CheckContext) -> list[tuple[str, CheckResult]]:
    cfg, t = ctx.cfg, ctx.task
    ctx.load_data()
    out: list[tuple[str, CheckResult]] = []
    if ctx.data_error:
        return [("question_needs_image", CheckResult.skip("needs a loadable data file"))]

    ratio, found = leak_ratio(ctx.data, t.question)
    nums, strs = _distinct_values(ctx.data)
    if ratio is None or len(nums) + len(strs) < cfg.get("validation.leak_min_values", 3):
        out.append(("question_needs_image", CheckResult.ok("too few data values to leak; leak check not applicable")))
    elif ratio >= cfg.get("validation.leak_ratio_fail", 0.6):
        out.append(("question_needs_image", CheckResult.fail(
            f"question text contains {ratio:.0%} of the data values (answerable without the image)",
            expected="question that does not list the data", actual=f"leaked: {', '.join(found[:6])}",
            where="generator.make_question()")))
    elif ratio >= cfg.get("validation.leak_ratio_warn", 0.3):
        out.append(("question_needs_image", CheckResult.warn(
            f"question text contains {ratio:.0%} of the data values; check it does not give the answer away",
            actual=f"overlap: {', '.join(found[:6])}", where="generator.make_question()")))
    else:
        out.append(("question_needs_image", CheckResult.ok(
            f"question text shares only {0 if ratio is None else ratio:.0%} of the data values")))

    # Answer written into the question?
    ans = t.answer
    answer_in_q = False
    if t.answer_type == "number" and len(f"{float(ans):g}") >= 3:
        answer_in_q = any(abs(float(ans) - float(x)) < 1e-9 for x in _NUM.findall(t.question))
    elif t.answer_type == "text" and len(str(ans)) >= 3:
        answer_in_q = str(ans).lower() in t.question.lower()
    if answer_in_q:
        out.append(("answer_not_in_question", CheckResult.fail(
            "the reference answer appears verbatim in the question", actual=t.question,
            where="generator.make_question()")))
    else:
        out.append(("answer_not_in_question", CheckResult.ok("the answer does not appear in the question")))

    if ctx.generator is not None:
        try:
            res = ctx.generator.question_needs_image(ctx.data, t.question)
        except Exception as exc:  # noqa: BLE001
            res = CheckResult.fail(f"question_needs_image() raised {type(exc).__name__}: {exc}",
                                   where="generator.question_needs_image()")
        out.append(("generator_needs_image_hook", res))
    return out


def text_only_baseline(cfg, task, client, model, extract, is_correct) -> CheckResult:
    """Opt-in: ask `model` the question WITHOUT the image."""
    prompt = f"{task.question}\n\n{cfg.get('eval.answer_instruction')}"
    params = {
        "temperature": cfg.get("eval.temperature"),
        "reasoning_effort": cfg.get("eval.reasoning_effort"),
        "max_tokens": cfg.get("eval.max_tokens"),
    }
    comp = client.complete(model, [{"role": "user", "content": prompt}], params,
                           meta={"task_id": task.id, "expected": task.answer, "text_only": True})
    if comp.status != "ok":
        return CheckResult.warn(f"text-only baseline call was invalid ({comp.status}); not conclusive")
    got = extract(comp.text)
    correct, _ = is_correct(task.answer_type, task.answer, got, task.tolerance)
    if correct:
        return CheckResult.warn(
            f"{model.label} answered correctly with NO image; the question may be answerable from text "
            "or by guessing", actual=got)
    return CheckResult.ok(f"{model.label} could not answer without the image (answered {got!r})")


def needs_image(task) -> bool:
    """True if the question does not leak the data or the answer (so the image is needed)."""
    from validate.context import legacy_context
    return all(res.status != "fail" for _, res in run(legacy_context(task)))
