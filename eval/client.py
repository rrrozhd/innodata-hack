"""Model clients.

OpenRouterClient : the real client (standard library only; no extra packages).
MockClient       : offline stand-in used by `python run.py selftest` and by --dry-run style demos.

Both return a `Completion`. A completion is only usable when status == "ok". The rules do not
accept API errors or token cutoffs, so every other status is reported loudly and re-run.

status values:
    ok         normal reply with text
    truncated  hit max_tokens (finish_reason 'length')  -> raise max_tokens in results/run_config.json
    empty      reply had no text
    filtered   provider stopped the reply for another reason (e.g. content_filter)
    api_error  HTTP/network/provider error after retries
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Completion:
    status: str
    text: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    latency_s: float | None = None
    attempts: int = 1
    error: str = ""
    raw: dict = field(default_factory=dict)


class OpenRouterClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 300, max_retries: int = 3,
                 backoff: float = 5, sleep=time.sleep):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self._sleep = sleep

    def complete(self, model, messages: list[dict], params: dict, meta: dict | None = None) -> Completion:
        body: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "temperature": params["temperature"],
            "max_tokens": params["max_tokens"],
            "usage": {"include": True},
        }
        if params.get("reasoning_effort"):
            body["reasoning"] = {"effort": params["reasoning_effort"]}
        body.update(model.extra_body)  # per-model overrides from results/run_config.json (models[].extra_body)
        payload = json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

        started = time.time()
        last_error = ""
        attempts = 0
        while attempts <= self.max_retries:
            attempts += 1
            retry_after = None
            try:
                req = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                comp = self._parse(data)
                comp.latency_s = time.time() - started
                comp.attempts = attempts
                if comp.status == "api_error" and attempts <= self.max_retries and "retry" in comp.error.lower():
                    last_error = comp.error
                else:
                    return comp
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code in (408, 425, 429) or exc.code >= 500:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                else:
                    return Completion("api_error", error=last_error, attempts=attempts,
                                      latency_s=time.time() - started)
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                    http.client.HTTPException, ssl.SSLError) as exc:
                last_error = f"network error: {exc}"
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    return Completion("api_error", attempts=attempts, latency_s=time.time() - started,
                                      error=last_error + ". TLS certificate check failed: on a macOS python.org "
                                      "install, run 'Install Certificates.command' (in /Applications/Python 3.x/) "
                                      "once, then try again")
            except json.JSONDecodeError as exc:
                last_error = f"response was not JSON: {exc}"
            if attempts <= self.max_retries:
                try:
                    wait = float(retry_after) if retry_after else self.backoff * attempts
                except ValueError:
                    wait = self.backoff * attempts
                self._sleep(wait)
        return Completion("api_error", error=last_error or "unknown error", attempts=attempts,
                          latency_s=time.time() - started)

    @staticmethod
    def _parse(data: dict) -> Completion:
        choices = data.get("choices") or []
        if not choices:
            err = data.get("error")
            msg = json.dumps(err)[:300] if err else "response had no choices"
            code = err.get("code") if isinstance(err, dict) else None
            retryable = isinstance(code, int) and (code in (408, 429) or code >= 500)
            return Completion("api_error", error=msg + (" (will retry)" if retryable else ""), raw=data)
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = content or ""
        finish = choice.get("finish_reason")
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        comp = Completion(
            status="ok", text=text, finish_reason=finish,
            prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
            reasoning_tokens=details.get("reasoning_tokens"),
            cost_usd=usage.get("cost"), raw=data,
        )
        if finish in ("length", "max_tokens"):
            comp.status = "truncated"
            comp.error = "reply was cut off at max_tokens; raise max_tokens in results/run_config.json"
        elif finish in ("content_filter", "error", "safety"):
            comp.status = "filtered"
            comp.error = f"provider stopped the reply (finish_reason={finish})"
        elif not text.strip():
            comp.status = "empty"
            comp.error = "reply contained no text"
        return comp


class MockClient:
    """Offline client for the self-test. Never touches the network.

    behaviors maps model label -> a function(task_id, expected, call_number) -> reply text, or a
    Completion to return directly.
    """

    def __init__(self, behaviors: dict):
        self.behaviors = behaviors
        self.calls: list[tuple[str, str]] = []
        self.counts: dict[tuple[str, str], int] = {}

    def complete(self, model, messages: list[dict], params: dict, meta: dict | None = None) -> Completion:
        meta = meta or {}
        task_id = meta.get("task_id", "?")
        key = (model.label, task_id)
        self.counts[key] = self.counts.get(key, 0) + 1
        self.calls.append(key)
        out = self.behaviors[model.label](task_id, meta.get("expected"), self.counts[key], meta)
        if isinstance(out, Completion):
            return out
        return Completion("ok", text=out, finish_reason="stop", prompt_tokens=400, completion_tokens=120,
                          reasoning_tokens=60, cost_usd=0.001, latency_s=0.01)
