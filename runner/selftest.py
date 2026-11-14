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

    _mermaid_checks(ui, t, rt, tmp)
    _ownership_checks(ui, t, rt, tmp)
    _vitals_checks(ui, t, rt, tmp)
    _hard_vitals_checks(ui, t, rt, tmp)
    _overlay_vitals_checks(ui, t, rt, tmp)
    _ecg_checks(ui, t, rt, tmp)
    _review_sheet_checks(ui, t, rt, tmp)


_FAKE_MMDC = """#!{py}
import sys, zlib, struct
a = sys.argv
import os
if "fail" in os.path.basename(__file__):
    sys.stderr.write("Error: Chromium could not start"); sys.exit(1)
out = a[a.index("-o") + 1]
def ch(k, p):
    return struct.pack(">I", len(p)) + k + p + struct.pack(">I", zlib.crc32(k + p) & 0xffffffff)
import hashlib
raw = b"".join(b"\\x00" + hashlib.sha256(bytes([i])).digest() * 6 for i in range(64))
open(out, "wb").write(b"\\x89PNG\\r\\n\\x1a\\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 192, 64, 8, 0, 0, 0, 0))
                      + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
# mode: {mode}
"""


def _mermaid_checks(ui: UI, t: _Checker, rt, tmp: Path) -> None:
    """A fixed question + Mermaid source stored with each task (no real Mermaid install needed: a fake mmdc)."""
    import io
    import stat
    from generate import mermaid as mm
    from generate import new_generator
    from generate.generate_tasks import generate_all
    from generate.generators import _template_mermaid as tm
    from generate.registry import Registry

    _section(ui, "H. fixed question + Mermaid source")
    work = Path(tempfile.mkdtemp(prefix="vtf_mermaid_"))
    try:
        def fake(mode: str) -> Path:
            p = work / f"mmdc_{mode}"
            p.write_text(_FAKE_MMDC.format(py=sys.executable, mode=mode), encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
            return p

        old_env = os.environ.get("MMDC")
        try:
            os.environ["MMDC"] = str(work / "does_not_exist")
            try:
                mm.render_mermaid("flowchart TD\n A --> B", work / "x.png")
                t.check("mermaid helper: a missing mmdc gives an install hint", False)
            except RuntimeError as exc:
                t.check("mermaid helper: a missing mmdc gives an install hint", "mermaid-cli" in str(exc))
            os.environ["MMDC"] = str(fake("fail"))
            try:
                mm.render_mermaid("flowchart TD\n A --> B", work / "x.png")
                t.check("mermaid helper: a failing mmdc raises with its error text", False)
            except RuntimeError as exc:
                t.check("mermaid helper: a failing mmdc raises with its error text", "Chromium could not start" in str(exc))
            os.environ["MMDC"] = str(fake("ok"))
            mm.render_mermaid("flowchart TD\n A --> B", work / "x.png")
            t.check("mermaid helper: a working mmdc produces the image file",
                    (work / "x.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")

            class Live(tm.MyGenerator):
                enabled = True
                name = "selftest_mermaid"

            class BadFixed(Live):
                name = "selftest_bad_fixed"
                question_template = "How many arrows lead out of {start}?"
                fixed_question = True

            class NoQuestion(Live):
                name = "selftest_no_question"
                question_template = None
                make_question = TaskGenerator.make_question

            class NoSource(Live):
                name = "selftest_no_source"
                source_key = "missing_key"

            reg_bad = Registry()
            reg_bad.add(BadFixed)
            reg_bad.add(NoQuestion)
            t.check("registry: fixed_question with {placeholders} is rejected; no question_template and no make_question is rejected",
                    not reg_bad.generators and len(reg_bad.problems) == 2, str(reg_bad.problems))

            for d in ("runner", "generate", "validate", "eval", "tasks", "results"):
                (work / d).mkdir(exist_ok=True)
            (work / "tasks" / "tasks.jsonl").write_text(json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}) + "\n", encoding="utf-8")
            (work / "results" / "run_config.json").write_text(json.dumps({"models": [], "temperature": 0}), encoding="utf-8")
            cfg2 = Config.load(work, work / "results" / "run_config.json", overrides={"task_count_target": 4, "seed": 3})
            reg = discover(extra=[Live], include_user=False)
            ctx2 = RunContext(cfg=cfg2, out=ui, registry=reg, client=None, args=SimpleNamespace(
                task=None, models=None, dry_run=True, text_only_baseline=False, force=False, generate=False,
                ignore_preflight=False, count=None, generator=None, relaxed=False, strict=False, verbose=False))
            with contextlib.redirect_stdout(io.StringIO()):
                tasks2, report2 = generate_all(cfg2, ui, reg, count=4)
            t.check("mermaid generator: 4 tasks written, every question identical, answers computed",
                    len(tasks2) == 4 and len({x.question for x in tasks2}) == 1
                    and all(isinstance(x.answer, int) and x.answer >= 1 for x in tasks2), str(report2["failures"]))
            same = all((work / "tasks" / "source" / f"{x.id}.mmd").read_text(encoding="utf-8").rstrip("\n")
                       == json.loads((work / x.data_path).read_text(encoding="utf-8"))["mermaid"] for x in tasks2)
            t.check("mermaid source is stored in the data JSON AND as tasks/source/<id>.mmd (the two match)",
                    bool(tasks2) and same)
            with ui.quiet():
                vres = validate_tasks(cfg2, ui, tasks2, reg, strict=False)
            bad = [(i, n, r.message) for i, rs in vres.results.items() for n, r in rs
                   if n in ("render_matches_data", "answer_recomputes", "data_reproducible") and r.status == "fail"]
            t.check("validation: data reproducible, answer recomputes and the Mermaid source matches the data's edges",
                    not bad and vres.count_status("render_matches_data", "pass") == 4, str(bad))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ctx2.args.dry_run = True
                rt.run_selected_stages(ctx2, [3])
            text = buf.getvalue()
            t.check("variation: a declared fixed question is reported as by design, not as a near-identical warning",
                    "by design" in text and "near-identical question text" not in text, text[-400:])
            with contextlib.redirect_stdout(io.StringIO()):
                tasks3, _ = generate_all(cfg2, ui, reg, count=4)
            t.check("regenerating leaves exactly one source file per task (no stale ones)",
                    sorted(p.stem for p in (work / "tasks" / "source").glob("*.mmd")) == sorted(x.id for x in tasks3))
            reg2 = discover(extra=[NoSource], include_user=False)
            with contextlib.redirect_stdout(io.StringIO()):
                _, rep3 = generate_all(cfg2, ui, reg2, count=1)
            t.check("a generator whose data lacks its source_key fails with a clear message",
                    len(rep3["failures"]) == 1 and "missing_key" in rep3["failures"][0]["error"], str(rep3))
        finally:
            if old_env is None:
                os.environ.pop("MMDC", None)
            else:
                os.environ["MMDC"] = old_env

        gen_dir = work / "gens"
        gen_dir.mkdir()
        real_dir = Path(new_generator.__file__).resolve().parent / "generators"
        for f in ("_template.py", "_template_mermaid.py"):
            shutil.copy(real_dir / f, gen_dir / f)
        saved = new_generator.GEN_DIR
        new_generator.GEN_DIR = gen_dir
        try:
            dest = new_generator.create("my_flow", mermaid=True)
            text = dest.read_text(encoding="utf-8")
            t.check("new_generator --mermaid copies the Mermaid template and renames it",
                    'name = "my_flow"' in text and "source_key" in text and "class MyFlowGenerator" in text)
        finally:
            new_generator.GEN_DIR = saved
    finally:
        shutil.rmtree(work, ignore_errors=True)



