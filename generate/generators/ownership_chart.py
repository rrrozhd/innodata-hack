"""Ownership-structure charts: who ultimately owns the target company?  (a Mermaid diagram, one fixed question)

A chart is a made-up web of people and companies. Arrows run from an owner DOWN to the company it owns, each
labeled with the percent it holds. The one company that owns nothing (at the bottom) is the target. A person's
effective stake in the target is found by multiplying the percents along each route down to it and adding the
routes up. The question is always the same: how many people hold 25% or more of the target?

HOW A CHART IS MADE (numbers in brackets are the rules from the brief):
  * `level` (1-10) is the difficulty dial: entities grow 8 -> 22 [16], extra cross-holdings 5% -> 25% [16], and
    the nearest owner on each side of the 25% line must be within a gap of 40 points down to 5 [15].
  * A chart depends ONLY on (level, seed) [20]. `build_chart(level, seed)` returns the chart or None when one of
    the structure / number / answer rules fails; then the next seed is tried.
  * A chart whose picture does not fit in 2,000 px (labels are always 16 px, never scaled) is also redrawn from
    the next seed [19]. This needs the Mermaid CLI (`mmdc`) at generation time, see generate/mermaid.py.
  * The Mermaid source stores the level and seed as `%%` comments [20] and is saved in data["mermaid"] and in
    tasks/source/<id>.mmd.
  * The answer is the COUNT of people with an effective stake of 25% or more. It is computed from the edges
    (never from the picture).
"""
from __future__ import annotations

import random
import re
import tempfile
from fractions import Fraction
from pathlib import Path

from generate.base import CheckResult, TaskGenerator
from generate.mermaid import png_size, render_mermaid

LINE = Fraction(25)  # the beneficial-owner line, in percent
MIN_DISTANCE = 2  # nobody may be within this many points of the line [13]
MAX_OWNERS = 4  # one to four owners per company [7]
PICTURE_LIMIT = 2000  # px, both width and height [19]
LABEL_PX = 16  # label size, always [19]
LEVELS = 10
MAX_SEED_TRIES = 5000

SYLLABLES = ["ba", "bel", "dor", "fen", "gal", "hin", "jor", "kal", "lum", "mar", "nel", "or", "pel", "quin",
             "ras", "sel", "tov", "um", "var", "wen", "xan", "yor", "zel", "dri", "fro", "gri", "kro", "mi",
             "no", "ru", "si", "ta", "vu", "lo"]
COMPANY_ENDINGS = ["Holdings", "Capital", "Group", "Partners", "Ventures", "Trust", "Industries", "Enterprises",
                   "Investments", "Associates"]  # ten endings [17]


# ---------------------------------------------------------------------------------------------------------
# level dial
# ---------------------------------------------------------------------------------------------------------
def level_params(level: int) -> dict:
    """Entities (8 -> 22), cross-holding chance (0.05 -> 0.25) and gap in points (40 -> 5) for a level 1-10."""
    if not 1 <= level <= LEVELS:
        raise ValueError(f"level must be 1-{LEVELS}, got {level}")
    t = Fraction(level - 1, LEVELS - 1)
    return {"entities": round(8 + t * 14), "cross": float(Fraction(5, 100) + t * Fraction(20, 100)),
            "gap": Fraction(40) - t * 35}


# ---------------------------------------------------------------------------------------------------------
# pure maths on the edges (used by compute_answer and by the checks)
# ---------------------------------------------------------------------------------------------------------
def _structure(data: dict):
    kind = {e["id"]: e["kind"] for e in data["entities"]}
    pct: dict[tuple[str, str], int] = {(e["owner"], e["company"]): e["percent"] for e in data["edges"]}
    owns: dict[str, list[str]] = {i: [] for i in kind}
    for (o, c) in pct:
        owns[o].append(c)
    return kind, pct, owns


def _topological_company_order(kind: dict, owns: dict) -> list[str]:
    """Companies, owned-before-owner (the target first). Raises ValueError on a loop."""
    state: dict[str, int] = {}
    order: list[str] = []

    def visit(n: str) -> None:
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            raise ValueError("the ownership arrows contain a loop")
        state[n] = 1
        for c in owns[n]:
            visit(c)
        state[n] = 2
        if kind[n] == "company":
            order.append(n)

    for n in kind:
        visit(n)
    return order


