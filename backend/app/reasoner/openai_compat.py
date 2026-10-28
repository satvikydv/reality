"""OpenAICompatReasoner: any OpenAI-compatible Chat Completions endpoint (Modal-served Muse Glimmer via
vLLM, Baseten-served Muse Spark Open). Selected with REASONER_BACKEND=glimmer_modal|spark_open_baseten.

Not exercised against a live endpoint yet [VERIFY]: image input in the served setup, the model id the
server exposes, and whether it accepts strict json_schema response_format.
"""
from __future__ import annotations

import json
import os
import time

from app.reasoner.base import Result, parse_json_reply, with_retries


def to_chat_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        parts = []
        for p in c:
            if p["type"] == "text":
                parts.append({"type": "text", "text": p["text"]})
            elif p["type"] == "image":
                parts.append({"type": "image_url", "image_url": {"url": p["data_url"]}})
            else:
                raise ValueError(f"unknown message part type {p['type']!r}")
        out.append({"role": m["role"], "content": parts})
    return out


class OpenAICompatReasoner:
    def __init__(self, client=None, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        if client is None:
            from openai import AsyncOpenAI

            base_url = base_url or os.getenv("OPENAI_COMPAT_BASE_URL", "").strip()
            if not base_url:
                raise RuntimeError("OPENAI_COMPAT_BASE_URL not set")
            client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_COMPAT_API_KEY", "") or "unused", base_url=base_url, max_retries=0, timeout=120)
        self.client = client
        self.model = model or os.getenv("OPENAI_COMPAT_MODEL", "").strip()
        if not self.model:
            raise RuntimeError("OPENAI_COMPAT_MODEL not set")

    async def respond(self, messages, *, tools=None, schema=None, effort="low", max_output_tokens=4000) -> Result:
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {k: v for k, v in t.items() if k != "type"}} for t in tools]
        t0 = time.perf_counter()
        resp = await with_retries(lambda: self.client.chat.completions.create(
            model=self.model, messages=to_chat_messages(messages), max_tokens=max_output_tokens, **kwargs))
        ms = (time.perf_counter() - t0) * 1000
        msg = resp.choices[0].message
        text = msg.content or ""
        calls = []
        for tc in getattr(msg, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            calls.append({"name": tc.function.name, "arguments": args, "id": tc.id})
        u = getattr(resp, "usage", None)
        return Result(text=text, parsed=parse_json_reply(text) if schema is not None else None, tool_calls=calls,
                      input_tokens=getattr(u, "prompt_tokens", 0) or 0, output_tokens=getattr(u, "completion_tokens", 0) or 0,
                      latency_ms=ms, model=self.model, raw=resp)
