"""Model backends.

- OpenRouter (or any OpenAI-compatible endpoint given with base_url): one chat
  completion per task, image attached as base64, with retries on 429, 5xx and
  timeouts.
- mock:* backends: offline stand-ins used to test the pipeline. They never
  appear in a reported evaluation.

The API key is read from an environment variable only when a real call is made.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field

import requests

from factory.core.seed import rng_for

from .prompt import build_messages

OPENROUTER_URL = "https://openrouter.ai/api/v1"
RETRY_STATUS = {408, 409, 425, 429}
# USD per million tokens (input, output), from OpenRouter's public model list on 2026-11-14.
PRICES = {"anthropic/claude-haiku-5.5": (0.10, 0.50), "openai/gpt-6-luna": (0.10, 0.50)}


@dataclass
class Settings:
    temperature: float = 0
    max_tokens: int = 64000
    reasoning_effort: str = "high"
    timeout_s: int = 900
    max_attempts: int = 6
    base_url: str = OPENROUTER_URL
    key_env: str = "OPENROUTER_API_KEY"


@dataclass
class Reply:
    content: str = ""
    finish_reason: str = ""
    status: str = "ok"            # ok | api_error
    error_detail: str = ""
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0
    attempts: int = 1
    retries: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def is_mock(model: str) -> bool:
    """Backends that never count as a reported evaluation: offline mocks and imported replies."""
    return model.startswith(("mock:", "replies:"))


def _imported(model: str, record: dict) -> Reply:
    """replies:<label>=<file.json>: score replies collected elsewhere (a person, a chat window, a subagent).
    The file maps task_id to the reply text. Informal by definition; never a reported evaluation."""
    import json
    path = model.split("=", 1)[1]
    with open(path, encoding="utf-8") as fh:
        replies = json.load(fh)
    text = replies.get(record["task_id"], "")
    return Reply(content=text or "(no reply collected)", finish_reason="stop", raw={"imported_from": path})


def _fmt(answer) -> str:
    return ", ".join(str(a) for a in answer) if isinstance(answer, list) else str(answer)


def _mock(model: str, record: dict, data: dict) -> Reply:
    kind = model.split(":", 1)[1]
    if kind == "oracle":
        text = f"Reading the capture step by step.\nFINAL ANSWER: {_fmt(record['answer'])}"
    elif kind == "silent":
        text = "I cannot determine this from the image."
    elif kind.startswith("shortcut:"):
        want = kind.split(":", 1)[1]
        hit = next((f for f in data["scene"]["fingerprints"] if f["id"] == want and f["armed"]), None)
        text = (f"Taking the quick route ({want}).\nFINAL ANSWER: {_fmt(hit['answer'])}" if hit
                else "That shortcut does not apply here.")
    else:  # blind: a guess that never looks at the image
        rng = rng_for("mock-blind", record["task_id"])
        fmt = record.get("answer_format")
        if fmt == "hex_byte":
            guess = f"0x{int(rng.integers(256)):02X}"
        elif fmt in ("int", "number"):
            guess = str(int(rng.integers(0, 50)))
        else:
            guess = "unknown"
        text = f"Without reading the image, a guess.\nFINAL ANSWER: {guess}"
    return Reply(content=text, finish_reason="stop", usage={"prompt_tokens": 0, "completion_tokens": 0},
                 raw={"mock": model})


def _content_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def _call(model: str, question: str, png, settings: Settings) -> Reply:
    key = os.environ.get(settings.key_env, "")
    if not key and settings.base_url.rstrip("/") == OPENROUTER_URL:
        raise SystemExit(f"{settings.key_env} is not set; pass it with -e {settings.key_env} or use a mock model")
    body = {"model": model, "messages": build_messages(question, png), "temperature": settings.temperature,
            "max_tokens": settings.max_tokens, "reasoning": {"effort": settings.reasoning_effort}}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    reply, started = Reply(), time.time()
    for attempt in range(1, settings.max_attempts + 1):
        reply.attempts = attempt
        wait, why = None, ""
        try:
            resp = requests.post(settings.base_url.rstrip("/") + "/chat/completions", json=body, headers=headers,
                                 timeout=(20, settings.timeout_s))
            payload = None
            try:
                payload = resp.json()
            except ValueError:
                pass
            err = payload.get("error") if isinstance(payload, dict) else None
            code = resp.status_code
            if err and isinstance(err, dict) and isinstance(err.get("code"), int):
                code = err["code"] if resp.status_code == 200 else resp.status_code
            if code in RETRY_STATUS or code >= 500:
                why = f"HTTP {code}"
                wait = float(resp.headers.get("Retry-After", 0) or 0) or None
            elif code != 200 or err:
                reply.status, reply.error_detail = "api_error", f"HTTP {code}: {str(err or resp.text)[:300]}"
                reply.raw = payload if isinstance(payload, dict) else {"text": resp.text[:2000]}
                break
            elif not isinstance(payload, dict) or not payload.get("choices"):
                why = "no choices in the response"
            else:
                choice = payload["choices"][0]
                text = _content_text(choice.get("message") or {})
                reply.raw, reply.usage = payload, payload.get("usage") or {}
                reply.finish_reason = choice.get("finish_reason") or ""
                if text.strip() or reply.finish_reason == "length":
                    reply.content, reply.status, reply.error_detail = text, "ok", ""
                    break
                why = "empty reply"
        except (requests.Timeout, requests.ConnectionError) as e:
            why = type(e).__name__
        reply.retries.append({"attempt": attempt, "reason": why})
        reply.status, reply.error_detail = "api_error", f"gave up after {attempt} attempts: {why}"
        if attempt < settings.max_attempts:
            time.sleep(wait if wait is not None else min(60.0, 2.0 ** attempt) + random.random())
    reply.latency_s = round(time.time() - started, 2)
    return reply


def complete(model: str, record: dict, data: dict, png, settings: Settings) -> Reply:
    """One reply for one task. png=None sends the question without the image."""
    if model.startswith("replies:"):
        return _imported(model, record)
    if is_mock(model):
        return _mock(model, record, data)
    return _call(model, record["question"], png, settings)


def cost_usd(model: str, usage: dict):
    if isinstance(usage.get("cost"), (int, float)):
        return round(float(usage["cost"]), 6)
    if model not in PRICES:
        return None
    p_in, p_out = PRICES[model]
    return round((usage.get("prompt_tokens", 0) * p_in + usage.get("completion_tokens", 0) * p_out) / 1e6, 6)