def effective_stakes(data: dict) -> dict[str, Fraction]:
    """Each person's effective stake in the target, in percent (exact fractions). Sums routes of products."""
    kind, pct, owns = _structure(data)
    target = data["target"]
    order = _topological_company_order(kind, owns)  # owned-before-owner
    share = {i: Fraction(0) for i in kind}
    share[target] = Fraction(1)
    for c in order:  # push each company's share up to its owners
        for (o, cc), p in pct.items():
            if cc == c:
                share[o] += Fraction(p, 100) * share[c]
    return {i: share[i] * 100 for i in kind if kind[i] == "person"}


def beneficial_owner_count(data: dict) -> int:
    """The answer: how many people hold 25% or more of the target."""
    return sum(1 for v in effective_stakes(data).values() if v >= LINE)


def shortcut_sets(data: dict) -> tuple[set[str], set[str]]:
    """The two wrong shortcuts: (people whose BIGGEST SINGLE ROUTE reaches 25%, people with ANY ARROW of 25%+)."""
    kind, pct, owns = _structure(data)
    target = data["target"]
    best = {i: Fraction(0) for i in kind}
    best[target] = Fraction(1)
    for c in _topological_company_order(kind, owns):
        for (o, cc), p in pct.items():
            if cc == c:
                best[o] = max(best[o], Fraction(p, 100) * best[c])
    people = [i for i in kind if kind[i] == "person"]
    route = {i for i in people if best[i] * 100 >= LINE}
    arrow = {i for i in people if any(p >= LINE for (o, _), p in pct.items() if o == i)}
    return route, arrow


# ---------------------------------------------------------------------------------------------------------
# building one chart from (level, seed)
# ---------------------------------------------------------------------------------------------------------
def _word(rng: random.Random, syllables: int) -> str:
    return "".join(rng.choice(SYLLABLES) for _ in range(syllables)).capitalize()


def _names(rng: random.Random, n_people: int, n_companies: int) -> tuple[list[str], list[str]]:
    used: set[str] = set()

    def unique(make) -> str:
        for _ in range(1000):
            name = make()
            if name.lower() not in used:
                used.add(name.lower())
                return name
        raise RuntimeError("could not make a unique name")

    people = [unique(lambda: f"{_word(rng, 2)} {_word(rng, rng.choice((2, 3)))}") for _ in range(n_people)]
    companies = [unique(lambda: f"{_word(rng, rng.choice((2, 3)))} {rng.choice(COMPANY_ENDINGS)}")
                 for _ in range(n_companies)]
    return people, companies


def build_mermaid(level: int, seed: int, entities: list[dict], edges: list[dict]) -> str:
    """The Mermaid text. People are pills, companies are rectangles, every arrow shows its percent."""
    lines = [
        '%%{init: {"theme": "base", "themeVariables": {"fontSize": "' + str(LABEL_PX) + 'px"}, '
        '"flowchart": {"useMaxWidth": false, "nodeSpacing": 40, "rankSpacing": 60}}}%%',
        f"%% level: {level}",
        f"%% seed: {seed}",
        "flowchart TB",
    ]
    for e in entities:
        lines.append(f'    {e["id"]}(["{e["name"]}"])' if e["kind"] == "person" else f'    {e["id"]}["{e["name"]}"]')
    for e in edges:
        lines.append(f'    {e["owner"]} -->|{e["percent"]}%| {e["company"]}')
    return "\n".join(lines)


