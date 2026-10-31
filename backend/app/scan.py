"""Scan pipeline (F10): keyframe + camera pose -> Spark names the objects -> SAM boxes them -> World Model.

Per keyframe:
  1. resize the JPEG (SAM cost and latency grow with size; boxes are anchored with the resized dims);
  2. Spark lists the object types in view (one call, names reused from earlier frames of the scan);
  3. SAM runs one concept prompt per name, concurrently;
  4. overlapping boxes are reduced to one per object and the rest go to `WorldModel.observe`.

A frame is stored only if every SAM call succeeded. A stored frame says "this direction was looked
at", which what-changed (app.changes) uses to decide an object is gone; a half-failed frame would
fake that.

Spark only names what it sees, so an object it forgets to list is not looked for in that frame.
Merging across frames (overlap between keyframes) is what recovers misses.
"""
from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, field_validator

from app.concepts import canonical_name, is_sam_friendly
from app.pricing import SAM_USD_PER_IMAGE, spark_usd
from app.reasoner.base import Reasoner
from app.sam_client import SamCall
from app.memory import mask_histogram
from app.world_model import WorldModel, normalize_label

Segmenter = Callable[[str, str], Awaitable[SamCall]]

SYSTEM = """You make an inventory of a room from one camera photo so that objects can be found later.
List the distinct physical objects a person might ask to find: devices, bottles, cups, bags, keys, cables, chargers, books, stationery, remotes, tools, clothing, food items.
Skip: people and body parts, walls, floors, ceilings, windows, doors, lights, large fixed furniture, and anything that is only text or an image shown on a screen or printed on paper.
Each item is one short English noun phrase of 1 to 3 words naming the object type (for example "water bottle", "laptop", "phone charger"). No colors, no positions, no commas.
List each object type once even if several are visible. At most {n} items, most prominent first.
If a list of known names is given, reuse a name from it when it fits the object.
For each item also give size_cm: the typical real-world length of its longest side in centimetres (a mug 10, a laptop 34, a water bottle 24, a phone charger 8). Use null only if you cannot say."""

_SKIP = {"person", "people", "man", "woman", "human", "face", "hand", "screen", "monitor screen", "wall", "floor", "ceiling", "window", "door", "table", "desk", "chair"}


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    size_cm: float | None


class Listing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objects: list[Item]

    @field_validator("objects", mode="before")
    @classmethod
    def _bare_names(cls, v):  # a reply (or a test) that lists plain names has no sizes
        return [{"name": x, "size_cm": None} if isinstance(x, str) else x for x in v]


SCHEMA = Listing.model_json_schema()


def clean_names(raw: list[str], limit: int) -> list[str]:
    """SAM-friendly, normalised, unique names, in the order given."""
    out: list[str] = []
    for r in raw:
        if not is_sam_friendly(r):
            continue
        n = canonical_name(normalize_label(r))
        if n and n not in _SKIP and n not in out:
            out.append(n)
    return out[:limit]


def iou(a, b) -> float:
    ix, iy = max(0.0, min(a[2], b[2]) - max(a[0], b[0])), max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def suppress_overlaps(dets: list[dict], iou_thresh: float = 0.6, tie: float = 0.05) -> list[dict]:
    """One detection per physical object when different names (or duplicates) box the same thing.
    Highest confidence wins; within `tie` the more specific (longer) label wins."""
    order = sorted(dets, key=lambda d: (-d["conf"], -len(d["label"].split())))
    kept: list[dict] = []
    for d in order:
        clash = next((k for k in kept if iou(k["box"], d["box"]) > iou_thresh), None)
        if clash is None:
            kept.append(d)
        elif d["conf"] >= clash["conf"] - tie and len(d["label"].split()) > len(clash["label"].split()):
            kept[kept.index(clash)] = d
    return kept


def merge_parts(dets: list[dict], overlap: float = 0.35) -> list[dict]:
    """One detection per object when SAM boxes parts of the same thing (a laptop's screen and its base, a box
    nested inside a bigger one): same-label boxes whose overlap covers `overlap` of the smaller box become
    their union, keeping the best confidence. Different labels are left alone (a cup in front of a laptop)."""
    groups: list[dict] = []
    for d in sorted(dets, key=lambda d: -d["conf"]):
        box = list(d["box"])
        hits = [g for g in groups if g["label"] == d["label"] and _covered(g["box"], box) >= overlap]
        for g in hits:  # a new box can bridge two groups
            box = [min(box[0], g["box"][0]), min(box[1], g["box"][1]), max(box[2], g["box"][2]), max(box[3], g["box"][3])]
            groups.remove(g)
        groups.append({**(hits[0] if hits else d), "box": box, "conf": max([d["conf"]] + [g["conf"] for g in hits]),
                       "polygon": None if hits else d.get("polygon")})  # a merged box has no single mask
    return groups


def _covered(a, b) -> float:
    """Overlap area as a share of the smaller box."""
    ix, iy = max(0.0, min(a[2], b[2]) - max(a[0], b[0])), max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def pose_from_meta(meta: dict, prefer: str = "absolute") -> dict | None:
    """Camera pose from a Save-sample sidecar / frame meta: {yaw, pitch, roll, screen_angle, source, api}.
    Falls back to the other source when the preferred one is missing. None if there is no orientation."""
    o = meta.get("orientation") or {}
    for src in (prefer, "relative" if prefer == "absolute" else "absolute"):
        s = o.get(src)
        if s and s.get("yaw") is not None and s.get("pitch") is not None:
            cap = o.get("captured_at")  # phone clock, epoch milliseconds: when the frame was grabbed
            return {"yaw": s["yaw"], "pitch": s["pitch"], "roll": s.get("roll"), "source": src, "api": s.get("api"),
                    "screen_angle": (meta.get("camera") or {}).get("screen_angle", 0),
                    "captured_at": cap / 1000.0 if isinstance(cap, (int, float)) else None}
    return None


