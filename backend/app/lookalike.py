"""Look-alikes: after a scan, decide whether two objects with the same name are one thing seen twice or two things.

The memory (app/memory.py) merges sightings by direction and colour. It can still leave one real laptop as two objects (the
anchors drifted further apart than its merge radius) and it names two real laptops both "laptop". This step looks at the
pairs that are genuinely in doubt and asks the model, with the pictures and the directions, which it is.

Calls are spent only where geometry cannot decide:
  * Two objects detected in the same photo are two things (one view does not see an object twice). No call.
  * Two objects further apart than the phone's direction error allows for one object cannot be one thing. No call.
  * Each object is paired only with its NEAREST look-alike (same name) inside that radius, so n laptops give at most n-1
    pairs, never n*(n-1)/2; the closest pairs (the likeliest duplicates) are asked first, up to MAX_CALLS per scan.

A "same" answer the model is sure of merges the two through the correction log (the same path as a merge made by hand, so
every later rebuild keeps it). "Different" is logged. "Unsure" changes nothing. Labels are never rewritten: objects of one
kind are told apart by a display name ("laptop 1", "laptop 2") computed from the scan, because comparing scans (Changes)
and Find match on the label itself.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field

from app import anchoring as A
from app.pricing import spark_usd

log = logging.getLogger("reality.lookalike")

MAX_CALLS = 6            # model calls per scan at most
MIN_RADIUS_DEG = 15.0    # one object's sightings can land this far apart even when each is well placed
MAX_RADIUS_DEG = 35.0    # beyond this, two anchors are never one object, whatever the stated uncertainty
RADIUS_K = 2.5           # ... otherwise this many times the two objects' combined direction uncertainty
SURE = 0.7               # a "same" answer below this confidence is treated as unsure (nothing is merged)
CROP_SIDE = 448          # longest side of each object crop sent to the model
WIDE_SIDE = 768          # longest side of the wide photo showing both directions

SYSTEM = (
    "You check a phone's room scan for duplicates. The phone knows only the DIRECTION of each object from where the person stood "
    "(yaw = compass bearing, pitch = up/down), never its distance, and its directions can be off by several degrees. "
    "Two detections share a name. Decide whether A and B are the SAME physical object photographed twice, or two DIFFERENT objects "
    "of the same kind.\n"
    "Say same only when the pictures agree (make, colour, shape, what is on it or next to it) and the direction gap is within the "
    "stated error. Say different when you can see a difference, or when one photo shows two of them at once. "
    "When the pictures cannot tell (blurred, cut off, too small), say unsure. Never guess. "
    "Reply with JSON: verdict (same, different or unsure), confidence (0 to 1), and reason (one short sentence a person can read)."
)
SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["same", "different", "unsure"]},
                   "confidence": {"type": "number"}, "reason": {"type": "string"}},
    "required": ["verdict", "confidence", "reason"], "additionalProperties": False,
}


def radius_deg(ua: float | None, ub: float | None) -> float:
    """How far apart two sightings of ONE object may land, from the two objects' direction uncertainties."""
    return max(MIN_RADIUS_DEG, min(MAX_RADIUS_DEG, RADIUS_K * ((ua or 0.0) + (ub or 0.0))))


@dataclass
class Pair:
    a: dict
    b: dict
    sep_deg: float
    radius_deg: float
    decided: str | None = None      # "different" when geometry alone decides it, else None (ask)
    why: str = ""
    verdict: str | None = None      # the outcome: same / different / unsure / skipped
    confidence: float | None = None
    reason: str = ""
    usd: float = 0.0
    evidence: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.a['id']}-{self.b['id']}"


