"""TEMPLATE for a Mermaid-diagram generator whose question is the SAME every time.

Copy it with:

    python3 -m generate.new_generator my_flowchart --mermaid

It is a complete, working example: it invents a random flowchart, stores the Mermaid source, draws it with the
Mermaid CLI, asks "how many arrows on the shortest route from Start to End?" and computes the answer by code.
Make it your own: change how the graph is built (more nodes, more branches, subgraphs, labels), keep the idea.

THE IDEA (read this once):
    * The DATA is the source of truth: the nodes and edges, and the Mermaid text built from them.
    * The picture is drawn from the Mermaid text. The answer is computed from the nodes/edges, never from
      the picture. verify_render() proves the Mermaid text really contains the data's edges.
    * The Mermaid source lives in data["mermaid"] (that is `source_key`). The runner also saves a copy at
      tasks/source/<task_id>.mmd so a human can open it.
    * The question never changes (`fixed_question = True`), so what varies between tasks is the diagram and
      the answer. Keep the ANSWERS varied (the variation report warns if half the tasks share one answer).

Needs the Mermaid CLI on the machine that generates:  npm install -g @mermaid-js/mermaid-cli
"""
from __future__ import annotations

import random
import re
from collections import deque
from pathlib import Path

from generate.base import CheckResult, TaskGenerator
from generate.mermaid import render_mermaid

_EDGE = re.compile(r"^\s*(\w+)\s*-->\s*(\w+)\s*$")


def build_mermaid(nodes: list[str], edges: list[list[str]]) -> str:
    """The Mermaid text for these nodes and edges (first node is Start, last is End)."""
    lines = ["flowchart TD", f"    {nodes[0]}([Start])", f"    {nodes[-1]}([End])"]
    lines += [f"    {a} --> {b}" for a, b in edges]
    return "\n".join(lines)


def edges_from_mermaid(source: str) -> list[list[str]]:
    """Read the `A --> B` lines back out of Mermaid text."""
    return [[m.group(1), m.group(2)] for line in source.splitlines() if (m := _EDGE.match(line))]


def shortest_path_length(nodes: list[str], edges: list[list[str]]) -> int:
    """Number of arrows on the shortest route from the first node to the last (following arrow directions)."""
    nxt: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        nxt[a].append(b)
    dist = {nodes[0]: 0}
    queue = deque([nodes[0]])
    while queue:
        cur = queue.popleft()
        for n in nxt[cur]:
            if n not in dist:
                dist[n] = dist[cur] + 1
                queue.append(n)
    return dist[nodes[-1]]


class MyGenerator(TaskGenerator):
    # ---- identity -------------------------------------------------------------------------
    name = "my_generator"  # set for you by `new_generator`
    visual_type = "flowchart (Mermaid)"
    question_type = "shortest path length"
    answer_type = "number"
    tolerance = None
    exact_answer = True  # a whole-number count, not a visual estimate: no tolerance needed
    real_world_examples = [
        # TODO: replace with 1-2 REAL examples your team is modeling this on (a name + link or description)
        "Process / approval flowcharts in engineering runbooks and wikis drawn with Mermaid (see the flowchart examples at mermaid.js.org)",
    ]
    enabled = False  # TODO: set to True once the Mermaid CLI is installed and you have looked at one image

    # ---- same question every time; the Mermaid text is stored with each task ---------------
    question_template = (
        "The flowchart shows a process that begins at the box labeled Start and ends at the box labeled End. "
        "Following the arrows only in the direction they point, how many arrows must be followed along the "
        "shortest route from Start to End? Reply with a whole number."
    )
    fixed_question = True
    source_key = "mermaid"
    source_ext = "mmd"

    # ---- REQUIRED -------------------------------------------------------------------------
    def generate_data(self, rng: random.Random, index: int) -> dict:
        """Step 1: a random flowchart in which End can always be reached from Start."""
        count = rng.randint(9, 14)  # TODO: make it bigger / more tangled for a harder task
        nodes = [f"N{i}" for i in range(count)]
        edges = []
        for i in range(1, count):  # every node gets an arrow from an earlier node: End is always reachable
            for parent in sorted(rng.sample(range(i), k=min(i, rng.randint(1, 2)))):
                edges.append([nodes[parent], nodes[i]])
        return {"nodes": nodes, "edges": edges, "mermaid": build_mermaid(nodes, edges)}

    def render(self, data: dict, out_path: Path) -> None:
        """Step 2: draw the Mermaid text to a PNG with the Mermaid CLI."""
        render_mermaid(data["mermaid"], out_path)

    def compute_answer(self, data: dict):
        """Step 3: computed from the nodes and edges, never from the picture."""
        return shortest_path_length(data["nodes"], data["edges"])

    # make_question() is not needed: question_template is used.

    # ---- OPTIONAL but recommended ----------------------------------------------------------
    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        """Step 4: the Mermaid text must contain exactly the data's edges, and the image must be a PNG.

        This proves the TEXT matches the data. It does not look at the drawn pixels."""
        in_text = {tuple(e) for e in edges_from_mermaid(data["mermaid"])}
        in_data = {tuple(e) for e in data["edges"]}
        if in_text != in_data:
            return CheckResult.fail("the Mermaid source does not contain the same edges as the data",
                                    expected=sorted(in_data), actual=sorted(in_text))
        head = Path(image_path).read_bytes()[:8] if Path(image_path).exists() else b""
        if head != b"\x89PNG\r\n\x1a\n":
            return CheckResult.fail("the image file is missing or is not a PNG", where=str(image_path))
        return CheckResult.ok(f"Mermaid source has all {len(in_data)} edges from the data; image is a PNG "
                              "(pixels not inspected)")
