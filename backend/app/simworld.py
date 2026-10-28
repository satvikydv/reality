"""A room with known contents, as a sweeping phone camera would see it. Used by the memory tests and by
scripts/eval_memory.py: because the truth is known, duplicates, identity switches and find success can be counted.

Objects are rectangles on the sphere (centre yaw/pitch, size in degrees) with a colour signature. Each camera frame
projects them through a pinhole with the *true* field of view, clips them at the image border, jitters the boxes,
drops some detections at random and perturbs the colour histogram, so the memory has to cope with what real
detections look like: partial views, misses, and a field of view that is not exactly the one it assumes.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from app import anchoring as A

W, H = 540, 960
LABELS = ["laptop", "cup", "cable", "backpack", "phone", "mouse", "water bottle", "charger"]
SIZE_DEG = {"laptop": (22, 16), "cup": (6, 8), "cable": (14, 5), "backpack": (18, 24), "phone": (5, 9), "mouse": (5, 4),
            "water bottle": (5, 14), "charger": (7, 6)}
NBINS = 27  # matches memory.mask_histogram


@dataclass
class Truth:
    id: int
    label: str
    yaw: float
    pitch: float
    w: float
    h: float
    hist: list


def _hist(rng: random.Random, dominant: int) -> list:
    v = [rng.random() * 0.03 for _ in range(NBINS)]
    v[dominant] += 1.0
    v[(dominant + 1) % NBINS] += 0.3
    s = sum(v)
    return [x / s for x in v]


def make_scene(seed: int = 0, n: int = 16, min_gap_deg: float = 14.0, twins: int = 0, twin_gap_deg: float = 3.0) -> list[Truth]:
    """`n` objects around the room. Same-label objects are at least `min_gap_deg` apart and have different colours,
    so each really is a separate thing the memory must keep apart. `twins` more objects are then placed
    `twin_gap_deg` from an existing one with the same label and size but another colour: on position alone they
    are the same object, and only their appearance says otherwise."""
    rng = random.Random(seed)
    truths: list[Truth] = []
    tries = 0
    while len(truths) < n and tries < 5000:
        tries += 1
        label = LABELS[len(truths) % len(LABELS)] if len(truths) < len(LABELS) else rng.choice(LABELS)
        yaw, pitch = rng.uniform(0, 360), rng.uniform(-25, 25)
        sw, sh = SIZE_DEG[label]
        if any(t.label == label and A.angular_distance(t.yaw, t.pitch, yaw, pitch) < max(min_gap_deg, sw + t.w) for t in truths):
            continue
        used = {max(range(NBINS), key=t.hist.__getitem__) for t in truths if t.label == label}
        dom = rng.choice([b for b in range(0, NBINS, 2) if b not in used])
        truths.append(Truth(len(truths), label, yaw, pitch, sw * rng.uniform(0.85, 1.15), sh * rng.uniform(0.85, 1.15), _hist(rng, dom)))
    for k in range(twins):
        base = truths[(k * 3) % n]
        used = {max(range(NBINS), key=t.hist.__getitem__) for t in truths if t.label == base.label}
        dom = rng.choice([b for b in range(1, NBINS, 2) if b not in used and b != 26])
        truths.append(Truth(len(truths), base.label, (base.yaw + twin_gap_deg) % 360, base.pitch, base.w, base.h, _hist(rng, dom)))
    return truths


def render(truths: list[Truth], cam_yaw: float, cam_pitch: float, rng: random.Random, true_hfov: float = 60.0,
           noise_px: float = 5.0, miss_p: float = 0.08, min_visible: float = 0.3) -> list[tuple[int, dict]]:
    """[(truth id, detection)] for one frame. A detection has label, box, conf and a colour histogram."""
    out = []
    for t in truths:
        corners = [A.direction_to_pixel(t.yaw + dy, t.pitch + dp, W, H, cam_yaw, cam_pitch, 0.0, true_hfov)
                   for dy in (-t.w / 2, t.w / 2) for dp in (-t.h / 2, t.h / 2)]
        if any(c is None for c in corners):
            continue
        x1, x2 = min(c[0] for c in corners), max(c[0] for c in corners)
        y1, y2 = min(c[1] for c in corners), max(c[1] for c in corners)
        full = max(1.0, (x2 - x1) * (y2 - y1))
        cx1, cy1, cx2, cy2 = max(0.0, x1), max(0.0, y1), min(float(W), x2), min(float(H), y2)
        if cx2 <= cx1 or cy2 <= cy1 or (cx2 - cx1) * (cy2 - cy1) < min_visible * full or rng.random() < miss_p:
            continue
        j = lambda: rng.uniform(-noise_px, noise_px)  # noqa: E731
        box = [max(0.0, cx1 + j()), max(0.0, cy1 + j()), min(float(W), cx2 + j()), min(float(H), cy2 + j())]
        if box[2] - box[0] < 6 or box[3] - box[1] < 6:
            continue
        h = [max(0.0, v * rng.uniform(0.9, 1.1)) for v in t.hist]
        s = sum(h)
        out.append((t.id, {"label": t.label, "box": box, "conf": round(rng.uniform(0.6, 0.98), 3), "hist": [v / s for v in h]}))
    return out


def sweep(truths: list[Truth], seed: int = 0, step_deg: float = 15.0, pitches=(0.0,), true_hfov: float = 60.0, **kw) -> list[dict]:
    """Frames of a full turn: [{ts, yaw, pitch, dets: [detection], truth: [truth id]}], in capture order."""
    rng = random.Random(seed + 1000)
    frames = []
    for p in pitches:
        for k in range(int(360 / step_deg)):
            yaw = (k * step_deg + rng.uniform(-2, 2)) % 360
            pairs = render(truths, yaw, p + rng.uniform(-3, 3), rng, true_hfov=true_hfov, **kw)
            frames.append({"ts": 1000.0 + len(frames), "yaw": yaw, "pitch": p, "dets": [d for _, d in pairs], "truth": [i for i, _ in pairs]})
    return frames


def ingest(wm, scan_id: int, frames: list[dict], order: list[int] | None = None, hfov: float | None = None) -> list[list[dict]]:
    """Store frames in `order` (arrival order; default capture order). Returns, per frame in that order, what `observe` said."""
    seen = []
    for i in (order if order is not None else range(len(frames))):
        f = frames[i]
        fid = wm.add_frame(scan_id, f["ts"], f["yaw"], f["pitch"], W, H, roll=0.0, screen_angle=0.0, hfov_deg=hfov)
        seen.append(wm.observe(fid, [dict(d) for d in f["dets"]]))
    return seen


def fingerprint(wm, scan_id: int) -> list[tuple]:
    """The scan's objects without ids, for comparing two runs."""
    return sorted((o["label"], round(o["yaw"], 4), round(o["pitch"], 4), o["n_obs"], round(o["last_seen"], 3), round(o["uncertainty_deg"], 4),
                   round(o["conf_best"], 4)) for o in wm.list_objects(scan_id))
