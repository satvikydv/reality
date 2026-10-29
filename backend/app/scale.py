"""How far away an object is, roughly, and how much a change of viewpoint can move it in the picture.

An object's real size is not known, but Spark gives a typical size for each name (stored per label, see WorldModel.record_label_size), and a
box says how large the object looks. Distance is then size x focal length / box size, with a wide band because real objects vary
(a mug is 8 to 12 cm, a "bottle" 15 to 35). The band is honest about that and is carried into every decision that depends on it.

A viewpoint error of `e` centimetres moves a thing at distance `d` by about atan(e / d) degrees in the picture: little for a wall, a lot for
a mug an arm's length away. When the alignment found the direction the second scan was taken from, only the part of that shift across the line
of sight counts (an object straight ahead of the movement barely shifts), but never less than a third of the full amount, because that
direction is itself an estimate and the size of the shift is assumed.
"""
from __future__ import annotations

import json
import math
import os

from app import anchoring as A

VIEWPOINT_CM = float(os.getenv("VIEWPOINT_ERROR_CM", "15"))   # how far the phone may stand from where the first scan was made
SIZE_REL_ERR = 0.35       # typical sizes are good to about a third
BOX_REL_ERR = 0.08        # a box is good to about a twelfth
MIN_PERP = 0.33           # least share of the viewpoint error that is assumed to act across the line of sight
FALLBACK_CM = 30.0
MIN_DISTANCE_CM = 25.0

# Typical longest side in centimetres, used when Spark gave no size for a label. Rough, on purpose.
DEFAULT_SIZE_CM = {
    "mug": 10, "cup": 10, "glass": 12, "bottle": 24, "water bottle": 24, "can": 12, "plate": 25, "bowl": 15, "laptop": 34, "keyboard": 44, "mouse": 11,
    "monitor": 55, "phone": 15, "charger": 8, "cable": 100, "headphones": 20, "remote": 18, "remote control": 18, "keys": 8, "wallet": 11, "glasses": 14,
    "book": 24, "notebook": 25, "pen": 14, "pencil": 18, "scissors": 18, "bag": 40, "backpack": 45, "box": 30, "lamp": 40, "clock": 20, "plant": 35,
    "pillow": 45, "towel": 70, "shoe": 28, "jacket": 65, "umbrella": 80, "tablet": 25, "speaker": 20, "camera": 12, "toothbrush": 18, "pills": 8,
}


def size_for(wm, label: str) -> tuple[float, str]:
    """(size in cm, where it came from): "spark" (stored from the name pass), "default" (the table above) or "fallback"."""
    s = wm.get_label_size(label)
    if s:
        return s, "spark"
    d = DEFAULT_SIZE_CM.get(label)
    return (float(d), "default") if d else (FALLBACK_CM, "fallback")


def _median(v: list[float]) -> float:
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def distance_estimate(wm, obj: dict, size_cm: float, size_source: str = "default") -> dict | None:
    """{distance_cm, lo_cm, hi_cm, rel_err} for an object from the boxes of its detections; None when it has none."""
    rows = wm.db.execute("SELECT d.box, d.fragment, f.width, f.hfov_deg FROM detections d JOIN frames f ON f.id=d.frame_id WHERE d.object_id=?", (obj["id"],)).fetchall()
    if not rows:
        return None
    full = [r for r in rows if not r["fragment"]] or rows            # a box clipped by the frame edge says nothing about size
    ds = []
    for r in full:
        b = json.loads(r["box"])
        px = max(b[2] - b[0], b[3] - b[1])
        if px > 1:
            ds.append(size_cm * A.focal_px(r["width"], r["hfov_deg"]) / px)
    if not ds:
        return None
    d = max(MIN_DISTANCE_CM, _median(ds))
    spread = _median([abs(x - d) for x in ds]) / d if len(ds) > 2 else 0.0
    rel = min(0.8, math.hypot(SIZE_REL_ERR * (1.0 if size_source != "fallback" else 1.6), BOX_REL_ERR, spread))
    return {"distance_cm": round(d, 1), "lo_cm": round(max(MIN_DISTANCE_CM * 0.6, d * (1 - rel)), 1), "hi_cm": round(d * (1 + rel), 1), "rel_err": round(rel, 2),
            "size_cm": size_cm, "size_source": size_source}


def tolerance_deg(distance_cm: float, viewpoint_cm: float = VIEWPOINT_CM, across: float | None = None) -> float:
    """The shift in the picture, in degrees, that a viewpoint error alone can cause for something at `distance_cm`.
    `across`: share (0..1) of the assumed movement that is across the line of sight, when the direction of movement is known."""
    k = 1.0 if across is None else max(MIN_PERP, min(1.0, across))
    return math.degrees(math.atan2(viewpoint_cm * k, max(distance_cm, 1.0)))


def across_share(t_world: dict | None, yaw: float, pitch: float) -> float | None:
    """How much of a movement in direction `t_world` ({east, north, up}) lies across the line of sight toward (yaw, pitch)."""
    if not t_world:
        return None
    t = (t_world["east"], t_world["north"], t_world["up"])
    u = A.direction_vector(yaw, pitch)
    along = A.dot(t, u)
    return math.sqrt(max(0.0, 1.0 - along * along))


def band_deg(est: dict, viewpoint_cm: float = VIEWPOINT_CM, across: float | None = None) -> tuple[float, float]:
    """(tolerance at the best-guess distance, extra uncertainty from the distance band): the band's near end allows a larger shift."""
    mid = tolerance_deg(est["distance_cm"], viewpoint_cm, across)
    near = tolerance_deg(est["lo_cm"], viewpoint_cm, across)
    return mid, max(0.0, near - mid)
