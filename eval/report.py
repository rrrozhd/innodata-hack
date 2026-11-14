"""Turn scored rows into the numbers and tables that get reported."""
from __future__ import annotations

from collections import defaultdict


def _acc(rows) -> dict:
    done = [r for r in rows if r["correct"] in ("0", "1")]
    right = sum(1 for r in done if r["correct"] == "1")
    return {"n": len(done), "correct": right, "accuracy": round(right / len(done), 4) if done else None}


def summarise(rows: list, tasks: list, config: dict) -> dict:
    chance = {rec["task_id"]: data.get("chance_fingerprint_match") for rec, data in tasks}
    models = list(dict.fromkeys(r["model"] for r in rows))
    out = {"complete": config["complete"], "placeholder": config["placeholder"], "n_tasks": len(tasks),
           "total_cost_usd": config.get("total_cost_usd"), "models": {}}
    for m in models:
        mine = [r for r in rows if r["model"] == m]
        by_type, by_level = defaultdict(list), defaultdict(list)
        for r in mine:
            by_type[r["question_type"]].append(r)
            by_level[str(r["level"])].append(r)
        wrong = [r for r in mine if r["correct"] == "0"]
        counts = defaultdict(int)
        for r in wrong:
            counts[r["fingerprint"].split(":")[0] if r["fingerprint"].startswith("ambiguous") else
                   r["fingerprint"]] += 1
        matched = [r for r in wrong if r["fingerprint"] not in ("unmatched", "no_answer", "")]
        expected = [chance[r["task_id"]] for r in wrong if chance.get(r["task_id"]) is not None]
        # armed versus disarmed pairs: same seed and level, variants "<trap>_armed" / "<trap>_disarmed"
        pairs = defaultdict(lambda: {"armed": [], "disarmed": []})
        for r in mine:
            v = str(r.get("variant") or "")
            if v.endswith("_armed") or v.endswith("_disarmed"):
                trap, state = v.rsplit("_", 1)
                pairs[trap][state].append(r)
        lat = [float(r["latency_s"]) for r in mine if r["latency_s"] not in ("", None)]
        out["models"][m] = {
            "overall": _acc(mine),
            "by_question_type": {k: _acc(v) for k, v in sorted(by_type.items())},
            "by_level": {k: _acc(v) for k, v in sorted(by_level.items())},
            "wrong": len(wrong),
            "fingerprints": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "fingerprint_match_rate": round(len(matched) / len(wrong), 4) if wrong else None,
            "fingerprint_match_rate_by_chance": round(sum(expected) / len(expected), 4) if expected else None,
            "armed_vs_disarmed": {t: {"armed": _acc(p["armed"]), "disarmed": _acc(p["disarmed"])}
                                  for t, p in sorted(pairs.items())},
            "mean_latency_s": round(sum(lat) / len(lat), 1) if lat else None,
            "cost_usd": round(sum(float(r["cost_usd"] or 0) for r in mine), 4),
        }
    return out


def _pct(a: dict) -> str:
    return "n/a" if a["accuracy"] is None else f"{a['correct']}/{a['n']} ({a['accuracy']:.0%})"


def markdown(s: dict) -> str:
    lines = ["# Evaluation summary", ""]
    if s["placeholder"]:
        lines += ["**PLACEHOLDER: produced with offline mock backends. These are not model results.**", ""]
    if not s["complete"]:
        lines += ["**RUN INCOMPLETE: at least one reply did not finish with finish_reason `stop`.**", ""]
    lines += [f"Tasks: {s['n_tasks']}. Total cost: ${s['total_cost_usd']}.", "",
              "| Model | Correct | Wrong | Wrong answers matching a fingerprint | Expected by chance |",
              "|---|---|---|---|---|"]
    for m, d in s["models"].items():
        rate = ("n/a" if d["fingerprint_match_rate"] is None else
                f"{round(d['fingerprint_match_rate'] * d['wrong'])} of {d['wrong']} "
                f"({d['fingerprint_match_rate']:.0%})")
        base = ("n/a" if d["fingerprint_match_rate_by_chance"] is None
                else f"{d['fingerprint_match_rate_by_chance']:.0%}")
        lines.append(f"| `{m}` | {_pct(d['overall'])} | {d['wrong']} | {rate} | {base} |")
    for m, d in s["models"].items():
        lines += ["", f"## `{m}`", "", "| Question type | Correct |", "|---|---|"]
        lines += [f"| {k} | {_pct(v)} |" for k, v in d["by_question_type"].items()]
        lines += ["", "| Level | Correct |", "|---|---|"]
        lines += [f"| {k} | {_pct(v)} |" for k, v in d["by_level"].items()]
        if d["fingerprints"]:
            lines += ["", "| Wrong answer consistent with | Count |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in d["fingerprints"].items()]
        if d["armed_vs_disarmed"]:
            lines += ["", "| Trap | Correct when armed | Correct when disarmed |", "|---|---|---|"]
            lines += [f"| {t} | {_pct(p['armed'])} | {_pct(p['disarmed'])} |"
                      for t, p in d["armed_vs_disarmed"].items()]
    lines += ["", "A fingerprint match means the wrong answer equals what that shortcut produces on the task. "
                  "It is consistent with the shortcut, not proof that the model used it.", ""]
    return "\n".join(lines)