def build_chart(level: int, seed: int) -> dict | None:
    """The chart for (level, seed), or None if it breaks a structure / number / answer rule. Deterministic."""
    params = level_params(level)
    rng = random.Random(f"ownership-chart|{level}|{seed}")
    n = params["entities"]
    n_people = round(0.4 * n)  # people are 40% of the entities [5]
    n_companies = n - n_people
    people = [f"p{i + 1}" for i in range(n_people)]
    companies = [f"c{i + 1}" for i in range(n_companies)]  # in order: owners sit above, the target is last
    target = companies[-1]  # the only company that owns nothing [1]
    owners: dict[str, list[str]] = {c: [] for c in companies}

    # Structure [1-8]: every owner picks a company BELOW it; with the level's chance it also picks one more
    # (a cross-holding). Picks only go downward, so there can be no loop [2], and every entity but the target
    # owns something, so every entity has a route to the target [3].
    for o in people + companies[:-1]:
        below = companies if o in people else companies[companies.index(o) + 1:]
        for _ in range(1 + (1 if rng.random() < params["cross"] else 0)):
            free = [c for c in below if len(owners[c]) < MAX_OWNERS and o not in owners[c]]  # [7][8]
            if free:
                owners[rng.choice(free)].append(o)
        if not any(o in v for v in owners.values()):
            return None
    for c in companies:  # a company nobody picked gets one person as its 100% owner [7]
        if not owners[c]:
            owners[c].append(rng.choice(people))

    # Numbers [9-10]: whole percents in steps of 5 (5..100) that add to exactly 100 for each company
    edges = []
    for c in companies:
        k = len(owners[c])
        if k == 1:
            parts = [100]
        else:
            cuts = sorted(rng.sample(range(1, 20), k - 1))
            parts = [5 * (b - a) for a, b in zip([0] + cuts, cuts + [20])]
        edges += [{"owner": o, "company": c, "percent": p} for o, p in zip(owners[c], parts)]

    person_names, company_names = _names(rng, n_people, n_companies)  # [17]
    entities = ([{"id": i, "kind": "person", "name": nm} for i, nm in zip(people, person_names)]
                + [{"id": i, "kind": "company", "name": nm} for i, nm in zip(companies, company_names)])
    data = {"level": level, "seed": seed, "target": target, "entities": entities, "edges": edges}

    stakes = effective_stakes(data)  # [11]: always 100 exactly, because every company's stakes add to 100
    if sum(stakes.values()) != 100:
        return None
    above = [v for v in stakes.values() if v >= LINE]
    below = [v for v in stakes.values() if v < LINE]
    if not above or not below:  # [12]
        return None
    if any(abs(v - LINE) <= MIN_DISTANCE for v in stakes.values()):  # [13]
        return None
    answer = len(above)
    route, arrow = shortcut_sets(data)
    true_set = {i for i, v in stakes.items() if v >= LINE}
    for wrong in (route, arrow):  # [14] both shortcuts must fail, as a list of people and as a count
        if wrong == true_set or len(wrong) == answer:
            return None
    if min(v - LINE for v in above) > params["gap"] or min(LINE - v for v in below) > params["gap"]:  # [15]
        return None
    data["mermaid"] = build_mermaid(level, seed, entities, edges)
    return data