def _audit_chart(chart: dict, level: int) -> list[str]:
    """Check one ownership chart against the 20 rules, by a DIFFERENT method than the generator (routes are
    enumerated one by one). Returns the list of broken rules (empty = all hold)."""
    from fractions import Fraction as Fr
    bad: list[str] = []
    ents, edges, target = chart["entities"], chart["edges"], chart["target"]
    kind = {e["id"]: e["kind"] for e in ents}
    people = [i for i in kind if kind[i] == "person"]
    companies = [i for i in kind if kind[i] == "company"]
    out: dict[str, list[tuple[str, int]]] = {i: [] for i in kind}
    inn: dict[str, list[tuple[str, int]]] = {i: [] for i in kind}
    for e in edges:
        out[e["owner"]].append((e["company"], e["percent"]))
        inn[e["company"]].append((e["owner"], e["percent"]))
    n = len(ents)
    p = level
    t = Fr(level - 1, 9)
    if chart["level"] != level:
        bad.append("20 level not recorded")
    # structure 1-8
    if [i for i in companies if not out[i]] != [target]:
        bad.append("1 exactly one company (the target) owns nothing")
    # routes down from every entity (a loop would recurse forever, so cap the depth)
    def routes(node, prod=Fr(1), depth=0):
        if depth > n:
            raise RecursionError("loop")
        if node == target:
            yield prod
            return
        for nxt, pc in out[node]:
            yield from routes(nxt, prod * Fr(pc, 100), depth + 1)
    try:
        per_person = {}
        for i in kind:
            rs = list(routes(i))
            if not rs:
                bad.append(f"3 {i} has no route to the target")
            per_person[i] = rs
    except RecursionError:
        return bad + ["2 the arrows contain a loop"]
    if len(people) != round(0.4 * n):
        bad.append("5 people are not 40% of entities")
    if any(inn[i] for i in people):
        bad.append("6 an arrow points into a person")
    if any(not out[i] for i in people):
        bad.append("7 a person owns nothing")
    if any(not (1 <= len(inn[c]) <= 4) for c in companies):
        bad.append("7 a company has <1 or >4 owners")
    if any(len({o for o, _ in inn[c]}) != len(inn[c]) for c in companies):
        bad.append("8 two arrows between one owner and one company")
    # numbers 9-11
    if any(e["percent"] % 5 or not 5 <= e["percent"] <= 100 for e in edges):
        bad.append("9 a stake is not a whole 5% step in 5..100")
    if any(sum(pc for _, pc in inn[c]) != 100 for c in companies):
        bad.append("10 stakes into a company do not add to 100")
    eff = {i: sum(per_person[i]) * 100 for i in people}
    if sum(eff.values()) != 100:
        bad.append("11 people's effective stakes do not add to 100")
    # answer 12-15
    above = [v for v in eff.values() if v >= 25]
    below = [v for v in eff.values() if v < 25]
    if not above or not below:
        bad.append("12 need someone at/above and someone below 25%")
    if any(abs(v - 25) <= 2 for v in eff.values()):
        bad.append("13 someone is within 2 points of 25%")
    answer = len(above)
    route_set = {i for i in people if max(per_person[i]) * 100 >= 25}
    arrow_set = {i for i in people if any(pc >= 25 for _, pc in out[i])}
    for name, sset in (("biggest single route", route_set), ("any arrow", arrow_set)):
        if len(sset) == answer or sset == {i for i in people if eff[i] >= 25}:
            bad.append(f"14 the '{name}' shortcut gives the right answer")
    gap = 40 - t * 35
    if above and below and (min(v - 25 for v in above) > gap or min(25 - v for v in below) > gap):
        bad.append("15 nearest owner on a side is farther than the level's gap")
    # level dial 16
    if n != round(8 + t * 14):
        bad.append("16 wrong entity count for the level")
    # names 17
    names = [e["name"] for e in ents]
    if len({x.lower() for x in names}) != len(names):
        bad.append("17 duplicate names")
    endings = {"Holdings", "Capital", "Group", "Partners", "Ventures", "Trust", "Industries", "Enterprises", "Investments", "Associates"}
    for e in ents:
        words = e["name"].split()
        if e["kind"] == "person" and (len(words) != 2 or not all(w.isalpha() for w in words)):
            bad.append("17 a person is not two made-up words")
        if e["kind"] == "company" and (len(words) != 2 or words[1] not in endings or not words[0].isalpha()):
            bad.append("17 a company is not a word plus one of the ten endings")
    # picture text 18-20
    src = chart["mermaid"]
    lines = src.splitlines()
    if not (lines[0].startswith("%%{init:") and lines[0].endswith("}%%")):
        bad.append("20 the init directive is not wrapped in %%{ ... }%%")
    if '"fontSize": "16px"' not in lines[0] or '"useMaxWidth": false' not in lines[0]:
        bad.append("19 labels are not fixed at 16px / scaling is not off")
    if "flowchart TB" not in lines:
        bad.append("18 layout is not top-down")
    if f"%% level: {level}" not in lines or f"%% seed: {chart['seed']}" not in lines:
        bad.append("20 level and seed are not written into the .mmd")
    for e in ents:
        shape = f'{e["id"]}(["{e["name"]}"])' if e["kind"] == "person" else f'{e["id"]}["{e["name"]}"]'
        if f"    {shape}" not in lines:
            bad.append(f"18 {e['id']} is not drawn as a {'pill' if e['kind'] == 'person' else 'rectangle'}")
    for e in edges:
        if f'    {e["owner"]} -->|{e["percent"]}%| {e["company"]}' not in lines:
            bad.append("18 an arrow has no percent label")
    return bad


