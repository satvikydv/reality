"""Is this scan good enough to compare against later? Advice for the person scanning, shown on the review; it never blocks saving.

Each check follows from a rule the comparison applies (app.changes, app.align), so the advice says what would make a later "what changed"
trustworthy rather than generic photography tips:

  - frames: the alignment's standard error counts distinct frames on the thinner side (app.align.combine); with a phone's usual two degrees
    of scatter, 1.25 x 2 / sqrt(n) stays under the 1.5 degree limit with room to spare from five frames on. Arithmetic, not a measurement.
  - overlap: every shot should have a neighbour that shares half its view, so that every direction is in two shots.
  - seen twice: an object seen in only one frame is never called removed or new later (MIN_FRAMES_FOR_GONE), so most should be seen twice.
  - background texture: the alignment matches texture; when the shots are mostly a desk and the objects on it, there is little that stays put.
    Tilting up brings in the wall behind, which also moves least when the phone is not held in exactly the same spot.
  - sharpness: a blurred shot matches badly. Judged against the scan's own sharpest shots (sharpness depends a lot on what is in view).

The thresholds were set on the synthetic worlds in tests/synthetic_world.py and are not yet checked on a phone.
"""
from __future__ import annotations

import json
import statistics

import cv2
import numpy as np

from app import align
from app import anchoring as A

MIN_FRAMES = 5
MAX_STEP_SHARE = 0.5        # the largest turn between neighbouring shots, as a share of the field of view
MIN_SEEN_TWICE = 0.6        # share of objects seen in at least two frames
MIN_BACKGROUND = 60         # median SIFT features per shot outside the detected objects (at 640 px)
LOOKING_DOWN_DEG = -25.0    # a mean pitch below this: the shots are mostly of a desk or the floor
BLUR_SHARE = 0.35           # a shot is blurred when its sharpness is below this share of the scan's median ...
BLUR_FLOOR = 15.0           # ... or below this absolute value (variance of the Laplacian at 640 px)
MAX_BLURRED_SHARE = 1 / 3


def _sharpness(jpeg: bytes | None) -> float | None:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE) if jpeg else None
    if img is None:
        return None
    s = min(1.0, align.MAX_SIDE / max(img.shape))
    if s < 1.0:
        img = cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def assess(wm, scan_id: int) -> dict:
    """{ok, checks: [{id, ok, value, target, tip}], tips: [the tips of the checks that failed, most useful first]}."""
    frames = wm.list_frames(scan_id)
    objs = wm.list_objects(scan_id)
    boxes: dict[int, list] = {}
    for r in wm.db.execute("SELECT frame_id, box FROM detections WHERE scan_id=?", (scan_id,)):
        boxes.setdefault(r["frame_id"], []).append(json.loads(r["box"]))
    checks = []

    def check(cid: str, ok: bool, value, target, tip: str) -> None:
        checks.append({"id": cid, "ok": bool(ok), "value": value, "target": target, "tip": None if ok else tip})

    n = len(frames)
    check("frames", n >= MIN_FRAMES, n, MIN_FRAMES,
          f"Take a few more shots: turn more slowly so the phone saves more pictures ({n} of at least {MIN_FRAMES}).")

    # every shot should have another shot close enough to share half its view (in any order: a scan can come back to a direction)
    nearest = [min((A.angular_distance(a["yaw"], a["pitch"], b["yaw"], b["pitch"]) for b in frames if b is not a), default=0.0) for a in frames]
    fov = min((f["hfov_deg"] for f in frames), default=A.DEFAULT_HFOV_DEG)
    worst = max(nearest, default=0.0) if len(frames) > 1 else 0.0
    check("overlap", worst <= MAX_STEP_SHARE * fov + 0.5, round(worst, 1), round(MAX_STEP_SHARE * fov, 1),
          f"Pan slower: one shot is {round(worst)}° from its nearest neighbour, so they barely overlap. Each part of the room should be in two shots.")

    if objs:
        twice = sum(1 for o in objs if (o.get("n_obs") or 0) >= 2)
        check("seen_twice", twice >= MIN_SEEN_TWICE * len(objs), f"{twice} of {len(objs)}", f"{round(MIN_SEEN_TWICE * 100)}%",
              f"Pan slower so each thing is in at least two shots: only {twice} of {len(objs)} were. Something seen once cannot be called gone later.")

    bg, sharp = [], []
    for f in frames:
        jpeg = wm.frame_jpeg(f["id"])
        ft = align.extract(jpeg, boxes.get(f["id"]))
        bg.append(0 if ft is None else int((ft.region < 0).sum()) if ft.region is not None else len(ft.pts))
        s = _sharpness(jpeg)
        if s is not None:
            sharp.append(s)
    if bg:
        med = int(statistics.median(bg))
        down = statistics.fmean(f["pitch"] for f in frames) < LOOKING_DOWN_DEG
        check("background", med >= MIN_BACKGROUND, med, MIN_BACKGROUND,
              "Tilt up a little so the far wall is in view: the shots are mostly the desk and the things on it, which leaves little to line a later scan up with."
              if down else "Include more of the room around the objects: there is little texture outside them to line a later scan up with.")
    if sharp:
        mid = statistics.median(sharp)
        blurred = sum(1 for s in sharp if s < max(BLUR_FLOOR, BLUR_SHARE * mid))
        check("sharp", blurred <= MAX_BLURRED_SHARE * len(sharp), f"{blurred} of {len(sharp)} blurred", f"at most {round(MAX_BLURRED_SHARE * 100)}%",
              f"Hold the phone still for a moment at each step: {blurred} of {len(sharp)} shots are blurred.")

    order = ["frames", "seen_twice", "overlap", "background", "sharp"]
    tips = [c["tip"] for c in sorted(checks, key=lambda c: order.index(c["id"])) if c["tip"]]
    return {"ok": not tips, "checks": checks, "tips": tips,
            "words": "A good scan to compare against later." if not tips else "Saved scans can still be compared, but a later comparison will be surer if next time you:"}
