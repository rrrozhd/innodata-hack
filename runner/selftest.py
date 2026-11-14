"""`python run_tests.py --selftest`: proves the runner works end to end WITHOUT keys or network.

What it exercises:
  A. small unit checks (answer extraction, scoring, stage parsing, registry rules, leak check)
  B. the real OpenRouter client against a tiny local fake server (retry on 429, truncation,
     hard errors, request body shape)
  C. the whole pipeline in a temp folder with two built-in demo generators (stdlib-only PNG
     bar charts) and a mock model: generate -> validate -> variation -> evaluate -> score ->
     scorecard, including a deliberately truncated reply that must be flagged and re-run
  D. "deliberately broken" tests: a tampered answer and a tampered image must be CAUGHT
  E. packaging: the secret scan blocks a planted key and the zip excludes .env
  F. repo-specific: placeholder handling, run from another folder, original entry points, key never leaked

The demo generators below are also a worked example of the generator contract.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import random
import shutil
import struct
import tempfile
import threading
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from validate import hand_check as handcheck
from runner import package
from runner.config import DEFAULTS, Config, ModelSpec, deep_merge, from_run_config, is_placeholder
from runner.context import RunContext
from runner.schema import Task, from_official, load_tasks, save_tasks, task_fingerprint, to_official
from runner.output import UI
from eval.client import Completion, MockClient, OpenRouterClient
from eval.extract_answer import extract_final_answer
from eval.score import is_correct
from generate.base import CheckResult, TaskGenerator
from generate.registry import discover
from validate.check_needs_image import leak_ratio
from validate.run_all import validate_tasks

# ----------------------------------------------------------------------------------------------
# Demo generators: a worked example of the contract (stdlib only: writes PNG bytes by hand).
# ----------------------------------------------------------------------------------------------
W, H, BAR_W, STEP, X0, SCALE = 300, 200, 40, 52, 30, 2
BG, GRID = (255, 255, 255), (220, 220, 220)
COLORS = [(31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40), (148, 103, 189)]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_bar_png(values: list[int], path: Path) -> None:
    rows = [[BG] * W for _ in range(H)]
    for g in range(1, 10):  # a gridline every 10 units
        y = H - 1 - g * 10 * SCALE
        for x in range(W):
            rows[y][x] = GRID
    for i, v in enumerate(values):
        top = H - v * SCALE
        for y in range(top, H):
            for x in range(X0 + i * STEP, X0 + i * STEP + BAR_W):
                rows[y][x] = COLORS[i % len(COLORS)]
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)
    png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def read_bar_heights(path: Path, n: int) -> list[float]:
    """Measure bar heights back out of the pixels (this is what verify_render uses)."""
    blob = path.read_bytes()
    pos, idat = 8, b""
    while pos < len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        kind = blob[pos + 4:pos + 8]
        if kind == b"IDAT":
            idat += blob[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = 1 + W * 3
    heights = []
    for i in range(n):
        cx = X0 + i * STEP + BAR_W // 2
        top = H
        for y in range(H):
            off = y * stride + 1 + cx * 3
            px = tuple(raw[off:off + 3])
            if px not in (BG, GRID):
                top = y
                break
        heights.append((H - top) / SCALE)
    return heights


def _verify_bars(data: dict, image_path: Path) -> CheckResult:
    measured = read_bar_heights(image_path, len(data["values"]))
    if [round(m) for m in measured] == data["values"]:
        return CheckResult.ok(f"bar heights measured from the pixels match the data {data['values']}")
    return CheckResult.fail("bar heights read from the image do not match the data",
                            expected=data["values"], actual=[round(m) for m in measured],
                            where="generator.render() vs data file")


class DemoBarLetter(TaskGenerator):
    name = "selftest_bar_letter"
    visual_type = "bar chart (labeled A-E)"
    question_type = "find the tallest bar"
    answer_type = "choice"
    real_world_examples = ["SELFTEST ONLY: stand-in for a real source, e.g. a transit ridership bar chart"]

    def generate_data(self, rng: random.Random, index: int) -> dict:
        return {"values": rng.sample(range(10, 90), 5), "labels": list("ABCDE")}

    def render(self, data: dict, out_path: Path) -> None:
        write_bar_png(data["values"], out_path)

    def compute_answer(self, data: dict):
        return data["labels"][max(range(5), key=lambda i: data["values"][i])]

    def make_question(self, data: dict) -> str:
        return ("The bars in the chart are labeled A, B, C, D, E from left to right. "
                "Which bar is tallest? Answer with the letter.")

    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        return _verify_bars(data, image_path)


class DemoBarValue(TaskGenerator):
    name = "selftest_bar_value"
    visual_type = "bar chart (read a value)"
    question_type = "read one bar height"
    answer_type = "number"
    tolerance = 5
    real_world_examples = ["SELFTEST ONLY: stand-in for a real source, e.g. a utility-usage bar chart"]

    def generate_data(self, rng: random.Random, index: int) -> dict:
        return {"values": rng.sample(range(15, 90), 5), "target": rng.randrange(5)}

    def render(self, data: dict, out_path: Path) -> None:
        write_bar_png(data["values"], out_path)

    def compute_answer(self, data: dict):
        return data["values"][data["target"]]

    def make_question(self, data: dict) -> str:
        return (f"Gridlines are drawn every 10 units, with 0 at the bottom of the chart. The bars are "
                f"numbered 1 to 5 from left to right. What is the height of bar {data['target'] + 1}, in units?")

    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        return _verify_bars(data, image_path)


class _BadGeneratorNoExamples(TaskGenerator):
    name = "bad_no_examples"
    visual_type = "x"
    question_type = "y"

    def generate_data(self, rng, index): return {}
    def render(self, data, out_path): pass
    def compute_answer(self, data): return 1
    def make_question(self, data): return "?"


class _BadGeneratorUnfinished(TaskGenerator):
    name = "bad_unfinished"
    visual_type = "x"
    question_type = "y"
    real_world_examples = ["example"]


# ----------------------------------------------------------------------------------------------
# Test harness
# ----------------------------------------------------------------------------------------------
class _Checker:
    def __init__(self, ui: UI):
        self.ui = ui
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            self.ui.pass_(name)
        else:
            self.failed.append(name)
            self.ui.fail(name)
            if detail:
                self.ui.detail(detail)
        return condition


def _section(ui: UI, title: str) -> None:
    ui.emit()
    ui.emit(ui.c("#" * 78, "magenta"))
    ui.emit(ui.c(f" SELFTEST  {title}", "magenta", "bold"))
    ui.emit(ui.c("#" * 78, "magenta"))


def _reset_totals(ui: UI) -> None:
    ui.totals = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}


def run_selftest(ui: UI, strict: bool = False) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vtf_selftest_"))
    t = _Checker(ui)
    try:
        _unit_checks(ui, t)
        _http_checks(ui, t)
        _pipeline_checks(ui, t, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ui.emit()
    ui.emit(ui.c("=" * 78, "cyan"))
    if t.failed:
        ui.emit(ui.c(f" SELFTEST FAILED: {len(t.failed)} check(s) failed, {t.passed} passed", "red", "bold"))
        for name in t.failed:
            ui.emit(ui.c(f"   - {name}", "red"))
    else:
        ui.emit(ui.c(f" SELFTEST PASSED: all {t.passed} checks passed", "green", "bold"))
        ui.emit(ui.c(" (this proves the shell logic; live model calls still need your keys and model ids)", "gray"))
    ui.emit(ui.c("=" * 78, "cyan"))
    return 1 if t.failed else 0


# ---- A. unit checks -------------------------------------------------------------------------
def _unit_checks(ui: UI, t: _Checker) -> None:
    _section(ui, "A. unit checks")
    t.check("extract: takes the last FINAL ANSWER line, strips markdown",
            extract_final_answer("hmm\nFINAL ANSWER: 3\nwait\nFINAL ANSWER: **42**.") == "42")
    t.check("extract: returns None when the line is missing", extract_final_answer("it is 42") is None)
    t.check("score number: within tolerance", is_correct("number", 10, "$11", 2)[0])
    t.check("score number: outside tolerance", not is_correct("number", 10, "13", 2)[0])
    t.check("score number: thousands separators", is_correct("number", 1234, "1,234", None)[0])
    t.check("score number: unicode minus", is_correct("number", -5, "−5", None)[0])
    t.check("score number: exact match required when no tolerance", not is_correct("number", 10, "10.5", None)[0])
    t.check("score choice: '(b)' equals 'B'", is_correct("choice", "B", "(b)", None)[0])
    t.check("score text: punctuation/case ignored", is_correct("text", "The Red Line", "the red line!", None)[0])
    t.check("score: missing FINAL ANSWER is wrong, with a reason", is_correct("number", 1, None, None) == (False, "no 'FINAL ANSWER:' line found in the reply"))
    import argparse
    import run_tests as rt  # the runner entry point (a plain module in the repo root)
    t.check("stages: range 3-5", rt.parse_stage_range("3-5") == [3, 4, 5])
    t.check("stages: comma list with a range", rt.parse_stage_range("0,2-4") == [0, 2, 3, 4])
    t.check("stages: 'all' is 0-7", rt.parse_stage_range("all") == list(range(8)))
    for bad in ("bogus", "9", "5-2"):
        try:
            rt.parse_stage_range(bad)
            t.check(f"stages: {bad!r} is rejected", False)
        except argparse.ArgumentTypeError:
            t.check(f"stages: {bad!r} is rejected", True)
    t.check("placeholder: 'PLACEHOLDER: high' is a placeholder, 'high' is not",
            is_placeholder("PLACEHOLDER: high") and not is_placeholder("high") and not is_placeholder(16000))
    cfg_ph, notes = from_run_config({"models": [{"name": "A", "openrouter_slug": "PLACEHOLDER"}],
                                     "reasoning_effort": "PLACEHOLDER: high", "max_tokens": "PLACEHOLDER: x",
                                     "temperature": 0, "_placeholder": True, "run_date": "PLACEHOLDER"})
    t.check("config: placeholder effort/max_tokens fall back to defaults with a note, never passed through",
            "reasoning_effort" not in cfg_ph["eval"] and "max_tokens" not in cfg_ph["eval"] and len(notes) == 2
            and cfg_ph["eval"]["temperature"] == 0 and "run_date" not in cfg_ph)
    t.check("config: a model with a PLACEHOLDER slug is flagged", ModelSpec("A", "PLACEHOLDER").is_placeholder)
    official = {"task_id": "x1", "image_path": "tasks/images/x1.png", "question": "q?", "data": "tasks/data/x1.json",
                "reference_answer": 3, "checks": {"render": "r"}, "generator": "g", "index": 0, "seed": 1,
                "visual_type": "v", "question_type": "q", "answer_type": "number", "tolerance": None,
                "real_world_examples": ["e"]}
    internal = from_official(official)
    task = Task(**{k: v for k, v in internal.items() if k in Task.__dataclass_fields__},
                extra={k: v for k, v in internal.items() if k not in Task.__dataclass_fields__})
    back = to_official(task)
    t.check("schema: repo field names round-trip (task_id, data, reference_answer, checks)",
            back["task_id"] == "x1" and back["data"] == "tasks/data/x1.json" and back["reference_answer"] == 3
            and back["checks"] == {"render": "r"} and "id" not in back and "data_path" not in back)
    tmpl = Path(tempfile.mkdtemp(prefix="vtf_schema_"))
    try:
        lines = [json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}), "not json at all",
                 json.dumps({"task_id": "y", "question": "q"}), json.dumps(official)]
        (tmpl / "t.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        loaded, problems = load_tasks(tmpl / "t.jsonl")
        t.check("load: placeholder skipped; malformed line reported WITH its line number; bad record names missing fields",
                len(loaded) == 1 and loaded[0].id == "x1" and any(p.startswith("line 2") for p in problems)
                and any("line 3" in p and "missing field(s)" in p and "reference_answer" in p for p in problems),
                str(problems))
    finally:
        shutil.rmtree(tmpl, ignore_errors=True)
    ratio, _ = leak_ratio({"a": 120, "b": 340, "c": 560}, "The values are 120, 340 and 560. Which is largest?")
    t.check("leak check: question that lists the data scores 100%", ratio == 1.0, f"got {ratio}")
    ratio, _ = leak_ratio({"a": 120, "b": 340, "c": 560}, "Which value is largest?")
    t.check("leak check: clean question scores 0%", ratio == 0.0, f"got {ratio}")
    reg = discover(extra=[_BadGeneratorNoExamples, _BadGeneratorUnfinished, DemoBarLetter], include_user=False)
    t.check("registry: good generator accepted", "selftest_bar_letter" in reg.generators)
    t.check("registry: generator without cited examples rejected",
            any("real_world_examples" in p for p in reg.problems))
    t.check("registry: unfinished generator rejected with a clear reason",
            any("not implemented yet" in p for p in reg.problems))


# ---- B. real HTTP client against a local fake server ---------------------------------------
class _FakeServer(BaseHTTPRequestHandler):
    script: list = []
    seen: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _FakeServer.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        code, payload, headers = _FakeServer.script.pop(0)
        raw = json.dumps(payload).encode()
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence the default request log
        pass


def _ok_payload(text="reasoning...\nFINAL ANSWER: B", finish="stop", cost=0.0123):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 321, "completion_tokens": 654, "cost": cost,
                      "completion_tokens_details": {"reasoning_tokens": 500}}}


def _http_checks(ui: UI, t: _Checker) -> None:
    _section(ui, "B. OpenRouter client vs a local fake server (no internet)")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = OpenRouterClient(f"http://127.0.0.1:{server.server_port}/api/v1", "test-key-not-real",
                                  timeout=10, max_retries=2, backoff=0, sleep=lambda s: None)
        model = ModelSpec("m", "vendor/model-x", {"provider": {"order": ["x"]}})
        params = {"temperature": 0, "reasoning_effort": "high", "max_tokens": 1234}
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "q"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]

        _FakeServer.seen.clear()
        _FakeServer.script[:] = [(429, {"error": {"code": 429}}, {"Retry-After": "0"}), (200, _ok_payload(), {})]
        comp = client.complete(model, messages, params)
        t.check("client: retries a 429 then succeeds", comp.status == "ok" and comp.attempts == 2,
                f"status={comp.status} attempts={comp.attempts} error={comp.error}")
        t.check("client: parses text, tokens, reasoning tokens and cost",
                comp.text.endswith("FINAL ANSWER: B") and comp.prompt_tokens == 321
                and comp.completion_tokens == 654 and comp.reasoning_tokens == 500 and comp.cost_usd == 0.0123)
        body = _FakeServer.seen[-1]["body"]
        t.check("client: request has model, temperature 0, max_tokens, reasoning effort high, usage on",
                body["model"] == "vendor/model-x" and body["temperature"] == 0 and body["max_tokens"] == 1234
                and body["reasoning"] == {"effort": "high"} and body["usage"] == {"include": True})
        t.check("client: image is sent as an image_url part; per-model extra_body is merged",
                body["messages"][0]["content"][1]["type"] == "image_url" and body["provider"] == {"order": ["x"]})
        t.check("client: path and bearer auth header",
                _FakeServer.seen[-1]["path"].endswith("/chat/completions")
                and _FakeServer.seen[-1]["headers"].get("Authorization") == "Bearer test-key-not-real")

        _FakeServer.script[:] = [(200, _ok_payload(finish="length"), {})]
        comp = client.complete(model, messages, params)
        t.check("client: finish_reason 'length' is flagged as truncated (never accepted)", comp.status == "truncated")

        _FakeServer.script[:] = [(200, _ok_payload(text="   "), {})]
        t.check("client: blank reply is flagged as empty", client.complete(model, messages, params).status == "empty")

        _FakeServer.script[:] = [(401, {"error": {"message": "bad key"}}, {})]
        comp = client.complete(model, messages, params)
        t.check("client: 401 is an api_error with NO retry", comp.status == "api_error" and comp.attempts == 1)

        _FakeServer.script[:] = [(503, {}, {})] * 3
        comp = client.complete(model, messages, params)
        t.check("client: persistent 503 gives up as api_error after retries",
                comp.status == "api_error" and comp.attempts == 3)
    finally:
        server.shutdown()


# ---- C/D/E. full pipeline in a temp project -------------------------------------------------
def _mock_behaviors():
    def reply(answer):
        return f"Looking at the chart carefully, I measured the bar.\nFINAL ANSWER: {answer}"

    def wrong(expected):
        if isinstance(expected, str):
            return chr((ord(expected) - 65 + 1) % 5 + 65)
        return expected + 30

    def model_a(task_id, expected, call, meta):
        if meta.get("text_only"):
            return reply(wrong(expected))
        if task_id == "selftest_bar_value_001":
            return reply(wrong(expected))  # wrong on purpose
        if task_id == "selftest_bar_value_000":
            return reply(expected + 3)  # inside the +/-5 tolerance, so still correct
        return reply(expected)

    def model_b(task_id, expected, call, meta):
        if task_id == "selftest_bar_letter_002" and call == 1:
            return Completion("truncated", text="I was in the middle of", finish_reason="length",
                              error="reply was cut off at max_tokens; raise max_tokens in results/run_config.json",
                              prompt_tokens=400, completion_tokens=16000, reasoning_tokens=15900,
                              cost_usd=0.05, latency_s=0.02, attempts=1)
        if task_id in ("selftest_bar_letter_001", "selftest_bar_value_002", "selftest_bar_value_000"):
            return reply(wrong(expected))
        return reply(expected)

    return {"mock-a": model_a, "mock-b": model_b}


def _pipeline_checks(ui: UI, t: _Checker, tmp: Path) -> None:
    _section(ui, "C. full pipeline in a temp folder (demo generators + mock models)")
    for d in ("runner", "generate", "validate", "eval", "tasks", "results"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    # Same starting point as the real repo: a placeholder task record and a placeholder-style run config.
    (tmp / "tasks" / "tasks.jsonl").write_text(json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}) + "\n",
                                               encoding="utf-8")
    (tmp / "results" / "run_config.json").write_text(json.dumps({
        "_placeholder": True,
        "models": [{"name": "mock-a", "openrouter_slug": "mock/a"}, {"name": "mock-b", "openrouter_slug": "mock/b"}],
        "temperature": 0, "reasoning_effort": "PLACEHOLDER: high", "max_tokens": "PLACEHOLDER: fixed limit",
        "pricing": {"_source": "kept by the selftest to prove the file is not overwritten"},
        "format_checker": {"enabled": False, "command": "PLACEHOLDER"},
        "run_date": "PLACEHOLDER", "git_commit": "PLACEHOLDER", "prompt_version": "PLACEHOLDER"}, indent=2),
        encoding="utf-8")
    (tmp / "README.md").write_text("# selftest\n<!-- PILOT_RESULTS_PLACEHOLDER -->\n", encoding="utf-8")
    (tmp / ".gitignore").write_text(".env\n", encoding="utf-8")
    cfg = Config.load(tmp, tmp / "results" / "run_config.json", overrides={"task_count_target": 6, "seed": 7})
    registry = discover(extra=[DemoBarLetter, DemoBarValue], include_user=False)
    mock = MockClient(_mock_behaviors())
    import run_tests as rt
    args = SimpleNamespace(task=None, models=None, dry_run=False, text_only_baseline=False, force=False,
                           generate=False, ignore_preflight=False, count=None, generator=None, relaxed=False,
                           strict=False, verbose=False)
    ctx = RunContext(cfg=cfg, out=ui, registry=registry, args=args, client=mock)

    def run_stages(c, stages, generate=False):
        c.args.generate = generate
        aborted = rt.run_selected_stages(c, stages)
        return rt.print_verdict(c, aborted)

    _reset_totals(ui)  # the unit-check lines above must not count toward the pipeline's result
    ui.info("phase 1: preflight -> generate -> load")
    code = run_stages(ctx, [0, 1], generate=True)
    t.check("phase 1 finishes with no failures", code == 0, f"exit code {code}")
    tasks, problems = load_tasks(cfg.tasks_file)
    t.check("generate wrote 6 tasks with data files and images",
            len(tasks) == 6 and not problems and all((tmp / x.image_path).exists() and (tmp / x.data_path).exists() for x in tasks))

    for task in tasks:  # stand-in for humans: real teams run `python -m validate.hand_check ID --by NAME`
        handcheck.record(cfg.tasks_dir, task.id, "selftest", "automated", task_fingerprint(tmp, task))

    _reset_totals(ui)
    ui.info("phase 2: validate -> variation -> evaluate -> score -> summary "
            "(one mock reply is DELIBERATELY truncated: expect that [FAIL] and a PROBLEMS result)")
    run_stages(ctx, [2, 3, 4, 5, 6, 7])
    t.check("all 6 tasks validated (data, render, answer, needs-image, hand-check)",
            ctx.validation is not None and len(ctx.validation.valid_ids) == 6,
            f"invalid: {ctx.validation.invalid_ids if ctx.validation else '?'}")
    t.check("verify_render measured the bar heights from the pixels (step 4 evidence)",
            ctx.validation.count_status("render_matches_data", "pass") == 6)
    invalid = [r for r in ctx.records if r.status != "ok"]
    t.check("the truncated reply was flagged invalid (not scored, not accepted)",
            len(invalid) == 1 and invalid[0].status == "truncated" and invalid[0].model_label == "mock-b")
    t.check("both models were called on every valid task (12 calls)", len(mock.calls) == 12, f"{len(mock.calls)} calls")

    _reset_totals(ui)
    before = len(mock.calls)
    ui.info("phase 3: evaluate again. Valid replies are cached; ONLY the invalid one should be re-run")
    ctx.records = None
    code = run_stages(ctx, [4, 5, 7])
    t.check("re-run called the API exactly once (only the invalid reply)", len(mock.calls) - before == 1,
            f"{len(mock.calls) - before} calls")
    t.check("after the re-run every response is valid and the run is clean",
            all(r.status == "ok" for r in ctx.records) and code == 0, f"exit code {code}")
    c = ctx.scoring["counts"]
    t.check("scoring: tolerance honored (mock-a off by 3 is correct) and difficulty classes right",
            c["both_pass"] == 2 and c["split"] == 4 and c["both_fail"] == 0, str(c))
    rows = (cfg.results_dir / "responses.csv").read_text(encoding="utf-8").strip().splitlines()
    run_cfg = json.loads((cfg.results_dir / "run_config.json").read_text(encoding="utf-8"))
    raw_files = list((cfg.results_dir / "raw").rglob("*.json"))
    t.check("responses.csv has a header and 12 rows", len(rows) == 13, f"{len(rows)} lines")
    t.check("responses.csv starts with the repo's original columns",
            rows[0].startswith("task_id,model,raw_path,extracted_answer,reference_answer,correct,timestamp"), rows[0])
    t.check("run_config.json records temperature 0 and reasoning high; placeholders replaced by what the run used",
            run_cfg["settings"]["temperature"] == 0 and run_cfg["settings"]["reasoning_effort"] == "high"
            and run_cfg["reasoning_effort"] == "high" and run_cfg["max_tokens"] == 16000
            and not str(run_cfg["run_date"]).startswith("PLACEHOLDER") and run_cfg["prompt_version"] != "PLACEHOLDER")
    t.check("run_config.json: the team's own keys (models, pricing, format_checker) are NOT overwritten",
            [m["openrouter_slug"] for m in run_cfg["models"]] == ["mock/a", "mock/b"]
            and run_cfg["pricing"]["_source"].startswith("kept by the selftest") and "format_checker" in run_cfg)
    t.check("12 raw replies saved", len(raw_files) == 12, f"{len(raw_files)} files")
    t.check("summary.md and validation.json written",
            (cfg.results_dir / "summary.md").exists() and (cfg.results_dir / "validation.json").exists())

    # D. deliberately broken tasks must be caught
    _section(ui, "D. deliberately broken tasks must be caught")
    original = load_tasks(cfg.tasks_file)[0]
    broken = copy.deepcopy(original)
    victim = next(x for x in broken if x.id == "selftest_bar_value_000")
    victim.answer = victim.answer + 7
    save_tasks(cfg.tasks_file, broken)
    with ui.quiet():
        rep = validate_tasks(cfg, ui, load_tasks(cfg.tasks_file)[0], registry)
    t.check("tampered ANSWER is caught (answer_recomputes fails, task becomes invalid)",
            rep.status_of(victim.id, "answer_recomputes") == "fail" and victim.id in rep.invalid_ids)
    save_tasks(cfg.tasks_file, original)

    img_task = next(x for x in original if x.id == "selftest_bar_letter_001")
    data = json.loads((tmp / img_task.data_path).read_text(encoding="utf-8"))
    swapped = {**data, "values": list(reversed(data["values"]))}
    gen = registry.generators[img_task.generator]
    gen.render(swapped, tmp / img_task.image_path)
    with ui.quiet():
        rep = validate_tasks(cfg, ui, load_tasks(cfg.tasks_file)[0], registry)
    t.check("tampered IMAGE is caught (render_matches_data fails)",
            rep.status_of(img_task.id, "render_matches_data") == "fail" and img_task.id in rep.invalid_ids)
    gen.render(data, tmp / img_task.image_path)  # restore
    with ui.quiet():
        rep = validate_tasks(cfg, ui, load_tasks(cfg.tasks_file)[0], registry)
    t.check("after restoring, everything validates again", len(rep.valid_ids) == 6)

    # E. packaging
    _section(ui, "E. packaging: secret scan and zip contents")
    (tmp / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-" + "a" * 30 + "\n", encoding="utf-8")
    out = package.build_zip(cfg, ui, "SelfTest", 2)
    t.check("zip builds when clean and hand-checked", out is not None and out.exists())
    if out:
        names = zipfile.ZipFile(out).namelist()
        t.check("zip contains tasks and results; excludes .env",
                "tasks/tasks.jsonl" in names and "results/responses.csv" in names and ".env" not in names)
    (tmp / "leak.txt").write_text("key = sk-or-v1-" + "b" * 30 + "\n", encoding="utf-8")
    with ui.quiet():
        blocked = package.build_zip(cfg, ui, "SelfTest", 2)
    t.check("a planted API key BLOCKS packaging", blocked is None)
    (tmp / "leak.txt").unlink()

    _repo_checks(ui, t, ctx, mock, rt, tmp)


# ---- F. repo-specific behaviour ---------------------------------------------------------------
def _repo_checks(ui: UI, t: _Checker, ctx, mock, rt, tmp: Path) -> None:
    _section(ui, "F. placeholders, other folders, original entry points, key never leaked")
    cfg = ctx.cfg
    repo_root = Path(__file__).resolve().parent.parent

    # F1. a placeholder slug blocks a live run but not a dry run
    ph_cfg = Config(cfg.root, deep_merge(cfg.data, {"models": [
        {"label": "mock-a", "id": "PLACEHOLDER", "extra_body": {}},
        {"label": "mock-b", "id": "PLACEHOLDER", "extra_body": {}}]}))
    args = SimpleNamespace(**{**vars(ctx.args), "dry_run": False, "generate": False})
    live = RunContext(cfg=ph_cfg, out=ui, registry=ctx.registry, args=args, client=mock)
    before = len(mock.calls)
    _reset_totals(ui)
    with ui.quiet():
        aborted = rt.run_selected_stages(live, [4])
    t.check("a PLACEHOLDER model slug stops a live evaluation before any API call",
            aborted and len(mock.calls) == before, f"aborted={aborted} calls={len(mock.calls) - before}")
    args = SimpleNamespace(**{**vars(ctx.args), "dry_run": True, "generate": False})
    dry = RunContext(cfg=ph_cfg, out=ui, registry=ctx.registry, args=args, client=mock)
    _reset_totals(ui)
    with ui.quiet():
        aborted = rt.run_selected_stages(dry, [4])
    t.check("--dry-run still works with PLACEHOLDER slugs and makes no API call",
            not aborted and len(mock.calls) == before and ui.totals["fail"] == 0,
            f"aborted={aborted} calls={len(mock.calls) - before} fails={ui.totals['fail']}")

    # F2. the key is never printed or written
    secret = "sk-or-v1-" + "c" * 30
    old = os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = secret
    try:
        args = SimpleNamespace(**{**vars(ctx.args), "dry_run": False, "generate": False})
        keyed = RunContext(cfg=cfg, out=ui, registry=ctx.registry, args=args, client=mock)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rt.run_selected_stages(keyed, [0])
        t.check("preflight reports the key as set but never prints its value",
                "OPENROUTER_API_KEY is set" in buf.getvalue() and secret not in buf.getvalue())
        saved = [p for p in (cfg.results_dir).rglob("*") if p.is_file()]
        t.check("the key value appears in no file under results/",
                not any(secret in p.read_text(encoding="utf-8", errors="ignore") for p in saved))
    finally:
        if old is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = old

    # F3. runs the same from another folder; no traceback on the untouched repo
    env = {**os.environ, "VTF_NO_LOG": "1", "NO_COLOR": "1"}

    def run_cmd(args_, cwd):
        return subprocess.run([sys.executable, *args_], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)

    here = run_cmd([str(repo_root / "run_tests.py"), "--stage", "1"], repo_root)
    there = run_cmd([str(repo_root / "run_tests.py"), "--stage", "1"], tempfile.gettempdir())
    t.check("run_tests.py gives the same result from another folder (paths anchor to the repo root)",
            here.returncode == there.returncode and "Traceback" not in here.stdout + here.stderr
            + there.stdout + there.stderr, f"rc here={here.returncode} there={there.returncode}")

    # F4. the original entry points still start (help only: nothing is generated or modified)
    for module, args_ in (("validate.run_all", ["-h"]), ("generate.generate_tasks", ["-h"]),
                          ("validate.hand_check", ["-h"]), ("generate.new_generator", [])):
        proc = run_cmd(["-m", module, *args_], repo_root)
        t.check(f"python -m {module} starts without a traceback",
                "Traceback" not in proc.stdout + proc.stderr and proc.returncode in (0, 1, 2),
                (proc.stdout + proc.stderr)[-300:])
    proc = run_cmd(["-c", "import eval.call_models as c, eval.extract_answer as e, eval.score as s, eval.report as r, "
                          "validate.check_answer as a, validate.check_render as cr, validate.check_needs_image as n; "
                          "print(all(hasattr(c, x) for x in ('call_model','load_run_config','save_raw_response','main')) "
                          "and callable(a.compute_reference_answer) and callable(cr.check_render) and callable(n.needs_image) "
                          "and callable(s.score) and callable(r.build_report) and callable(e.extract_final_answer))"], repo_root)
    t.check("the original function names (call_model, compute_reference_answer, check_render, needs_image, score, "
            "build_report) still exist", proc.stdout.strip() == "True", (proc.stdout + proc.stderr)[-300:])

    _review_fix_checks(ui, t, ctx, rt, tmp)


def _review_fix_checks(ui: UI, t: _Checker, ctx, rt, tmp: Path) -> None:
    """Regression checks for the issues the whole-branch review found."""
    import http.client
    import io
    import urllib.request
    from eval import call_models
    from eval.client import OpenRouterClient
    from eval.score import score as legacy_score
    from runner import evaluate as ev
    from runner.preflight import load_dotenv
    from validate.context import legacy_context

    _section(ui, "G. review fixes (each pinned by a check)")
    cfg = ctx.cfg

    # scoring API order and robustness
    t.check("score(extracted, reference, tolerance): '44' vs 42 with tolerance 2 is correct",
            legacy_score("44", 42, 2) is True and legacy_score("44", 42) is False)
    t.check("score: a numeric-looking string reference is scored as a number",
            legacy_score("44", "42", 2) is True and legacy_score("7", "42", 2) is False)
    try:
        bad = is_correct("number", "abc", "5", None)
        t.check("score: a non-numeric stored answer is 'wrong with a reason', not a crash",
                bad[0] is False and "not a number" in bad[1])
    except Exception as exc:  # noqa: BLE001
        t.check("score: a non-numeric stored answer is 'wrong with a reason', not a crash", False, repr(exc))

    # config: top-level budget_usd, odd models entries
    cfg_b, notes_b = from_run_config({"budget_usd": 5, "models": ["oops", {"name": "A", "openrouter_slug": "x/a"}]})
    t.check("config: a top-level budget_usd becomes the eval budget; a non-object model entry is skipped with a note",
            cfg_b["eval"].get("budget_usd") == 5 and len(cfg_b.get("models", [])) == 1
            and any("not an object" in n for n in notes_b), str((cfg_b, notes_b)))

    # saved replies for a different question/settings are never scored
    tasks_now, _ = load_tasks(cfg.tasks_file)
    pair = next(((m, k) for m in cfg.models() for k in tasks_now
                 if call_models.raw_file(cfg, m, k.id).exists()), None)
    if pair is None:
        t.check("stale replies: a saved reply exists to test with", False)
    else:
        model, task_ = pair
        path = call_models.raw_file(cfg, model, task_.id)
        original = path.read_text(encoding="utf-8")
        fresh = call_models.load_records(cfg, [task_], [model])[0].status
        blob = json.loads(original)
        blob["request_key"] = "stale-key"
        path.write_text(json.dumps(blob), encoding="utf-8")
        stale = call_models.load_records(cfg, [task_], [model])[0].status
        path.write_text(original, encoding="utf-8")
        t.check("stale replies: a saved reply whose request_key no longer matches is not_run, a matching one is read",
                fresh != "not_run" and stale == "not_run", f"fresh={fresh} stale={stale}")

    # --task must not overwrite the full results files
    csv_path = cfg.results_dir / "responses.csv"
    if csv_path.exists() and tasks_now:
        before = csv_path.read_bytes()
        ctx.args.task, saved_rows, saved_records = [tasks_now[0].id], ctx.rows, ctx.records
        ctx.rows = ctx.records = None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rt.run_selected_stages(ctx, [5])
        ctx.args.task, ctx.rows, ctx.records = None, saved_rows, saved_records
        t.check("--task: stage 5 leaves results/responses.csv untouched and says so",
                csv_path.read_bytes() == before and "NOT rewritten" in buf.getvalue())
    else:
        t.check("--task: responses.csv exists to test with", False)

    # run record: hand-set prompt_version kept, written to the config file in use
    rc = cfg.results_dir / "run_config.json"
    blob = json.loads(rc.read_text(encoding="utf-8"))
    blob["prompt_version"] = "v-by-hand"
    rc.write_text(json.dumps(blob), encoding="utf-8")
    call_models.write_run_config(cfg, cfg.models(), {"reasoning_effort": "high", "max_tokens": 16000,
                                                     "temperature": 0}, tasks_now,
                                 {"calls": 0, "cached": 0, "spent": 0.0, "unknown_cost_calls": 0})
    t.check("run record: a hand-set prompt_version is kept (only a placeholder is replaced)",
            json.loads(rc.read_text(encoding="utf-8")).get("prompt_version") == "v-by-hand")

    # live one-off call with a placeholder slug
    try:
        call_models.call_model("PLACEHOLDER", "q", Path("x.png"), {})
        t.check("call_model: a placeholder slug is refused with a clear error", False)
    except ValueError as exc:
        t.check("call_model: a placeholder slug is refused with a clear error", "placeholder" in str(exc))
    except Exception as exc:  # noqa: BLE001
        t.check("call_model: a placeholder slug is refused with a clear error", False, repr(exc))

    # .env parsing
    envdir = Path(tempfile.mkdtemp(prefix="vtf_env_"))
    names = ("VTF_T_EXPORT", "VTF_T_COMMENT", "VTF_T_QUOTED")
    try:
        (envdir / ".env").write_text("export VTF_T_EXPORT=abc123\nVTF_T_COMMENT=def456  # my key\nVTF_T_QUOTED=\"gh #789\"\n", encoding="utf-8")
        for n in names:
            os.environ.pop(n, None)
        load_dotenv(envdir)
        t.check(".env: 'export KEY=v' works, an inline comment is stripped, a quoted value keeps its '#'",
                os.environ.get("VTF_T_EXPORT") == "abc123" and os.environ.get("VTF_T_COMMENT") == "def456"
                and os.environ.get("VTF_T_QUOTED") == "gh #789",
                str({n: os.environ.get(n) for n in names}))
    finally:
        for n in names:
            os.environ.pop(n, None)
        shutil.rmtree(envdir, ignore_errors=True)

    # a record with only the repo's six fields gives a clean result, not a TypeError
    try:
        lc = legacy_context({"task_id": "z", "image_path": "tasks/images/z.png", "question": "q",
                             "data": "tasks/data/z.json", "reference_answer": 1, "checks": {}})
        t.check("legacy_context: a six-field record builds a context (data problems are reported, not raised)",
                lc.task.id == "z")
    except Exception as exc:  # noqa: BLE001
        t.check("legacy_context: a six-field record builds a context (data problems are reported, not raised)",
                False, repr(exc))

    # original public names still exist
    names_ok = all(hasattr(ev, n) for n in ("MAX_RETRIES", "is_invalid_reply", "call_model", "call_with_retries",
                                            "save_raw"))
    import eval.extract_answer as ea
    import runner.validate_tasks as vt
    import runner.scorecard as sc
    import runner.output as ro
    import validate.run_all as ra
    import eval.report as er
    names_ok = names_ok and callable(ea.extract_answer) and all(hasattr(vt, n) for n in (
        "CheckResult", "CHECKS", "run_task_checks")) and callable(sc.build_scorecard) and callable(ro.Output.paint) \
        and all(hasattr(ra, n) for n in ("TASKS_PATH", "load_tasks", "run_checks")) and hasattr(er, "RESPONSES_PATH")
    t.check("the original runner/validate/eval function names are all still importable", names_ok)
    t.check("is_invalid_reply accepts a reply dict or a status string",
            ev.is_invalid_reply({"status": "truncated"}) == "truncated" and ev.is_invalid_reply("ok") is None
            and ev.is_invalid_reply({"status": "ok"}) is None)
    t.check("evaluate.call_with_retries: a dry run makes no call and returns not_run",
            (lambda c: ev.call_with_retries(tasks_now[0].id, cfg.models()[0].label, c)["status"] == "not_run")(
                RunContext(cfg=cfg, out=ui, registry=ctx.registry,
                           args=SimpleNamespace(**{**vars(ctx.args), "dry_run": True}), client=ctx.client)))

    # --config that does not exist is an error, not silently the defaults
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "run_tests.py"),
                           "--config", "no/such/file.json", "--stage", "1", "--no-color"],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "VTF_NO_LOG": "1", "NO_COLOR": "1"})
    t.check("--config with a missing file exits 2 with a clear message and no traceback",
            proc.returncode == 2 and "not found" in proc.stdout + proc.stderr
            and "Traceback" not in proc.stdout + proc.stderr, f"rc={proc.returncode} {(proc.stdout + proc.stderr)[-200:]}")

    # network edge cases in the client
    calls = {"n": 0}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(_ok_payload()).encode()

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.IncompleteRead(b"partial")
        return _Resp()

    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = flaky
    try:
        cl = OpenRouterClient("http://localhost:1", "k", max_retries=2, backoff=0, sleep=lambda s: None)
        comp = cl.complete(ModelSpec("A", "x/a"), [{"role": "user", "content": "q"}],
                          {"temperature": 0, "max_tokens": 100, "reasoning_effort": "high"})
        t.check("client: a cut-off response body (IncompleteRead) is retried, not a crash",
                comp.status == "ok" and comp.attempts == 2, f"{comp.status} attempts={comp.attempts}")

        import ssl

        def cert_fail(req, timeout=None):
            raise ssl.SSLError("CERTIFICATE_VERIFY_FAILED")
        urllib.request.urlopen = cert_fail
        comp = cl.complete(ModelSpec("A", "x/a"), [{"role": "user", "content": "q"}],
                          {"temperature": 0, "max_tokens": 100, "reasoning_effort": "high"})
        t.check("client: a certificate failure stops at once with the macOS 'Install Certificates.command' hint",
                comp.status == "api_error" and comp.attempts == 1 and "Install Certificates" in comp.error, comp.error)
    finally:
        urllib.request.urlopen = real_urlopen

    # --generator keeps other generators' tasks
    from generate.generate_tasks import generate_all
    before_tasks, _ = load_tasks(cfg.tasks_file)
    others = sorted(x.id for x in before_tasks if x.generator != "selftest_bar_letter")
    with contextlib.redirect_stdout(io.StringIO()):
        generate_all(cfg, ui, ctx.registry, count=2, only="selftest_bar_letter")
    after_tasks, _ = load_tasks(cfg.tasks_file)
    t.check("--generator regenerates only that generator and keeps the other generators' tasks",
            sorted(x.id for x in after_tasks if x.generator != "selftest_bar_letter") == others and len(others) > 0
            and all((tmp / x.image_path).exists() for x in after_tasks),
            f"others before={others}")

