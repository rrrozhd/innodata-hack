"""The prompt. A task's `question` field already holds the complete text the model
is sent (conventions, tolerance and answer format), so the template is the
question itself plus the image, with no system prompt."""
from __future__ import annotations

import base64

PROMPT_TEMPLATE = "{question}"
SYSTEM_PROMPT = None


def build_messages(question: str, png: bytes | None) -> list:
    content = [{"type": "text", "text": PROMPT_TEMPLATE.format(question=question)}]
    if png is not None:
        b64 = base64.b64encode(png).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return [{"role": "user", "content": content}]
