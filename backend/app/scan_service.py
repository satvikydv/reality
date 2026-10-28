"""Live scan sessions over the WebSocket (F10): queue keyframes, run the scan pipeline, report progress.

One `ScanSession` per socket. The phone sends keyframes (JPEG + orientation) as it pans; they wait in a
bounded queue and a few workers run `ScanPipeline.process` concurrently (each frame is ~7 s of mostly
network wait, so several in flight turn a 30 s pan into a finished scan soon after the pan ends).
Everything the phone needs comes back as JSON events:

  scan_started   {scan_id}
  frame_queued   {seq, queued}            frame_rejected {seq, reason}
  frame_done     {seq, stored, names, kept, objects, ms, usd, errors}      frame_error {seq, error}
  scan_summary   {objects, speech, coverage, frames, cost_usd}
  find_result    {query, concept, found, speech, object, replay}
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from app.events import EventBus
from app.resolver import semantic_find
from app.scan import ScanPipeline, pose_from_meta
from app.world_model import WorldModel, plural

log = logging.getLogger("uvicorn.error")
Send = Callable[[dict], Awaitable[None]]


def yaw_coverage(frames: list[dict], reach: float = 0.4) -> dict:
    """Which compass directions the stored frames looked at. Each frame covers yaw +- reach * its horizontal
    FOV (a conservative 80% of the image). Returns covered_deg and gaps [[start, end], ...] in degrees."""
    cov = [False] * 360
    for f in frames:
        half = reach * f["hfov_deg"]
        for d in range(int(-half), int(half) + 1):
            cov[int(round(f["yaw"] + d)) % 360] = True
    gaps, i = [], 0
    while i < 360:
        if cov[i]:
            i += 1
            continue
        j = i
        while j < 360 and not cov[j]:
            j += 1
        gaps.append([i, j - 1])
        i = j
    if len(gaps) > 1 and gaps[0][0] == 0 and gaps[-1][1] == 359:  # one gap across north
        gaps[0] = [gaps[-1][0], gaps[0][1]]
        gaps.pop()
    return {"covered_deg": sum(cov), "gaps": gaps}


def count_phrase(labels: list[str]) -> str:
    """["mug", "mug", "laptop"] -> "2 mugs and a laptop"."""
    parts = []
    for label, n in Counter(labels).most_common():
        already_plural = label.endswith("s") and not label.endswith("ss")
        parts.append(f"{n} {plural(label)}" if n > 1 else label if already_plural else f"a {label}")
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else ""


@dataclass
class ScanService:
    wm: WorldModel
    pipeline: ScanPipeline
    reasoner: object
    workers: int = 3
    max_queue: int = 32
    drain_timeout_s: float = 120.0
    bus: EventBus = field(default_factory=EventBus)

    async def find(self, query: str, source: str = "phone") -> dict:
        r = await semantic_find(self.reasoner, self.wm, query)
        found = r["found"]
        replay = None
        if found and found.get("replay"):
            rp = found["replay"]
            replay = {"frame_id": rp["frame_id"], "box": rp["box"], "width": rp["width"], "height": rp["height"]}
        concept = r["resolution"]["concept"]
        speech = found["speech"] if found else ("I have not seen that yet." if concept else "I did not understand what to look for.")
        obj = {k: found["object"][k] for k in ("id", "label", "yaw", "pitch", "n_obs", "conf_best")} if found else None
        if obj:
            obj["age_s"] = round(max(0.0, time.time() - found["object"]["last_seen"]))
        result = {"type": "find_result", "query": query, "concept": concept, "found": found is not None, "speech": speech,
                  "object": obj, "from_scan_start": found["from_scan_start"] if found else None, "replay": replay,
                  "needs_live_look": r["needs_live_look"], "skipped_llm": r["skipped_llm"]}
        self.bus.publish({**result, "source": source})
        return result

    def session(self, send: Send) -> "ScanSession":
        return ScanSession(self, send)


class ScanSession:
    def __init__(self, svc: ScanService, send: Send):
        self.svc, self._send, self._lock = svc, send, asyncio.Lock()
        self.scan_id: int | None = None
        self.queue: asyncio.Queue = asyncio.Queue(svc.max_queue)
        self.tasks: list[asyncio.Task] = []
        self.stats = Counter()
        self.usd = 0.0
        self.t0 = 0.0

    async def emit(self, obj: dict) -> None:
        self.svc.bus.publish({**obj, "source": "phone"})  # the dashboard sees everything the phone sees
        async with self._lock:
            try:
                await self._send(obj)
            except Exception:  # noqa: BLE001 - socket gone; the scan still finishes and is stored
                pass

    @property
    def active(self) -> bool:
        return self.scan_id is not None

    async def start(self, name: str | None = None) -> None:
        if self.active:
            await self.emit({"type": "error", "where": "scan_start", "message": "a scan is already running"})
            return
        self.scan_id = self.svc.wm.start_scan(name=name, source="live", ts=time.time())
        self.stats, self.usd, self.t0 = Counter(), 0.0, time.perf_counter()
        self.tasks = [asyncio.create_task(self._worker()) for _ in range(self.svc.workers)]
        log.info("scan %s started", self.scan_id)
        await self.emit({"type": "scan_started", "scan_id": self.scan_id})

    async def submit(self, seq: int, jpeg: bytes, meta: dict) -> None:
        """Queue one keyframe. Always answers with frame_queued or frame_rejected."""
        if not self.active:
            return await self.emit({"type": "frame_rejected", "seq": seq, "reason": "no scan running"})
        pose = pose_from_meta(meta)
        if pose is None:
            self.stats["rejected"] += 1
            return await self.emit({"type": "frame_rejected", "seq": seq, "reason": "no orientation with the frame"})
        try:
            self.queue.put_nowait((seq, jpeg, pose, time.time(), time.perf_counter()))
        except asyncio.QueueFull:
            self.stats["rejected"] += 1
            return await self.emit({"type": "frame_rejected", "seq": seq, "reason": "queue full, slow down"})
        self.stats["received"] += 1
        await self.emit({"type": "frame_queued", "seq": seq, "queued": self.queue.qsize()})

    async def _worker(self) -> None:
        while True:
            seq, jpeg, pose, ts, t_enq = await self.queue.get()
            try:
                t = time.perf_counter()
                rep = await self.svc.pipeline.process(self.scan_id, jpeg, ts, pose)
                self.usd += rep.usd
                self.stats["stored" if rep.stored else "failed"] += 1
                if rep.stored:
                    self.svc.wm.consolidate(self.scan_id)
                n_obj =len(self.svc.wm.list_objects(self.scan_id))
                log.info("scan %s frame %s: %s names=%s kept=%d objects=%d wait=%.1fs work=%.1fs $%.4f%s", self.scan_id, seq,
                         "stored" if rep.stored else "NOT STORED", rep.names, len(rep.detections), n_obj, t - t_enq,
                         time.perf_counter() - t, rep.usd, f" errors={rep.errors}" if rep.errors else "")
                self.svc.bus.publish({"type": "world_update", "scan_id": self.scan_id,
                                      "coverage_deg": yaw_coverage(self.svc.wm.list_frames(self.scan_id))["covered_deg"], "objects": [
                    {"id": o["id"], "label": o["label"], "yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1), "n_obs": o["n_obs"]}
                    for o in self.svc.wm.list_objects(self.scan_id)]})
                await self.emit({"type": "frame_done", "seq": seq, "stored": rep.stored, "names": rep.names, "kept": len(rep.detections),
                                 "objects": n_obj, "spark_ms": round(rep.spark_ms), "sam_ms": round(rep.sam_ms), "usd": round(rep.usd, 4),
                                 "errors": rep.errors})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one bad frame must not stop the scan
                self.stats["failed"] += 1
                log.warning("scan %s frame %s failed: %s: %s", self.scan_id, seq, type(e).__name__, e)
                await self.emit({"type": "frame_error", "seq": seq, "error": f"{type(e).__name__}: {e}"[:300]})
            finally:
                self.queue.task_done()

    async def end(self) -> dict | None:
        """Wait for queued frames, close the scan and send the summary."""
        if not self.active:
            return None
        timed_out = False
        try:
            await asyncio.wait_for(self.queue.join(), self.svc.drain_timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
        await self._stop_workers()
        wm, sid = self.svc.wm, self.scan_id
        wm.end_scan(sid, ts=time.time())
        wm.consolidate(sid)
        objs = wm.list_objects(sid)
        cov =yaw_coverage(wm.list_frames(sid))
        speech = f"I found {count_phrase([o['label'] for o in objs])}." if objs else "I did not find any objects."
        if cov["gaps"] and cov["covered_deg"] < 340:
            speech += f" About {360 - cov['covered_deg']} degrees of the room were not covered."
        summary = {"type": "scan_summary", "scan_id": sid, "speech": speech, "coverage": cov, "cost_usd": round(self.usd, 4),
                   "seconds": round(time.perf_counter() - self.t0, 1), "timed_out": timed_out,
                   "frames": {k: self.stats[k] for k in ("received", "stored", "failed", "rejected")},
                   "objects": [{"id": o["id"], "label": o["label"], "yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1),
                                "n_obs": o["n_obs"], "conf_best": round(o["conf_best"], 2)} for o in objs]}
        self.scan_id = None
        log.info("scan %s ended: %d objects, frames %s, coverage %d deg, $%.3f", sid, len(objs), dict(self.stats), cov["covered_deg"], self.usd)
        await self.emit(summary)
        return summary

    async def _stop_workers(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    async def close(self) -> None:
        """Socket gone: stop workers and close the scan with whatever was stored."""
        if self.active:
            await self._stop_workers()
            self.svc.wm.end_scan(self.scan_id, ts=time.time())
            log.info("scan %s closed by disconnect (frames %s)", self.scan_id, dict(self.stats))
            self.scan_id = None


def build_default_service() -> ScanService:
    """Real clients from the environment. Raises RuntimeError when MODEL_API_KEY is missing."""
    from app.reasoner import make_reasoner
    from app.sam_client import make_client, make_segmenter

    root = Path(__file__).resolve().parents[2] / "data"
    wm = WorldModel(os.getenv("WORLD_DB") or root / "world.sqlite", frames_dir=root / "world_frames")
    reasoner = make_reasoner()
    pipeline = ScanPipeline(wm, reasoner, make_segmenter(make_client()))
    return ScanService(wm, pipeline, reasoner)