def _walked_answer(chart: dict) -> int:
    """How many people hold 25%+ of the target, found by walking down every route (no shared code with the generator)."""
    from fractions import Fraction as Fr
    out: dict[str, list[tuple[str, int]]] = {}
    for e in chart["edges"]:
        out.setdefault(e["owner"], []).append((e["company"], e["percent"]))

    def walk(node: str, prod: Fr):
        if node == chart["target"]:
            yield prod
        for nxt, pc in out.get(node, []):
            yield from walk(nxt, prod * Fr(pc, 100))

    return sum(1 for e in chart["entities"] if e["kind"] == "person" and sum(walk(e["id"], Fr(1))) * 100 >= 25)


def _ownership_checks(ui: UI, t: _Checker, rt, tmp: Path) -> None:
    """The ownership-chart generator against the 20 rules, at every level, over many seeds."""
    import io
    from generate.generators import ownership_chart as oc

    _section(ui, "I. ownership chart: the 20 rules")
    broken: list[str] = []
    counts: set[int] = set()
    for level in range(1, 11):
        for k in range(12):
            chart = oc.make_chart(level, 1000 * level + 97 * k, fits=lambda s: True)
            problems = _audit_chart(chart, level)
            broken += [f"level {level} seed {chart['seed']}: {x}" for x in problems]
            counts.add(oc.beneficial_owner_count(chart))
    t.check("120 charts (12 per level, levels 1-10) satisfy all 20 rules, checked by independent route enumeration",
            not broken, "; ".join(broken[:4]))
    t.check("the answers vary across charts (not always the same number)", len(counts) >= 3, str(sorted(counts)))
    first = oc.make_chart(7, 4242, fits=lambda s: True)
    again = oc.build_chart(7, first["seed"])
    t.check("the same level and seed always rebuild the identical chart [20]", again == first)
    t.check("compute_answer() equals the answer found by walking every route one by one",
            all(oc.OwnershipChartGenerator().compute_answer(c) == _walked_answer(c)
                for c in (oc.make_chart(lv, 31 * lv, fits=lambda s: True) for lv in range(1, 11))))

    # a picture that does not fit is redrawn from the NEXT seed [19]
    calls = {"n": 0}

    def too_big_twice(src: str) -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    redrawn = oc.make_chart(3, 777, fits=too_big_twice)
    structural = [s for s in range(777, 777 + 200) if oc.build_chart(3, s) is not None]
    t.check("an oversized picture is rejected and the chart is redrawn from the next valid seed [19]",
            redrawn["seed"] == structural[2] and calls["n"] == 3, f"{redrawn['seed']} vs {structural[:3]}")

    # the generator class: registered, fixed question, verify_render catches a tampered source
    gen = oc.OwnershipChartGenerator()
    class _LiveOwnership(type(gen)):
        enabled = True

    reg = discover(extra=[_LiveOwnership], include_user=False)
    t.check("ownership_chart is registered with its fixed question and exact answer",
            "ownership_chart" in reg.generators and gen.fixed_question and gen.exact_answer
            and "{" not in gen.question_template)
    png = tmp / "own.png"
    import stat
    fake = tmp / "mmdc_own"
    fake.write_text(_FAKE_MMDC.format(py=sys.executable, mode="ok"), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    old = os.environ.get("MMDC")
    os.environ["MMDC"] = str(fake)
    try:
        gen.render(first, png)
        good = gen.verify_render(first, png)
        tampered = dict(first, mermaid=first["mermaid"].replace("-->|", "-->|9", 1))
        bad_text = gen.verify_render(tampered, png)
        small = gen.picture_fits(first["mermaid"])
    finally:
        if old is None:
            os.environ.pop("MMDC", None)
        else:
            os.environ["MMDC"] = old
    t.check("verify_render passes a matching chart and fails a tampered Mermaid source",
            good.status == "pass" and bad_text.status == "fail", f"{good.message} | {bad_text.message}")
    t.check("picture_fits measures the PNG size (the stand-in draws 192x64, which fits)", small is True)


def _vitals_checks(ui: UI, t: _Checker, rt, tmp: Path) -> None:
    """Synthetic patient-monitoring charts: data rules, picture-vs-data pixels, and answers recomputed independently."""
    import io
    from PIL import Image
    from generate.generate_tasks import generate_all
    from generate.generators import _vitals as vt
    from generate.generators.vitals_final_value import VitalsFinalValueGenerator
    from generate.generators.vitals_first_change import VitalsFirstChangeGenerator
    from generate.generators.vitals_onset import VitalsOnsetGenerator
    from generate.generators.vitals_threshold import VitalsThresholdGenerator

    _section(ui, "J. synthetic patient-monitoring charts")
    # --- determinism
    a = vt.build_patient(random.Random(5), 4)
    b = vt.build_patient(random.Random(5), 4)
    c = vt.build_patient(random.Random(6), 4)
    t.check("the same seed gives the identical patient data; another seed gives different data", a == b and a != c)

    # --- data rules at every level
    problems: list[str] = []
    for level in range(1, 11):
        for k in range(8):
            d = vt.build_patient(random.Random(100 * level + k), level)
            n, step, dur = len(d["panels"]), d["time_step"], d["duration"]
            want_panels = 2 if level <= 3 else 3 if level <= 7 else 4
            if n != want_panels:
                problems.append(f"L{level}: {n} panels, expected {want_panels}")
            if d["times"] != list(range(0, dur + 1, step)):
                problems.append(f"L{level}: bad time grid")
            if len({p["key"] for p in d["panels"]}) != n:
                problems.append(f"L{level}: a variable appears twice")
            for p in d["panels"]:
                vals = p["values"]
                if len(vals) != len(d["times"]) or vals[0] is None or vals[-1] is None:
                    problems.append(f"L{level}: first/last reading missing or wrong length")
                if level <= 2 and any(v is None for v in vals):
                    problems.append(f"L{level}: missing readings at an easy level")
                bound = vt.noise_bound(p, d["noise_mult"])
                for tm, v in zip(d["times"], vals):
                    if v is not None and abs(v - vt.clean_value(p, tm)) > bound + p["quantum"] + 1e-9:
                        problems.append(f"L{level}: reading off its clean curve by more than the noise bound")
                        break
                if p["trend"] == "flat" and p["onset"] is not None:
                    problems.append("flat panel has an onset")
                if p["trend"] != "flat":
                    if p["onset"] % step or not 0 < p["onset"] < dur:
                        problems.append(f"L{level}: onset not on the time grid")
                    if abs(p["delta"]) < 8 * bound / 2.5:
                        problems.append(f"L{level}: trend too small to see against the noise")
                if any(v is not None and not p["y_min"] <= v <= p["y_max"] for v in vals):
                    problems.append(f"L{level}: a reading is outside the axis")
    t.check("80 patients (8 per level, levels 1-10): panel count, time grid, noise bound, onsets and axes obey the rules",
            not problems, "; ".join(problems[:3]))

    # --- picture matches the data (pixels), and a tampered picture / tampered data is caught
    d = vt.build_patient(random.Random(11), 9)  # a level with 4 panels and missing readings
    png = tmp / "vitals.png"
    vt.render_png(d, png)
    ok = vt.verify_png(d, png)
    t.check("the picture matches the data when its pixels are re-measured (readings on the line, missing ones as gaps)",
            ok.status == "pass", ok.message)
    img = Image.open(png).convert("RGB")
    box = vt.layout(d)["panels"][0]
    for x in range(box["x0"] + 5, box["x1"] - 5):  # paint over panel A's plot area with white
        for y in range(box["y0"] + 1, box["y1"] - 1):
            img.putpixel((x, y), (255, 255, 255))
    bad_png = tmp / "vitals_bad.png"
    img.save(bad_png)
    t.check("verify_png fails when a panel's line has been erased from the picture",
            vt.verify_png(d, bad_png).status == "fail")
    moved = copy.deepcopy(d)
    j = next(i for i, v in enumerate(moved["panels"][0]["values"]) if v is not None and 3 < i < len(moved["times"]) - 3)
    moved["panels"][0]["values"][j] += (moved["panels"][0]["y_max"] - moved["panels"][0]["y_min"]) / 3
    t.check("verify_png fails when one reading in the data no longer matches the picture",
            vt.verify_png(moved, png).status == "fail")
    vt.render_png(d, tmp / "v2.png")
    t.check("the picture is deterministic (rendering twice gives identical bytes)",
            (tmp / "v2.png").read_bytes() == png.read_bytes())

    # --- the four generators, end to end in a temp folder
    work = Path(tempfile.mkdtemp(prefix="vtf_vitals_"))
    try:
        for dname in ("runner", "generate", "validate", "eval", "tasks", "results"):
            (work / dname).mkdir()
        (work / "tasks" / "tasks.jsonl").write_text(json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}) + "\n", encoding="utf-8")
        (work / "results" / "run_config.json").write_text(json.dumps({"models": [], "temperature": 0}), encoding="utf-8")
        cfg3 = Config.load(work, work / "results" / "run_config.json", overrides={"seed": 21})
        classes = [type(c.__name__, (c,), {"enabled": True})
                   for c in (VitalsOnsetGenerator, VitalsFinalValueGenerator, VitalsThresholdGenerator,
                             VitalsFirstChangeGenerator)]
        reg3 = discover(extra=classes, include_user=False)
        t.check("all four vitals generators register (examples cited, required methods filled)",
                len(reg3.generators) == 4 and not reg3.problems, str(reg3.problems))
        with contextlib.redirect_stdout(io.StringIO()):
            tasks3, rep3 = generate_all(cfg3, ui, reg3, count=20)
        t.check("20 tasks (5 per generator) are generated with no failures", len(tasks3) == 20 and not rep3["failures"],
                str(rep3["failures"][:2]))
        levels = sorted({json.loads((work / x.data_path).read_text(encoding="utf-8"))["level"] for x in tasks3})
        t.check("the tasks spread over many difficulty levels (not only the easy ones)", len(levels) >= 6, str(levels))
        wrong = []
        for x in tasks3:
            dd = json.loads((work / x.data_path).read_text(encoding="utf-8"))
            tgt = dd.get("target")
            if x.generator == "vitals_onset":
                want = dd["panels"][tgt]["onset"]
            elif x.generator == "vitals_final_value":
                want = [v for v in dd["panels"][tgt]["values"] if v is not None][-1]
            elif x.generator == "vitals_threshold":
                p = dd["panels"][tgt]
                up = p["delta"] > 0
                want = next(tm for tm, v in zip(dd["times"], p["values"])
                            if v is not None and (v > dd["threshold"] if up else v < dd["threshold"]))
            else:
                want = min((p["onset"], p["letter"]) for p in dd["panels"] if p["trend"] != "flat")[1]
            if str(x.answer) != str(want) and not (isinstance(want, float) and abs(float(x.answer) - want) < 1e-9):
                wrong.append((x.id, x.answer, want))
        t.check("every stored answer equals the answer found by re-reading the saved data independently",
                not wrong, str(wrong[:3]))
        with ui.quiet():
            vres = validate_tasks(cfg3, ui, tasks3, reg3, strict=False)
        fails = [(i, n, r.message) for i, rs in vres.results.items() for n, r in rs if r.status == "fail"]
        t.check("validation: no check fails on any of the 20 tasks (data, picture-vs-data, answer, needs-image)",
                not fails and vres.count_status("render_matches_data", "pass") == 20, str(fails[:2]))
        numeric = [x for x in tasks3 if x.answer_type == "number"]
        t.check("numeric tasks carry their own tolerance and the question states it",
                all(x.tolerance and f"{x.tolerance:g}" in x.question for x in numeric) and len(numeric) == 15)
        fc = [x for x in tasks3 if x.generator == "vitals_first_change"]
        t.check("the first-change tasks have a letter answer that matches a panel, with one fixed question",
                all(x.answer in ("A", "B", "C", "D") for x in fc) and len({x.question for x in fc}) == 1)
        t.check("the questions are not all the same wording within a generator (phrasing varies)",
                len({x.question for x in tasks3 if x.generator == "vitals_onset"}) >= 2)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _hard_vitals_checks(ui: UI, t: _Checker, rt, tmp: Path) -> None:
    """HARD vitals tasks: gap count, crossing count, exact reading. Answers recomputed by separate code."""
    from generate.generate_tasks import generate_all
    from generate.generators import _vitals as vt
    from generate.generators.vitals_crossing_count import VitalsCrossingCountGenerator
    from generate.generators.vitals_exact_reading import VitalsExactReadingGenerator
    from generate.generators.vitals_gap_count import VitalsGapCountGenerator
    from generate.generators.vitals_final_value import VitalsFinalValueGenerator
    from generate.generators.vitals_first_change import VitalsFirstChangeGenerator
    from generate.generators.vitals_onset import VitalsOnsetGenerator
    from generate.generators.vitals_threshold import VitalsThresholdGenerator

    _section(ui, "K. hard vitals tasks (count the gaps, count the crossings, read an exact value)")
    work = Path(tempfile.mkdtemp(prefix="vtf_hard_"))
    try:
        for dname in ("runner", "generate", "validate", "eval", "tasks", "results"):
            (work / dname).mkdir()
        (work / "tasks" / "tasks.jsonl").write_text(json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}) + "\n", encoding="utf-8")
        (work / "results" / "run_config.json").write_text(json.dumps({"models": [], "temperature": 0}), encoding="utf-8")
        cfg = Config.load(work, work / "results" / "run_config.json", overrides={"seed": 33})
        reg = discover(extra=[type(c.__name__, (c,), {"enabled": True}) for c in (VitalsGapCountGenerator, VitalsCrossingCountGenerator, VitalsExactReadingGenerator)], include_user=False)
        t.check("the three hard generators register when enabled (examples cited, methods filled)",
                {"vitals_gap_count", "vitals_crossing_count", "vitals_exact_reading"} <= set(reg.generators) and not reg.problems,
                str(reg.problems))
        t.check("the vitals and overlay generators are all switched off by default (the live tasks are the ECG strips)",
                not any(c.enabled for c in (VitalsGapCountGenerator, VitalsCrossingCountGenerator, VitalsExactReadingGenerator,
                                            VitalsOnsetGenerator, VitalsFinalValueGenerator, VitalsThresholdGenerator,
                                            VitalsFirstChangeGenerator)))
        with contextlib.redirect_stdout(io.StringIO()):
            tasks, rep = generate_all(cfg, ui, reg, count=12)
        t.check("12 hard tasks (4 per generator) are generated with no failures", len(tasks) == 12 and not rep["failures"],
                str(rep["failures"][:2]))
        wrong, notes = [], []
        for x in tasks:
            d = json.loads((work / x.data_path).read_text(encoding="utf-8"))
            p = d["panels"][d["target"]]
            vals = p["values"]
            if x.generator == "vitals_gap_count":
                runs = 0
                for i, v in enumerate(vals):
                    if v is None and (i == 0 or vals[i - 1] is not None):
                        runs += 1
                want = runs
                if not 9 <= runs <= 20 or len(d["times"]) < 91 or len(d["panels"]) != 5:
                    notes.append(f"{x.id}: gap task out of range ({runs} gaps, {len(d['times'])} readings)")
            elif x.generator == "vitals_crossing_count":
                ref = d["ref"]["value"]
                want = 0
                pres = [v for v in vals if v is not None]
                for a, b in zip(pres, pres[1:]):
                    if (a - ref) * (b - ref) < 0:
                        want += 1
                box = vt.layout(d)["panels"][d["target"]]
                gap_px = min(abs(v - ref) for v in pres) * (box["y1"] - box["y0"]) / (p["y_max"] - p["y_min"])
                if None in vals or gap_px < 3 or not 8 <= want <= 22 or p["trend"] != "flat":
                    notes.append(f"{x.id}: crossing task breaks its rules (min {gap_px:.1f}px, {want} crossings)")
            else:
                want = vals[d["times"].index(d["minute"])]
                if want is None or d["minute"] not in vt._x_ticks(d["duration"]):
                    notes.append(f"{x.id}: reading task asks about an unlabelled or missing minute")
                if x.tolerance != p["quantum"]:
                    notes.append(f"{x.id}: tolerance {x.tolerance} is not one unit ({p['quantum']})")
            if abs(float(x.answer) - float(want)) > 1e-9:
                wrong.append((x.id, x.answer, want))
        t.check("every stored answer equals the answer re-counted / re-read from the saved data by separate code",
                not wrong, str(wrong[:3]))
        t.check("each task obeys its own rules (ranges, gaps, 3-pixel clearance, labelled minute, one-unit tolerance)",
                not notes, "; ".join(notes[:3]))
        with ui.quiet():
            vres = validate_tasks(cfg, ui, tasks, reg, strict=False)
        fails = [(i, n, r.message) for i, rs in vres.results.items() for n, r in rs if r.status == "fail"]
        t.check("validation: no check fails and every picture matches its data (including the dashed line)",
                not fails and vres.count_status("render_matches_data", "pass") == 12, str(fails[:2]))
        cx = next(x for x in tasks if x.generator == "vitals_crossing_count")
        d = json.loads((work / cx.data_path).read_text(encoding="utf-8"))
        d2 = json.loads(json.dumps(d))
        d2["ref"]["value"] = round(d2["ref"]["value"] + 0.5 * (d2["panels"][d2["ref"]["panel"]]["y_max"] - d2["panels"][d2["ref"]["panel"]]["y_min"]) / 4, 3)
        res = vt.verify_png(d2, work / cx.image_path)
        t.check("verify_png fails when the dashed reference line is not where the data says",
                res.status == "fail", res.message)
        g1 = VitalsGapCountGenerator()
        a1 = g1.generate_data(random.Random(9), 1)
        b1 = g1.generate_data(random.Random(9), 1)
        t.check("hard patients are deterministic (same seed, same data and answer)",
                a1 == b1 and g1.compute_answer(a1) == g1.compute_answer(b1))
        t.check("every hard task is level 8-10 (the generators never produce an easy chart)",
                all(8 <= json.loads((work / x.data_path).read_text(encoding="utf-8"))["level"] <= 10 for x in tasks))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _overlay_vitals_checks(ui: UI, t: _Checker, rt, tmp: Path) -> None:
    """OVERLAY vitals tasks: six lines on one axis. Answers recomputed by separate code."""
    from generate.generate_tasks import generate_all
    from generate.generators import _overlay as ov
    from generate.generators.overlay_cross_count import OverlayCrossCountGenerator
    from generate.generators.overlay_highest_at import OverlayHighestAtGenerator
    from generate.generators.overlay_peak_minute import OverlayPeakMinuteGenerator

    _section(ui, "L. overlay vitals tasks (six overlapping lines on one chart)")
    work = Path(tempfile.mkdtemp(prefix="vtf_overlay_"))
    try:
        for dname in ("runner", "generate", "validate", "eval", "tasks", "results"):
            (work / dname).mkdir()
        (work / "tasks" / "tasks.jsonl").write_text(json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}) + "\n", encoding="utf-8")
        (work / "results" / "run_config.json").write_text(json.dumps({"models": [], "temperature": 0}), encoding="utf-8")
        cfg = Config.load(work, work / "results" / "run_config.json", overrides={"seed": 77})
        reg = discover(extra=[type(c.__name__, (c,), {"enabled": True}) for c in (OverlayCrossCountGenerator, OverlayHighestAtGenerator, OverlayPeakMinuteGenerator)], include_user=False)
        t.check("the three overlay generators register when enabled (examples cited, methods filled)",
                {"overlay_cross_count", "overlay_highest_at", "overlay_peak_minute"} <= set(reg.generators) and not reg.problems,
                str(reg.problems))
        with contextlib.redirect_stdout(io.StringIO()):
            tasks, rep = generate_all(cfg, ui, reg, count=12)
        t.check("12 overlay tasks (4 per generator) are generated with no failures", len(tasks) == 12 and not rep["failures"],
                str(rep["failures"][:2]))
        wrong, notes = [], []
        for x in tasks:
            d = json.loads((work / x.data_path).read_text(encoding="utf-8"))
            if len(d["series"]) != 6 or len(d["times"]) < 91:
                notes.append(f"{x.id}: not 6 series x 91+ readings")
            if x.generator == "overlay_cross_count":
                a, b = (d["series"][k]["values"] for k in d["pair"])
                diffs = [p - q for p, q in zip(a[1:], b[1:])]
                want = sum(1 for p, q in zip(diffs, diffs[1:]) if p * q < 0)
                if 0 in diffs or not 8 <= want <= 22:
                    notes.append(f"{x.id}: cross task breaks its rules ({want} crossings, tie={0 in diffs})")
            elif x.generator == "overlay_highest_at":
                i = d["times"].index(d["minute"])
                col = sorted(((s["values"][i], s["letter"]) for s in d["series"]), reverse=True)
                want = col[0][1]
                if col[0][0] - col[1][0] < 0.7 or d["minute"] not in ov.x_ticks(d["duration"]):
                    notes.append(f"{x.id}: highest-at task is a photo finish or an unlabelled minute")
            else:
                vals = d["series"][d["target"]]["values"]
                srt = sorted(vals, reverse=True)
                want = d["times"][vals.index(srt[0])]
                if srt[0] - srt[1] < 0.8 or vals.index(srt[0]) in (0, len(vals) - 1) or x.tolerance != d["time_step"]:
                    notes.append(f"{x.id}: peak task breaks its rules")
            if str(x.answer) != str(want) and not (isinstance(want, int) and abs(float(x.answer) - want) < 1e-9):
                wrong.append((x.id, x.answer, want))
        t.check("every stored answer equals the answer re-counted / re-read from the saved data by separate code",
                not wrong, str(wrong[:3]))
        t.check("each task obeys its own rules (6 series, 91+ readings, no ties or photo finishes, one-interval tolerance)",
                not notes, "; ".join(notes[:3]))
        with ui.quiet():
            vres = validate_tasks(cfg, ui, tasks, reg, strict=False)
        fails = [(i, n, r.message) for i, rs in vres.results.items() for n, r in rs if r.status == "fail"]
        t.check("validation: no check fails and every picture matches its data",
                not fails and vres.count_status("render_matches_data", "pass") == 12, str(fails[:2]))
        first = tasks[0]
        d = json.loads((work / first.data_path).read_text(encoding="utf-8"))
        d["series"][2]["values"][5] = round(d["series"][2]["values"][5] + 3.0, 1)
        res = ov.verify_png(d, work / first.image_path)
        t.check("verify_png fails when one reading in the data no longer matches the picture",
                res.status == "fail", res.message)
        g = OverlayCrossCountGenerator()
        a1, b1 = g.generate_data(random.Random(4), 0), g.generate_data(random.Random(4), 0)
        t.check("overlay charts are deterministic (same seed, same data and answer)",
                a1 == b1 and g.compute_answer(a1) == g.compute_answer(b1))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _ecg_checks(ui: UI, t: _Checker, rt, tmp: Path) -> None:
    """ECG rhythm strips: the picture is the data, and every answer is re-derived from the raw samples by separate code."""
    from generate.generate_tasks import generate_all
    from generate.generators import _ecg as ec
    from generate.generators.ecg_beat_count import EcgBeatCountGenerator
    from generate.generators.ecg_ectopic_count import EcgEctopicCountGenerator
    from generate.generators.ecg_interval import EcgIntervalGenerator
    from generate.generators.ecg_premature_beat import EcgPrematureBeatGenerator
    from generate.generators.ecg_rate import EcgRateGenerator

    _section(ui, "M. synthetic ECG rhythm strips")
    a = ec.build_strip(random.Random(5), "pvc", 70)
    b = ec.build_strip(random.Random(5), "pvc", 70)
    t.check("the same seed gives identical strip data; another seed gives different data",
            a == b and a != ec.build_strip(random.Random(6), "pvc", 70))
    work = Path(tempfile.mkdtemp(prefix="vtf_ecg_"))
    try:
        for dname in ("runner", "generate", "validate", "eval", "tasks", "results"):
            (work / dname).mkdir()
        (work / "tasks" / "tasks.jsonl").write_text(json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}) + "\n", encoding="utf-8")
        (work / "results" / "run_config.json").write_text(json.dumps({"models": [], "temperature": 0}), encoding="utf-8")
        cfg = Config.load(work, work / "results" / "run_config.json", overrides={"seed": 91})
        classes = [EcgBeatCountGenerator, EcgRateGenerator, EcgPrematureBeatGenerator, EcgIntervalGenerator, EcgEctopicCountGenerator]
        reg = discover(extra=classes, include_user=False)
        t.check("all five ECG generators register by default (examples cited, methods filled)",
                {"ecg_beat_count", "ecg_rate", "ecg_premature_beat", "ecg_interval", "ecg_ectopic_count"} <= set(reg.generators) and not reg.problems,
                str(reg.problems))
        with contextlib.redirect_stdout(io.StringIO()):
            tasks, rep = generate_all(cfg, ui, reg, count=20)
        t.check("20 ECG tasks (4 per generator) are generated with no failures", len(tasks) == 20 and not rep["failures"],
                str(rep["failures"][:2]))
        wrong, notes = [], []
        for x in tasks:
            d = json.loads((work / x.data_path).read_text(encoding="utf-8"))
            fs = d["fs"]
            peaks = ec.find_beats(d["samples"], d["amp"])  # separate beat finder, from the raw samples only
            stored = [bt["i"] for bt in d["beats"]]
            if len(peaks) != len(stored) or any(abs(p - q) > ec.APEX_SLACK for p, q in zip(peaks, stored)):  # apex may sit a sample or two off the centre
                notes.append(f"{x.id}: beat finder disagrees with the stored beats")
            if peaks[0] < 0.4 * fs or peaks[-1] > len(d["samples"]) - 0.4 * fs:
                notes.append(f"{x.id}: a beat is too close to the strip edge")
            gaps = [q - p for p, q in zip(peaks, peaks[1:])]
            if x.generator == "ecg_beat_count":
                want = len(peaks)
            elif x.generator == "ecg_rate":
                want = round(60 * fs / (sum(gaps) / len(gaps)))
                sg = [q - p for p, q in zip(stored, stored[1:])]
                if max(sg) - min(sg) > 2:  # the stored beat spacing is steady to within one sample of jitter
                    notes.append(f"{x.id}: rate strip is not steady")
            elif x.generator in ("ecg_premature_beat", "ecg_ectopic_count"):
                med = sorted(gaps)[len(gaps) // 2]
                early = [k + 2 for k, g in enumerate(gaps) if g < 0.75 * med]
                if x.generator == "ecg_premature_beat":
                    if len(early) != 1:
                        notes.append(f"{x.id}: expected exactly one early beat, found {len(early)}")
                    want = early[0] if early else None
                else:
                    if not 3 <= len(early) <= 5 or any(q - p == 1 for p, q in zip(early, early[1:])):
                        notes.append(f"{x.id}: expected 3-5 separate early beats, found {early}")
                    want = len(early)
            else:
                a_, b_ = d["pair"]
                want = round((peaks[b_ - 1] - peaks[a_ - 1]) / fs / 0.2, 2)
                if x.tolerance != 0.5 or not 5 <= b_ - a_ <= 8 or len(peaks) < 18:
                    notes.append(f"{x.id}: interval task should span 5-8 beats on 18+ beats with 0.5-box tolerance")
            slack = 0.25 if x.generator == "ecg_interval" else (4 if x.generator == "ecg_rate" else 1e-9)  # apex vs centre: up to 6 samples each end
            if want is None or abs(float(x.answer) - float(want)) > slack:
                wrong.append((x.id, x.answer, want))
        t.check("every stored answer equals the answer re-derived from the raw samples by separate code (within the apex-vs-centre slack)", not wrong, str(wrong[:3]))
        t.check("each strip obeys its rules (beats found again, none at the edge, steady rate strips, one early beat)",
                not notes, "; ".join(notes[:3]))
        with ui.quiet():
            vres = validate_tasks(cfg, ui, tasks, reg, strict=False)
        fails = [(i, n, r.message) for i, rs in vres.results.items() for n, r in rs if r.status == "fail"]
        t.check("validation: no check fails and every picture matches its data",
                not fails and vres.count_status("render_matches_data", "pass") == 20, str(fails[:2]))
        first = tasks[0]
        d = json.loads((work / first.data_path).read_text(encoding="utf-8"))
        bi = d["beats"][2]["i"]
        d["samples"][bi] = round(d["samples"][bi] - 1.0, 3)
        res = ec.verify_png(d, work / first.image_path)
        t.check("verify_png fails when a beat's peak in the data no longer matches the picture", res.status == "fail", res.message)
        blank = tmp / "ecg_grid_wrong.png"
        from PIL import Image
        Image.new("RGB", (ec.WIDTH, ec.HEIGHT), (255, 255, 255)).save(blank)
        t.check("verify_png fails on a picture without the grid", ec.verify_png(d, blank).status == "fail")
        t.check("the question texts never ask for a diagnosis (no 'rhythm is', 'diagnos', 'abnormal', 'disease')",
                not any(w in x.question.lower() for x in tasks for w in ("diagnos", "abnormal", "disease", "rhythm is", "attack")))
        t.check("the ECG tasks use a single visual type but several question types and answers",
                len({x.question_type for x in tasks}) == 5 and len({str(x.answer) for x in tasks}) >= 8)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _review_sheet_checks(ui: UI, t: _Checker, rt, tmp: Path) -> None:
    """The human review sheet: every task's picture, question and answer, answer hidden in the HTML."""
    from generate import review_sheet
    from generate.generate_tasks import generate_all
    from generate.generators.ecg_beat_count import EcgBeatCountGenerator
    from generate.generators.ecg_rate import EcgRateGenerator

    _section(ui, "N. human review sheet")
    work = Path(tempfile.mkdtemp(prefix="vtf_review_"))
    try:
        for dname in ("runner", "generate", "validate", "eval", "tasks", "results"):
            (work / dname).mkdir()
        (work / "tasks" / "tasks.jsonl").write_text(json.dumps({"_placeholder": True, "task_id": "PLACEHOLDER"}) + "\n", encoding="utf-8")
        (work / "results" / "run_config.json").write_text(json.dumps({"models": [], "temperature": 0}), encoding="utf-8")
        cfg = Config.load(work, work / "results" / "run_config.json", overrides={"seed": 5})
        t.check("no review sheet is written when there are no tasks", review_sheet.build(work) is None)
        reg = discover(extra=[EcgBeatCountGenerator, EcgRateGenerator], include_user=False)
        with contextlib.redirect_stdout(io.StringIO()):
            tasks, _ = generate_all(cfg, ui, reg, count=4)
        (work / "results" / "responses.csv").write_text(
            "task_id,model,extracted_answer,reference_answer,correct,status\n" + f"{tasks[0].id},Model A,999,{tasks[0].answer},false,ok\n"
            + f"{tasks[1].id},Stale Model,777,{tasks[1].answer}0001,false,ok\n", encoding="utf-8")
        made = review_sheet.build(work)
        t.check("tasks/review.html and tasks/REVIEW.md are written next to the tasks", made is not None and all(p.exists() for p in made))
        page = made[0].read_text(encoding="utf-8")
        text = made[1].read_text(encoding="utf-8")
        t.check("every task's id, full question and correct answer are in the sheet",
                all(x.id in page and x.id in text and x.question in text.replace("\n", " ") for x in tasks) and
                all(f"Correct answer:** {x.answer}" in text for x in tasks))
        t.check("every picture the sheet links to exists", all((work / "tasks" / "images" / f"{x.id}.png").exists() and f"images/{x.id}.png" in page for x in tasks))
        t.check("in the HTML the correct answer sits inside a closed <details> (hidden until clicked)",
                page.count("<details>") == len(tasks) and all(page.index("Correct answer") > page.index("<details>") for _ in tasks[:1]))
        t.check("model answers from results/responses.csv appear for the task they belong to",
                "Model A" in text and "999" in text and "wrong" in text)
        t.check("replies scored against a different reference answer (a stale run) are not shown", "Stale Model" not in text)
        t.check("the sheet says these are synthetic tasks, not clinical", "not clinical cases" in page and "not clinical cases" in text)
    finally:
        shutil.rmtree(work, ignore_errors=True)