def candidate_pairs(objs: list[dict], frames_of: dict[int, set[int]], max_calls: int = MAX_CALLS) -> tuple[list[Pair], list[Pair]]:
    """(pairs to ask the model about, pairs geometry already decided), from one scan's objects. Pure.

    `frames_of[object id]` is the set of frame ids the object was detected in. Each object is paired with its nearest object of
    the same label only; a pair is listed once."""
    by_label: dict[str, list[dict]] = {}
    for o in objs:
        by_label.setdefault(o["label"], []).append(o)
    seen: set[tuple[int, int]] = set()
    ask: list[Pair] = []
    decided: list[Pair] = []
    for label in sorted(by_label):
        group = by_label[label]
        if len(group) < 2:
            continue
        for o in group:
            near = min((p for p in group if p["id"] != o["id"]),
                       key=lambda p: (A.angular_distance(o["yaw"], o["pitch"], p["yaw"], p["pitch"]), p["id"]))
            k = tuple(sorted((o["id"], near["id"])))
            if k in seen:
                continue
            seen.add(k)
            a, b = (o, near) if o["id"] < near["id"] else (near, o)
            sep = A.angular_distance(a["yaw"], a["pitch"], b["yaw"], b["pitch"])
            p = Pair(a, b, sep, radius_deg(a.get("uncertainty_deg"), b.get("uncertainty_deg")))
            if frames_of.get(a["id"], set()) & frames_of.get(b["id"], set()):
                p.decided, p.why = "different", "seen together in one photo"
            elif sep > p.radius_deg:
                p.decided, p.why = "different", f"{sep:.0f}° apart, more than one object's sightings can drift"
            (decided if p.decided else ask).append(p)
    ask.sort(key=lambda p: (p.sep_deg, p.a["id"], p.b["id"]))  # the likeliest duplicates first
    for p in ask[max_calls:]:
        p.verdict, p.reason = "skipped", "not checked: this scan's limit of model calls was reached"
    return ask, decided


def display_names(objs: list[dict]) -> dict[int, str]:
    """{object id: name}: the label, numbered in the order the scan first saw them when a scan has more than one ("laptop 1")."""
    by_label: dict[str, list[dict]] = {}
    for o in objs:
        by_label.setdefault(o["label"], []).append(o)
    out: dict[int, str] = {}
    for label, group in by_label.items():
        if len(group) == 1:
            out[group[0]["id"]] = label
            continue
        for i, o in enumerate(sorted(group, key=lambda o: (o.get("first_seen") or 0.0, o["id"])), 1):
            out[o["id"]] = f"{label} {i}"
    return out


# ------------------------------------------------------------------ evidence (pictures from the stored frames)

def _jpeg_url(img, side: int) -> str:
    import cv2
    h, w = img.shape[:2]
    s = min(1.0, side / max(h, w))
    if s < 1.0:
        img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""


def _decode(jpeg: bytes | None):
    if not jpeg:
        return None
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def _crop(wm, obj_id: int) -> tuple[str | None, dict | None]:
    """The object's best sighting, cropped with a margin, and where it came from (for the phone's thumbnails)."""
    rp = wm.replay(obj_id)
    if not rp:
        return None, None
    img = _decode(wm.frame_jpeg(rp["frame_id"]))
    info = {"frame_id": rp["frame_id"], "box": rp["box"], "width": rp["width"], "height": rp["height"]}
    if img is None:
        return None, info
    h, w = img.shape[:2]
    sx, sy = w / rp["width"], h / rp["height"]
    x1, y1, x2, y2 = rp["box"]
    mx, my = (x2 - x1) * 0.3, (y2 - y1) * 0.3
    c = img[max(0, int((y1 - my) * sy)):min(h, int((y2 + my) * sy)), max(0, int((x1 - mx) * sx)):min(w, int((x2 + mx) * sx))]
    return (_jpeg_url(c, CROP_SIDE) if c.size else None), info