def resize_jpeg(jpeg: bytes, max_side: int, quality: int = 80) -> tuple[bytes, int, int]:
    img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not a decodable image")
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("could not encode image")
    return buf.tobytes(), w, h


@dataclass
class FrameReport:
    stored: bool
    frame_id: int | None = None
    names: list[str] = field(default_factory=list)
    detections: list[dict] = field(default_factory=list)  # kept, after overlap suppression
    observed: list[dict] = field(default_factory=list)  # WorldModel.observe results (object_id, new, yaw, pitch)
    dropped_low_conf: int = 0
    errors: list[str] = field(default_factory=list)
    spark_ms: float = 0.0
    sam_ms: float = 0.0  # wall time of the concurrent SAM stage
    sam_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def usd(self) -> float:
        return spark_usd(self.input_tokens, self.output_tokens) + self.sam_calls * SAM_USD_PER_IMAGE


class ScanPipeline:
    def __init__(self, wm: WorldModel, reasoner: Reasoner, segment: Segmenter, *, max_side: int = 960, quality: int = 80,
                 min_conf: float = 0.5, max_names: int = 10, concurrency: int = 4, sam_retries: int = 1, effort: str = "low"):
        self.wm, self.reasoner, self.segment = wm, reasoner, segment
        self.max_side, self.quality, self.min_conf = max_side, quality, min_conf
        self.max_names, self.sam_retries, self.effort = max_names, sam_retries, effort
        self._sem = asyncio.Semaphore(concurrency)

    async def list_names(self, data_url: str, known: list[str]) -> tuple[list[str], dict]:
        text = "Photo to inventory."
        if known:
            text += "\nKnown names: " + ", ".join(sorted(set(known)))
        r = await self.reasoner.respond(
            [{"role": "system", "content": SYSTEM.format(n=self.max_names)},
             {"role": "user", "content": [{"type": "text", "text": text}, {"type": "image", "data_url": data_url}]}],
            schema=SCHEMA, effort=self.effort, max_output_tokens=2000)
        listing = Listing.model_validate(r.parsed)
        names = clean_names([i.name for i in listing.objects], self.max_names)
        sizes: dict[str, float] = {}
        for i in listing.objects:  # the size Spark gave, filed under the same canonical name the objects will carry
            n = canonical_name(normalize_label(i.name))
            if n in names and i.size_cm and n not in sizes:
                sizes[n] = float(i.size_cm)
        return names, {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "ms": r.latency_ms, "sizes": sizes}

    async def _segment(self, data_url: str, name: str) -> tuple[SamCall, int]:
        calls = 0
        async with self._sem:
            while True:
                calls += 1
                r = await self.segment(data_url, name)
                if r.outcome != "error" or calls > self.sam_retries:
                    return r, calls

    async def process(self, scan_id: int, jpeg: bytes, ts: float, pose: dict) -> FrameReport:
        """Run one keyframe. `pose` needs yaw, pitch; roll, screen_angle, source, api, hfov_deg are optional."""
        small, w, h = resize_jpeg(jpeg, self.max_side, self.quality)
        data_url = "data:image/jpeg;base64," + base64.b64encode(small).decode()
        rep = FrameReport(stored=False)

        known = [o["label"] for o in self.wm.list_objects(scan_id)]
        rep.names, u = await self.list_names(data_url, known)
        rep.spark_ms, rep.input_tokens, rep.output_tokens = u["ms"], u["input_tokens"], u["output_tokens"]
        for n, size in u.get("sizes", {}).items():
            self.wm.record_label_size(n, size)

        t = time.perf_counter()
        results = await asyncio.gather(*(self._segment(data_url, n) for n in rep.names))
        rep.sam_ms = (time.perf_counter() - t) * 1000
        dets: list[dict] = []
        for name, (r, calls) in zip(rep.names, results):
            rep.sam_calls += calls
            if r.outcome == "error":
                rep.errors.append(f"{name}: {r.error}")
                continue
            for o in r.objects:
                if o.box is None:
                    continue
                conf = o.confidence if o.confidence is not None else 0.0
                if conf < self.min_conf:
                    rep.dropped_low_conf += 1
                    continue
                dets.append({"label": name, "box": list(o.box), "conf": conf, "polygon": o.polygon})
        if rep.errors:
            return rep  # incomplete look: do not record this direction as scanned

        rep.detections = merge_parts(suppress_overlaps(dets))
        img = cv2.imdecode(np.frombuffer(small, dtype=np.uint8), cv2.IMREAD_COLOR)
        for d in rep.detections:  # colour signature of each object, used to refuse merging two different-looking things
            d["hist"] = mask_histogram(img, d.get("polygon"), d["box"]) if img is not None else None
        rep.frame_id = self.wm.add_frame(scan_id, ts, pose["yaw"], pose["pitch"], w, h, roll=pose.get("roll"),
                                         screen_angle=pose.get("screen_angle", 0.0), source=pose.get("source"),
                                         api=pose.get("api"), hfov_deg=pose.get("hfov_deg"), jpeg=small)
        rep.observed = self.wm.observe(rep.frame_id, rep.detections)
        rep.stored = True
        return rep
