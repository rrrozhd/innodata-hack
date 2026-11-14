"""Rubric scorecard: for each judged item, the EVIDENCE this run produced.

This reports readiness, not points. READY = the measurable checks are satisfied.
ATTENTION = something measurable is missing. HUMAN = needs your team's judgement (the numbers are
shown). NOT RUN = the stage that produces the evidence has not run in this invocation or earlier.
"""
from __future__ import annotations

import json

from runner.context import RunContext, StageResult
from runner.output import UI

NAME = "Final summary"

# Weights are percentages of the 35-point visual track (as listed in build()).
RUBRIC = [
    ("Realistic/useful visuals", 20), ("Reliable generation", 20), ("Fair/verifiable tasks", 15),
    ("Task difficulty", 15), ("Meaningful variation", 10), ("Model evaluation", 10), ("Runnable submission", 10),
]

PLACEHOLDER_MARKER = "<!-- PILOT_RESULTS_PLACEHOLDER -->"


def _item(items: list, name: str, weight: int, status: str, evidence: list[str]) -> None:
    items.append({"name": name, "weight": weight, "status": status, "evidence": evidence})


def build(ctx) -> list[dict]:
    cfg = ctx.cfg
    tasks = ctx.tasks or []
    val = ctx.validation
    n = len(tasks)
    items: list[dict] = []

    # 1. Realistic, useful visuals (20%)
    if not n:
        _item(items, "Realistic, useful visuals", 20, "NOT RUN", ["no tasks loaded"])
    else:
        cited = sum(1 for t in tasks if 1 <= len([e for e in t.real_world_examples if e.strip()]) <= 2)
        vts = sorted({t.visual_type for t in tasks})
        _item(items, "Realistic, useful visuals", 20, "ATTENTION" if cited < n else "HUMAN", [
            f"{cited}/{n} tasks cite one or two real-world examples",
            f"visual types: {', '.join(vts)}",
            "Judgement call: would a real person plausibly use these visuals? Make sure the README says why.",
        ])

    # 2. Reliable generation (20%)
    gen_report = {}
    gp = cfg.results_dir / "generation_report.json"
    if gp.exists():
        try:
            gen_report = json.loads(gp.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    if val is None:
        _item(items, "Reliable generation", 20, "NOT RUN", ["run the validate stage"])
    else:
        fails = len(gen_report.get("failures", []))
        repro = val.count_status("data_reproducible", "pass")
        ev = [f"{len(val.valid_ids)}/{n} tasks pass every check",
              f"{repro}/{n} regenerate identically from their seed",
              f"generation failures in the last generate run: {fails}"]
        _item(items, "Reliable generation", 20,
              "READY" if (not val.invalid_ids and repro == n and fails == 0) else "ATTENTION", ev)

    # 3. Fair, verifiable tasks (15%)
    if val is None:
        _item(items, "Fair, verifiable tasks", 15, "NOT RUN", ["run the validate stage"])
    else:
        want = ("answer_recomputes", "render_matches_data", "question_needs_image", "answer_not_in_question",
                "hand_checked")
        ev, good = [], True
        for check in want:
            c = val.count_status(check, "pass")
            ev.append(f"{check}: {c}/{n} pass")
            good &= (c == n)
        tol_fail = val.count_status("tolerance_stated", "fail")
        ev.append(f"tolerance stated where used: {'yes' if tol_fail == 0 else str(tol_fail) + ' task(s) missing it'}")
        good &= tol_fail == 0
        _item(items, "Fair, verifiable tasks", 15, "READY" if good else "ATTENTION", ev)

    # 4. Task difficulty (15%)
    s = ctx.scoring
    if not s or all(m["valid"] == 0 for m in s["per_model"].values()):
        _item(items, "Task difficulty", 15, "NOT RUN", ["run the evaluate and score stages"])
    else:
        c = s["counts"]
        total = max(1, sum(c.values()))
        ev = [f"{c['both_pass']} both-pass, {c['split']} split, {c['both_fail']} both-fail, {c['incomplete']} incomplete"]
        ev += [f"{label}: {m['correct']}/{m['valid']} correct" for label, m in s["per_model"].items()]
        too_easy = c["both_pass"] / total >= 0.8
        too_hard = c["both_fail"] / total >= 0.5
        if too_easy:
            ev.append("most tasks are solved by both models: probably too easy")
        if too_hard:
            ev.append("most tasks are failed by both models: confirm they are fair, not just broken")
        _item(items, "Task difficulty", 15, "ATTENTION" if (too_easy or too_hard) else "READY", ev)

    # 5. Meaningful variation (10%)
    v = ctx.variation
    if not v:
        _item(items, "Meaningful variation", 10, "NOT RUN", ["run the variation stage"])
    else:
        _item(items, "Meaningful variation", 10, "READY" if v["ok"] else "ATTENTION", v["evidence"])

    # 6. Model evaluation (10%)
    recs = ctx.records
    if not recs or all(r.status == "not_run" for r in recs):
        _item(items, "Model evaluation", 10, "NOT RUN", ["run the evaluate stage with real model ids and keys"])
    else:
        models = cfg.models()
        bad = [r for r in recs if r.status != "ok"]
        ev = [f"{len(recs) - len(bad)}/{len(recs)} (task, model) responses are valid",
              f"models: {', '.join(m.label + '=' + m.id for m in models)}",
              f"temperature={cfg.get('eval.temperature')}, reasoning={cfg.get('eval.reasoning_effort')}, "
              f"max_tokens={cfg.get('eval.max_tokens')}"]
        settings_ok = cfg.get("eval.temperature") == 0 and str(cfg.get("eval.reasoning_effort")).lower() == "high"
        if bad:
            ev.append(f"{len(bad)} response(s) invalid (API error / cutoff / empty): not accepted, re-run them")
        if not settings_ok:
            ev.append("settings differ from the brief (temperature 0, reasoning high)")
        _item(items, "Model evaluation", 10,
              "READY" if (not bad and settings_ok and not any(m.is_placeholder for m in models)) else "ATTENTION", ev)

    # 7. Runnable submission (10%)
    ev, good = [], True
    if ctx.preflight_ok is None:
        ev.append("preflight not run in this invocation")
        good = False
    elif not ctx.preflight_ok:
        ev.append("preflight reported failures")
        good = False
    else:
        ev.append("preflight passed")
    readme = cfg.root / "README.md"
    if readme.exists() and PLACEHOLDER_MARKER in readme.read_text("utf-8"):
        ev.append("README still contains the pilot-results placeholder: paste in results/summary.md")
        good = False
    elif not readme.exists():
        ev.append("README.md missing")
        good = False
    else:
        ev.append("README pilot-results section filled in")
    fc = ctx.format_check
    if fc is None:
        ev.append("organizer format checker not configured (submission.format_checker_command)")
        good = False
    else:
        ev.append("organizer format checker: " + ("passed" if fc else "FAILED"))
        good &= bool(fc)
    _item(items, "Runnable submission", 10, "READY" if good else "ATTENTION", ev)
    return items


def print_scorecard(ui: UI, items: list[dict]) -> None:
    colors = {"READY": "green", "ATTENTION": "yellow", "HUMAN": "cyan", "NOT RUN": "gray"}
    for it in items:
        tag = ui.c(f"[{it['status']}]", colors[it["status"]], "bold")
        ui.emit(f" {tag} {ui.c(it['name'], 'bold')}  {ui.c('(' + str(it['weight']) + '%)', 'gray')}")
        for line in it["evidence"]:
            ui.detail(line, indent=6)


def list_files_written(ctx: RunContext) -> list[str]:
    """The log, raw replies, and responses written by this run (de-duplicated, in order)."""
    return list(dict.fromkeys(ctx.files_written))


def run(ctx: RunContext) -> StageResult:
    """Print the scorecard, the overall result, and the files written."""
    out = ctx.out
    out.blank()
    out.heading("Rubric scorecard (evidence from this run; not a predicted score)")
    print_scorecard(out, build(ctx))
    written = list_files_written(ctx)
    if written:
        out.blank()
        out.heading("Files written this run")
        for f in written:
            out.detail(f, indent=2)
    return ctx.stage_result(NAME)


def build_scorecard(ctx: RunContext) -> list[tuple[str, int, str]]:
    """(rubric item, weight %, evidence line) for each item, from this run's results."""
    return [(i["name"], i["weight"], f"[{i['status']}] " + "; ".join(i["evidence"])) for i in build(ctx)]