def _wide(wm, scan_id: int, a: dict, b: dict, dets_by_frame: dict[int, dict[int, list]]) -> str | None:
    """One photo of the scan where both directions are in view, A and B marked; preferring one that detected either. None if
    no photo shows both."""
    import cv2
    best, best_score = None, None
    for f in wm.list_frames(scan_id):
        roll = A.image_roll(f.get("roll"), f.get("screen_angle"))
        pts = []
        for o in (a, b):
            p = A.direction_to_pixel(o["yaw"], o["pitch"], f["width"], f["height"], f["yaw"], f["pitch"], roll, f["hfov_deg"])
            if p is None or not (0.08 * f["width"] <= p[0] <= 0.92 * f["width"] and 0.08 * f["height"] <= p[1] <= 0.92 * f["height"]):
                break
            pts.append(p)
        if len(pts) < 2:
            continue
        hits = dets_by_frame.get(f["id"], {})
        score = (len([o for o in (a, b) if o["id"] in hits]), -abs(pts[0][0] + pts[1][0] - f["width"]))  # detected there, then centred
        if best_score is None or score > best_score:
            best, best_score = (f, pts), score
    if best is None:
        return None
    f, pts = best
    img = _decode(wm.frame_jpeg(f["id"]))
    if img is None:
        return None
    h, w = img.shape[:2]
    sx, sy = w / f["width"], h / f["height"]
    for (o, tag, col), p in zip(((a, "A", (255, 164, 138)), (b, "B", (79, 213, 255))), pts):
        boxes = dets_by_frame.get(f["id"], {}).get(o["id"])
        if boxes:
            x1, y1, x2, y2 = boxes[0]
            cv2.rectangle(img, (int(x1 * sx), int(y1 * sy)), (int(x2 * sx), int(y2 * sy)), col, max(2, w // 300))
            at = (int(x1 * sx), max(18, int(y1 * sy) - 6))
        else:
            c = (int(p[0] * sx), int(p[1] * sy))
            cv2.circle(img, c, max(14, w // 40), col, max(2, w // 300))
            at = (c[0] + max(14, w // 40) + 4, c[1])
        cv2.putText(img, tag, at, cv2.FONT_HERSHEY_SIMPLEX, max(0.7, w / 900), col, max(2, w // 400), cv2.LINE_AA)
    return _jpeg_url(img, WIDE_SIDE)


def _describe(tag: str, o: dict) -> str:
    return (f"{tag}: direction yaw {o['yaw']:.0f}°, pitch {o['pitch']:.0f}°, seen in {o.get('n_obs', 1)} photo"
            f"{'' if o.get('n_obs', 1) == 1 else 's'}, direction error about ±{(o.get('uncertainty_deg') or 0):.0f}°.")


def messages_for(p: Pair, crop_a: str | None, crop_b: str | None, wide: str | None) -> list[dict]:
    text = (f"Kind: {p.a['label']}.\n{_describe('A', p.a)}\n{_describe('B', p.b)}\n"
            f"A and B are {p.sep_deg:.0f}° apart. Sightings of one object in this scan can land up to {p.radius_deg:.0f}° apart.\n"
            "Picture 1 is A, picture 2 is B" + (", picture 3 is a wider photo from the same scan with both directions in view, "
                                              "A and B marked (the marks can be a few degrees off)." if wide else "."))
    parts: list[dict] = [{"type": "text", "text": text}]
    for url in (crop_a, crop_b, wide):
        if url:
            parts.append({"type": "image", "data_url": url})
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": parts}]


# ------------------------------------------------------------------ the step

async def run(wm, reasoner, scan_id: int, emit=None, *, max_calls: int = MAX_CALLS, concurrency: int = 3, timeout_s: float = 30.0) -> dict:
    """Check one scan's look-alikes; merge the pairs the model is sure are one object. Returns a report:
    {"found": objects before, "pairs": [...], "merged": n, "usd": cost}. `emit` (async) gets progress for the phone."""
    async def tell(obj):
        if emit is not None:
            try:
                await emit({"type": "lookalike", "scan_id": scan_id, **obj})
            except Exception:  # noqa: BLE001 - progress is best effort
                pass

    objs = wm.list_objects(scan_id)
    rows = wm.db.execute("SELECT id, object_id, frame_id, box FROM detections WHERE scan_id=? AND object_id IS NOT NULL", (scan_id,)).fetchall()
    frames_of: dict[int, set[int]] = {}
    dets_by_frame: dict[int, dict[int, list]] = {}
    first_det: dict[int, int] = {}
    for r in rows:
        frames_of.setdefault(r["object_id"], set()).add(r["frame_id"])
        dets_by_frame.setdefault(r["frame_id"], {}).setdefault(r["object_id"], []).append(json.loads(r["box"]))
        first_det.setdefault(r["object_id"], r["id"])
    ask, decided = candidate_pairs(objs, frames_of, max_calls)
    todo = [p for p in ask if p.verdict is None]
    for p in decided:
        p.verdict, p.reason = p.decided, p.why

    def card(p: Pair) -> dict:
        return {"key": p.key, "label": p.a["label"], "sep_deg": round(p.sep_deg, 1), "verdict": p.verdict, "reason": p.reason,
                "a": {"id": p.a["id"], "replay": p.evidence.get("a")}, "b": {"id": p.b["id"], "replay": p.evidence.get("b")}}

    for p in todo:  # thumbnails first, so the phone can show the pairs while the model looks at them
        _, p.evidence["a"] = await asyncio.to_thread(_crop, wm, p.a["id"])
        _, p.evidence["b"] = await asyncio.to_thread(_crop, wm, p.b["id"])
    if not todo:  # nothing in doubt: no call, and nothing for the phone to show
        return {"found": len(objs), "pairs": [card(p) for p in ask + decided], "merged": 0, "usd": 0.0}
    await tell({"phase": "start", "found": len(objs), "checking": [card(p) for p in todo],
                "settled": len(decided), "skipped": len(ask) - len(todo)})

    sem = asyncio.Semaphore(concurrency)

    async def check(p: Pair) -> None:
        async with sem:
            try:
                ca, _ = await asyncio.to_thread(_crop, wm, p.a["id"])
                cb, _ = await asyncio.to_thread(_crop, wm, p.b["id"])
                wide = await asyncio.to_thread(_wide, wm, scan_id, p.a, p.b, dets_by_frame)
                if not ca or not cb:
                    p.verdict, p.reason = "unsure", "no picture of one of them"
                else:
                    r = await reasoner.respond(messages_for(p, ca, cb, wide), schema=SCHEMA, effort="low", max_output_tokens=400)
                    p.usd = spark_usd(r.input_tokens, r.output_tokens)
                    v = r.parsed or {}
                    p.verdict = v.get("verdict") if v.get("verdict") in ("same", "different", "unsure") else "unsure"
                    p.confidence = float(v.get("confidence") or 0.0)
                    p.reason = str(v.get("reason") or "")[:200]
                    if p.verdict == "same" and p.confidence < SURE:
                        p.verdict = "unsure"
            except Exception as e:  # noqa: BLE001 - one failed check leaves the pair as it was
                log.warning("scan %s look-alike %s failed: %s: %s", scan_id, p.key, type(e).__name__, e)
                p.verdict, p.reason = "unsure", "the check failed"
        await tell({"phase": "result", **card(p)})

    try:
        await asyncio.wait_for(asyncio.gather(*(check(p) for p in todo)), timeout_s)
    except asyncio.TimeoutError:
        for p in todo:
            if p.verdict is None:
                p.verdict, p.reason = "unsure", "the check took too long"

    merged = 0
    for p in todo:  # merges in pair order; a pair whose two sides already became one object is skipped
        if p.verdict == "same":
            oa, ob = (wm.db.execute("SELECT object_id FROM detections WHERE id=?", (first_det[o["id"]],)).fetchone()["object_id"] for o in (p.a, p.b))
            if oa is not None and ob is not None and oa != ob:
                try:
                    wm.merge_objects([oa, ob], source="lookalike")
                    merged += 1
                except (KeyError, ValueError) as e:
                    log.warning("scan %s look-alike merge %s refused: %s", scan_id, p.key, e)
        elif p.verdict == "different":
            wm.record_correction("distinct", scan_id, {"objects": [p.a["id"], p.b["id"]], "label": p.a["label"], "reason": p.reason,
                                                       "confidence": p.confidence}, source="lookalike")
    usd = sum(p.usd for p in todo)
    log.info("scan %s look-alikes: %d asked, %d settled by geometry, %d merged, $%.4f", scan_id, len(todo), len(decided), merged, usd)
    return {"found": len(objs), "pairs": [card(p) for p in ask + decided], "merged": merged, "usd": usd}
