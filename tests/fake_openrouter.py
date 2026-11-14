"""A local stand-in for the OpenRouter chat-completions endpoint.

It lets the client's retry logic and the completeness gate be tested without an
API key or any network call. The model name selects the behaviour:

    fake/ok       a complete reply
    fake/flaky    429, then 500, then a complete reply
    fake/length   a reply cut off by the token limit
    fake/down     always 503
    fake/empty    a "complete" reply with no text
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOpenRouter:
    def __init__(self, answer: str = "0x00"):
        self.answer = answer
        self.calls = {}
        self.bodies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                outer.bodies.append(body)
                model = body["model"]
                n = outer.calls[model] = outer.calls.get(model, 0) + 1

                def ok(text, finish="stop"):
                    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
                            "usage": {"prompt_tokens": 1200, "completion_tokens": 300}}

                if model == "fake/flaky" and n == 1:
                    return self._send(429, {"error": {"code": 429, "message": "rate limited"}})
                if model == "fake/flaky" and n == 2:
                    return self._send(500, {"error": {"code": 500, "message": "upstream error"}})
                if model == "fake/down":
                    return self._send(503, {"error": {"code": 503, "message": "unavailable"}})
                if model == "fake/length":
                    return self._send(200, ok("Sampling the first clock edge, then", "length"))
                if model == "fake/empty":
                    return self._send(200, ok(""))
                return self._send(200, ok(f"Reading the capture.\nFINAL ANSWER: {outer.answer}"))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
