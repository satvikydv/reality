"""Provider-agnostic reasoning client.

Messages are plain dicts: {"role": "system"|"user"|"assistant", "content": str | [part, ...]} where a
part is {"type": "text", "text": ...} or {"type": "image", "data_url": "data:image/jpeg;base64,..."}.
Each implementation converts to its provider's wire format.
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


class ReasonerError(Exception):
    pass


@dataclass
class Result:
    text: str
    parsed: dict | None = None  # set when a schema was requested and the reply parsed as JSON
    tool_calls: list[dict] = field(default_factory=list)  # [{"name", "arguments": dict, "id"}]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""
    raw: Any = None


class Reasoner(Protocol):
    async def respond(self, messages: list[dict], *, tools: list[dict] | None = None, schema: dict | None = None,
                      effort: str = "low", max_output_tokens: int = 4000) -> Result: ...


def parse_json_reply(text: str) -> dict:
    try:
        v = json.loads(text)
    except ValueError as e:
        raise ReasonerError(f"reply is not valid JSON: {text[:200]!r}") from e
    if not isinstance(v, dict):
        raise ReasonerError(f"reply JSON is not an object: {text[:200]!r}")
    return v


def _retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    return type(exc).__name__ in ("APIConnectionError", "APITimeoutError") or isinstance(exc, (asyncio.TimeoutError, ConnectionError))


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        v = headers.get("retry-after") if headers is not None else None
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def with_retries(call: Callable[[], Awaitable[Any]], *, attempts: int = 4, base_delay: float = 0.5, max_delay: float = 8.0,
                       sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, rand: Callable[[], float] = random.random):
    """Run `call`, retrying 429, 5xx and connection errors with exponential backoff and jitter,
    honouring Retry-After. Other errors propagate immediately."""
    for attempt in range(attempts):
        try:
            return await call()
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts - 1 or not _retryable(exc):
                raise
            delay = _retry_after(exc)
            if delay is None:
                delay = min(max_delay, base_delay * 2 ** attempt) * (0.5 + rand() / 2)
            await sleep(min(delay, 60.0))
