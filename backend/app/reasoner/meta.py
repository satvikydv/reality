"""MetaApiReasoner: muse-spark-1.3 (Standard mode) through the Responses API.

Never point this at `muse-spark-1.3-contributor`: that mode lets Meta train on our camera frames.
"""
from __future__ import annotations

import json
import os
import time

from app.reasoner.base import Result, parse_json_reply, with_retries

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


def to_responses_input(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        parts = []
        for p in c:
            if p["type"] == "text":
                parts.append({"type": "input_text", "text": p["text"]})
            elif p["type"] == "image":
                parts.append({"type": "input_image", "image_url": p["data_url"]})
            else:
                raise ValueError(f"unknown message part type {p['type']!r}")
        out.append({"role": m["role"], "content": parts})
    return out


class MetaApiReasoner:
    def __init__(self, client=None, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        if "contributor" in (model or DEFAULT_MODEL):
            raise ValueError("Contributor mode is not allowed: it trains on our content")
        if client is None:
            from openai import AsyncOpenAI

            key = api_key or os.getenv("MODEL_API_KEY", "").strip()
            if not key:
                raise RuntimeError("MODEL_API_KEY not set")
            client = AsyncOpenAI(api_key=key, base_url=base_url or os.getenv("META_API_BASE", DEFAULT_BASE_URL), max_retries=0, timeout=60)
        self.client = client
        self.model = model or os.getenv("SPARK_MODEL", DEFAULT_MODEL)

    async def respond(self, messages, *, tools=None, schema=None, effort="low", max_output_tokens=4000) -> Result:
        kwargs: dict = {}
        if schema is not None:
            kwargs["text"] = {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}}
        if tools:
            kwargs["tools"] = tools
        t0 = time.perf_counter()
        resp = await with_retries(lambda: self.client.responses.create(
            model=self.model, input=to_responses_input(messages), reasoning={"effort": effort},
            max_output_tokens=max_output_tokens, **kwargs))
        ms = (time.perf_counter() - t0) * 1000
        text = resp.output_text or ""
        calls = []
        for item in getattr(resp, "output", None) or []:
            if getattr(item, "type", None) == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except ValueError:
                    args = {}
                calls.append({"name": item.name, "arguments": args, "id": getattr(item, "call_id", None)})
        u = getattr(resp, "usage", None)
        return Result(text=text, parsed=parse_json_reply(text) if schema is not None else None, tool_calls=calls,
                      input_tokens=getattr(u, "input_tokens", 0) or 0, output_tokens=getattr(u, "output_tokens", 0) or 0,
                      latency_ms=ms, model=self.model, raw=resp)
