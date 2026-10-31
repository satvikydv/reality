"""Live scan sessions over the WebSocket (F10): queue keyframes, run the scan pipeline, report progress.

One `ScanSession` per socket. The phone sends keyframes (JPEG + orientation) as it pans; they wait in a
bounded queue and a few workers run `ScanPipeline.process` concurrently (each frame is ~7 s of mostly
network wait, so several in flight turn a 30 s pan into a finished scan soon after the pan ends).
Everything the phone needs comes back as JSON events:

  scan_started   {scan_id}
  frame_queued   {seq, queued}            frame_rejected {seq, reason}
  frame_done     {seq, stored, names, kept, objects, ms, usd, errors}      frame_error {seq, error}
  scan_summary   {objects, speech, coverage, frames, cost_usd, scan_quality}
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

from app import anchoring as A
from app import scan_quality
from app.coverage import coverage
from app.events import EventBus
from app.resolver import semantic_find, semantic_find_rooms
from app.scan import ScanPipeline, pose_from_meta
from app.world_model import WorldModel, plural

log = logging.getLogger("uvicorn.error")
Send = Callable[[dict], Awaitable[None]]


def yaw_coverage(frames: list[dict], reach: float = 0.4) -> dict:
    """Which compass directions the stored frames looked at: covered_deg and gaps [[start, end], ...] in degrees.
    The definition lives in app/coverage.py, shared with the phone."""
    c = coverage(frames, reach)
    return {"covered_deg": c["covered_deg"], "gaps": c["gaps"]}


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

    def _group(self, room: dict, scan_id: int | None, concept: str, found: dict | None) -> dict:
        """One room's answer: the same fields whether it is the only room asked or one of several. Everything in it comes from
        that room's own memory (its latest saved scan)."""
        replay = None
        if found and found.get("replay"):
            rp = found["replay"]
            replay = {"frame_id": rp["frame_id"], "box": rp["box"], "width": rp["width"], "height": rp["height"]}
        obj = None
        if found:
            o = found["object"]
            obj = {k: o[k] for k in ("id", "label", "yaw", "pitch", "n_obs", "conf_best")}
            obj["age_s"] = round(max(0.0, time.time() - o["last_seen"]))
            obj["uncertainty_deg"] = round(o.get("uncertainty_deg") or o["spread_deg"], 1)  # drawn as the cone around the arrow
            obj["fov_error_deg"] = round(o.get("fov_err_deg") or 0.0, 1)
        if found:
            speech = found["speech"]
        elif scan_id is None:
            speech = f"{room['name']} has not been scanned yet."
        else:
            speech = "I have not seen that yet." if concept else "I did not understand what to look for."
        out = {"room_id": room["id"], "room_name": room["name"], "active": room["active"], "found": found is not None, "speech": speech,
               "object": obj, "from_scan_start": found["from_scan_start"] if found else None, "replay": replay}
        if not found:  # lets the phone say what "not found" means: not scanned, or how much of this room the scan covered
            saved = [s for s in self.wm.room_scans(room["id"]) if s["status"] == "saved"]
            out["checked"] = {"scans": len(saved), "covered_deg": coverage(self.wm.list_frames(scan_id))["covered_deg"] if scan_id else 0, "scan_id": scan_id}
        return out

    async def find(self, query: str, source: str = "phone", room_id: int | None = None, all_rooms: bool = False) -> dict:
        """Find in one room (`room_id`, else the active room), or with `all_rooms` in every room, read-only, grouped per room."""
        if all_rooms:
            return await self._find_everywhere(query, source)
        room = self.wm.get_room(room_id) if room_id is not None else self.wm.active_room()
        if room is None:
            names = [r["name"] for r in self.wm.list_rooms()]
            result = {"type": "find_result", "query": query, "concept": "", "found": False, "no_room": True, "room_id": None, "room_name": None,
                      "speech": "Choose a room first, then ask again." if names else "There are no rooms yet. Scan a room first.",
                      "object": None, "from_scan_start": None, "replay": None, "needs_live_look": False, "skipped_llm": True, "rooms": names}
        else:
            sid = self.wm.latest_scan_id(room["id"])  # a room never reads another room's scans: no memory here means nothing found
            if sid is None:
                r = {"resolution": {"concept": ""}, "found": None, "needs_live_look": False, "skipped_llm": True}
            else:
                r = await semantic_find(self.reasoner, self.wm, query, sid)
            concept = r["resolution"]["concept"]
            result = {"type": "find_result", "query": query, "concept": concept, **self._group(room, sid, concept, r["found"]),
                      "needs_live_look": r["needs_live_look"], "skipped_llm": r["skipped_llm"]}
        self.bus.publish({**result, "source": source})
        return result

    async def _find_everywhere(self, query: str, source: str) -> dict:
        rooms = self.wm.list_rooms()
        scans = {r["id"]: r["scan_id"] for r in rooms if r["scan_id"]}
        concept, found, skipped = "", {}, True
        if scans:
            r = await semantic_find_rooms(self.reasoner, self.wm, query, scans)
            concept, found, skipped = r["resolution"]["concept"], r["found"], r["skipped_llm"]
        groups = [self._group(self.wm.get_room(r["id"]), scans.get(r["id"]), concept, found.get(r["id"])) for r in rooms]
        groups.sort(key=lambda g: (not g["found"], not g["active"], g["room_name"].casefold()))
        n = sum(g["found"] for g in groups)
        speech = (f"I found it in {n} room{'s' if n != 1 else ''}." if n else
                  "No room has been scanned yet." if not scans else "I did not see that in any room." if concept else "I did not understand what to look for.")
        result = {"type": "find_result", "all_rooms": True, "query": query, "concept": concept, "found": n > 0, "speech": speech, "groups": groups,
                  "skipped_llm": skipped}
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
        self.clock_offset: float | None = None
        self.room: dict | None = None

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

    async def start(self, name: str | None = None, room_id: int | None = None) -> None:
        """Start a scan of a room (`room_id`, else the active room). It is a draft until the user saves it."""
        if self.active:
            await self.emit({"type": "error", "where": "scan_start", "message": "a scan is already running"})
            return
        wm = self.svc.wm
        room = wm.get_room(room_id) if room_id is not None else wm.active_room()
        if room is None:
            await self.emit({"type": "error", "where": "scan_start", "message": "no such room" if room_id is not None else "choose a room to scan first"})
            return
        self.room = room
        self.scan_id = wm.start_scan(name=name, source="live", ts=time.time(), room_id=room["id"], status="draft")
        self.stats, self.usd, self.t0 = Counter(), 0.0, time.perf_counter()
        self.clock_offset = None
        self.tasks = [asyncio.create_task(self._worker()) for _ in range(self.svc.workers)]
        log.info("scan %s started", self.scan_id)
        await self.emit({"type": "scan_started", "scan_id": self.scan_id, "room_id": room["id"], "room_name": room["name"]})

    def _capture_time(self, pose: dict) -> float:
        """When the frame was taken, on the server's clock: the phone's capture time shifted by this scan's clock offset
        (fixed by its first frame), so frames sort by when they were grabbed, not by when they finished uploading."""
        cap = pose.get("captured_at")
        if cap is None:
            return time.time()
        if self.clock_offset is None:
            self.clock_offset = time.time() - cap
        return cap + self.clock_offset

    async def submit(self, seq: int, jpeg: bytes, meta: dict) -> None:
        """Queue one keyframe. Always answers with frame_queued or frame_rejected."""
        if not self.active:
            return await self.emit({"type": "frame_rejected", "seq": seq, "reason": "no scan running"})
        pose = pose_from_meta(meta)
        if pose is None:
            self.stats["rejected"] += 1
            return await self.emit({"type": "frame_rejected", "seq": seq, "reason": "no orientation with the frame"})
        problem = A.check_capture(meta.get("camera"))
        if problem:  # the FOV only holds for the camera state it was calibrated in: refuse loudly, never anchor with a wrong angle
            self.stats["rejected"] += 1
            log.error("scan %s frame %s refused: %s", self.scan_id, seq, problem)
            await self.emit({"type": "error", "where": "camera", "message": problem})
            return await self.emit({"type": "frame_rejected", "seq": seq, "reason": problem})
        try:
            self.queue.put_nowait((seq, jpeg, pose, self._capture_time(pose), time.perf_counter()))
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
        wm.rebuild(sid)
        objs = wm.list_objects(sid)
        cov =yaw_coverage(wm.list_frames(sid))
        speech = f"I found {count_phrase([o['label'] for o in objs])}." if objs else "I did not find any objects."
        if cov["gaps"] and cov["covered_deg"] < 340:
            speech += f" About {360 - cov['covered_deg']} degrees of the room were not covered."
        def thumb(o):  # where to crop this object's picture from (the review shows objects grouped by kind, with thumbnails)
            rp = wm.replay(o["id"])
            return {"frame_id": rp["frame_id"], "box": rp["box"], "width": rp["width"], "height": rp["height"]} if rp else None

        try:  # advice for a scan that will be compared later; never blocks saving
            quality = await asyncio.to_thread(scan_quality.assess, wm, sid)
        except Exception:  # noqa: BLE001  a check that fails must not lose the scan
            log.exception("scan quality check failed for scan %s", sid)
            quality = None
        summary = {"type": "scan_summary", "scan_id": sid, "room_id": self.room["id"], "room_name": self.room["name"], "status": "draft",
                   "speech": speech, "coverage": cov, "cost_usd": round(self.usd, 4), "scan_quality": quality,
                   "seconds": round(time.perf_counter() - self.t0, 1), "timed_out": timed_out,
                   "frames": {k: self.stats[k] for k in ("received", "stored", "failed", "rejected")},
                   "objects": [{"id": o["id"], "label": o["label"], "yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1),
                                "n_obs": o["n_obs"], "conf_best": round(o["conf_best"], 2), "replay": thumb(o)} for o in objs]}
        self.scan_id = None
        log.info("scan %s ended: %d objects, frames %s, coverage %d deg, $%.3f", sid, len(objs), dict(self.stats), cov["covered_deg"], self.usd)
        await self.emit(summary)
        return summary

    async def _stop_workers(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    async def cancel(self) -> None:
        """The user discarded the scan: stop the workers and delete everything it stored. No summary is sent."""
        if not self.active:
            return
        sid = self.scan_id
        await self._stop_workers()
        self.scan_id = None
        self.svc.wm.delete_scan(sid)
        log.info("scan %s cancelled by the user and deleted", sid)
        await self.emit({"type": "scan_cancelled", "scan_id": sid})

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