def picture_fits(source: str) -> bool:
    """Draw `source` with the Mermaid CLI and check the picture fits in 2,000 px each way [19]."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "check.png"
        render_mermaid(source, out, scale=1)
        width, height = png_size(out)
    return width <= PICTURE_LIMIT and height <= PICTURE_LIMIT


def make_chart(level: int, base_seed: int, fits=picture_fits) -> dict:
    """First valid chart from base_seed, base_seed+1, ...: rules pass AND the picture fits [19]."""
    for seed in range(base_seed, base_seed + MAX_SEED_TRIES):
        chart = build_chart(level, seed)
        if chart is not None and fits(chart["mermaid"]):
            return chart
    raise RuntimeError(f"no valid level-{level} chart in {MAX_SEED_TRIES} seeds from {base_seed}")


# ---------------------------------------------------------------------------------------------------------
# edges read back from the Mermaid text (used by verify_render)
# ---------------------------------------------------------------------------------------------------------
_EDGE = re.compile(r"^\s*(\w+)\s*-->\|(\d+)%\|\s*(\w+)\s*$")
_PERSON = re.compile(r'^\s*(\w+)\(\["(.+)"\]\)\s*$')
_COMPANY = re.compile(r'^\s*(\w+)\["(.+)"\]\s*$')


def read_back(source: str) -> dict:
    """Level, seed, nodes (id -> (kind, name)) and edges read from Mermaid text written by build_mermaid."""
    level = re.search(r"^%% level: (\d+)$", source, re.M)
    seed = re.search(r"^%% seed: (\d+)$", source, re.M)
    nodes, edges = {}, set()
    for line in source.splitlines():
        if m := _PERSON.match(line):
            nodes[m.group(1)] = ("person", m.group(2))
        elif m := _COMPANY.match(line):
            nodes[m.group(1)] = ("company", m.group(2))
        elif m := _EDGE.match(line):
            edges.add((m.group(1), m.group(3), int(m.group(2))))
    return {"level": int(level.group(1)) if level else None, "seed": int(seed.group(1)) if seed else None,
            "nodes": nodes, "edges": edges, "font": re.search(r'"fontSize": "(\d+)px"', source)}


class OwnershipChartGenerator(TaskGenerator):
    name = "ownership_chart"
    visual_type = "ownership structure chart (Mermaid)"
    question_type = "count beneficial owners"
    answer_type = "number"
    tolerance = None
    exact_answer = True  # a whole-number count
    real_world_examples = [
        "Ownership structure charts used in beneficial-ownership due diligence: the US FinCEN customer due "
        "diligence rule asks who owns 25% or more of a company, directly or through other companies",
        "Corporate group structure charts in company filings and KYC files, where a person's holding through a "
        "chain of companies is the product of the percentages along the chain",
    ]
    enabled = False  # switched off (not deleted) when the team moved to the vital-signs charts; set True to use it

    question_template = (
        "The chart shows who owns what. An arrow from an owner to a company is labeled with the percent of that "
        "company the owner holds. The company at the bottom, which owns nothing, is the target company. A person's "
        "stake in the target is found by multiplying the percentages along each route from that person down to the "
        "target and then adding up all of that person's routes. How many people hold 25% or more of the target "
        "company? Reply with a whole number."
    )
    fixed_question = True
    source_key = "mermaid"
    source_ext = "mmd"

    def picture_fits(self, source: str) -> bool:  # a method so tests can stand in for the Mermaid CLI
        return picture_fits(source)

    def generate_data(self, rng: random.Random, index: int) -> dict:
        level = index % LEVELS + 1  # tasks 0..9 are levels 1..10; more tasks cycle through the levels again
        return make_chart(level, rng.randrange(1, 2**31), fits=self.picture_fits)

    def render(self, data: dict, out_path: Path) -> None:
        render_mermaid(data["mermaid"], out_path, scale=1)

    def compute_answer(self, data: dict):
        return beneficial_owner_count(data)

    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        """The Mermaid text matches the data (nodes, shapes, names, percents, level, seed, 16 px), and the picture
        is a PNG that fits in 2,000 px. This checks the text and the file size, not the drawn pixels."""
        got = read_back(data["mermaid"])
        want_nodes = {e["id"]: (e["kind"], e["name"]) for e in data["entities"]}
        want_edges = {(e["owner"], e["company"], e["percent"]) for e in data["edges"]}
        problems = []
        if got["nodes"] != want_nodes:
            problems.append("the nodes, names or shapes in the Mermaid text differ from the data")
        if got["edges"] != want_edges:
            problems.append("the arrows or percentages in the Mermaid text differ from the data")
        if (got["level"], got["seed"]) != (data["level"], data["seed"]):
            problems.append("the level/seed comments in the Mermaid text differ from the data")
        first_line = data["mermaid"].splitlines()[0] if data["mermaid"] else ""
        if not (first_line.startswith("%%{init:") and first_line.endswith("}%%")):
            problems.append("the first line must be an init directive wrapped in %%{ ... }%%")
        if not got["font"] or int(got["font"].group(1)) != LABEL_PX:
            problems.append(f"labels must be {LABEL_PX}px")
        if problems:
            return CheckResult.fail("; ".join(problems), where="data['mermaid']")
        try:
            width, height = png_size(image_path)
        except (ValueError, OSError) as exc:
            return CheckResult.fail(f"the image is missing or not a PNG ({exc})", where=str(image_path))
        if width > PICTURE_LIMIT or height > PICTURE_LIMIT:
            return CheckResult.fail(f"the picture is {width}x{height}px; it must fit in {PICTURE_LIMIT}px",
                                    expected=f"<= {PICTURE_LIMIT}", actual=f"{width}x{height}", where=str(image_path))
        return CheckResult.ok(f"Mermaid text matches the data ({len(want_edges)} arrows, level {data['level']}, "
                              f"seed {data['seed']}); picture {width}x{height}px (pixels not inspected)")
