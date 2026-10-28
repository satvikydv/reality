"""What the phone's Home screen shows: is the server up, does the model API accept the key, what was the last scan.

The key check is one cheap authenticated read (list models, 5 s limit) and its answer is cached, so polling the status every
few seconds costs nothing. It never sends a frame or a prompt.
"""
from __future__ import annotations

import asyncio
import os
import time

from app.coverage import coverage

CACHE_S = 300.0       # a verdict that came from the API stays valid this long
RETRY_S = 20.0        # a failed attempt (network trouble) is retried sooner
TIMEOUT_S = 5.0
WAIT_S = 1.5          # how long a status request waits for the key check before answering "checking"

MESSAGES = {
    "ok": "The model API accepted the key.",
    "set": "A key is set. The model API has no cheap check, so it was not verified.",
    "missing": "MODEL_API_KEY is not set on the server. Scans and Find cannot work until it is.",
    "invalid": "The model API rejected the key. Check MODEL_API_KEY on the server.",
    "unreachable": "The server could not reach the model API.",
    "checking": "Checking the key with the model API…",
}


def _classify(exc: BaseException) -> str:
    import openai

    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "invalid"
    if isinstance(exc, openai.APIStatusError) and exc.status_code in (404, 405, 501):
        return "set"  # reached the API and it did not refuse the key; it just has no such endpoint
    return "unreachable"


class KeyCheck:
    def __init__(self, make_client=None, clock=time.monotonic, timeout: float = TIMEOUT_S):
        self._make_client, self._clock, self.timeout = make_client, clock, timeout
        self._cached: dict | None = None
        self._until = 0.0
        self._task: asyncio.Future | None = None

    def _client(self):
        if self._make_client is not None:
            return self._make_client()
        from app.sam_client import make_client

        return make_client()

    async def _refresh(self) -> dict:
        try:
            client = self._client()
            await asyncio.wait_for(client.with_options(timeout=self.timeout).models.list(), self.timeout + 1)
            state = "ok"
        except Exception as e:  # noqa: BLE001 - every failure becomes a state the phone can show
            state = _classify(e)
        self._cached = {"state": state, "message": MESSAGES[state]}
        self._until = self._clock() + (RETRY_S if state == "unreachable" else CACHE_S)
        return self._cached

    async def state(self, force: bool = False, wait: float | None = None) -> dict:
        """The key's state. With `wait`, a check that is still running after that many seconds answers "checking" and
        carries on in the background, so the Home screen never waits on the model API; the next poll gets the verdict."""
        if not os.getenv("MODEL_API_KEY", "").strip():
            return {"state": "missing", "message": MESSAGES["missing"]}
        if not force and self._cached is not None and self._clock() < self._until:
            return self._cached
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._refresh())
        if wait is None:
            return await self._task
        try:
            return await asyncio.wait_for(asyncio.shield(self._task), wait)
        except asyncio.TimeoutError:
            return {"state": "checking", "message": MESSAGES["checking"]}


def last_scan(svc) -> tuple[dict | None, int]:
    """(summary of the newest scan that stored any frames, how many such scans exist)."""
    scans = [s for s in svc.wm.list_scans() if s["n_frames"] > 0]
    if not scans:
        return None, 0
    s = scans[0]
    cov = coverage(svc.wm.list_frames(s["id"]))
    return {"id": s["id"], "started_at": s["started_at"], "ended_at": s["ended_at"], "n_objects": s["n_objects"], "n_frames": s["n_frames"],
            "coverage_pct": round(cov["fraction"] * 100), "covered_deg": cov["covered_deg"]}, len(scans)


async def build(get_service, key_check: KeyCheck, force: bool = False, wait: float | None = WAIT_S) -> dict:
    out = {"server": True, "ts": time.time(), "api": await key_check.state(force, wait), "last_scan": None, "scans": 0, "service_error": None}
    try:
        out["last_scan"], out["scans"] = last_scan(get_service())
    except Exception as e:  # noqa: BLE001 - usually the missing key; the api field already says so
        out["service_error"] = f"{type(e).__name__}: {e}"[:200]
    return out
