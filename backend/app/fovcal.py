"""Field-of-view calibration at the exact capture state.

The horizontal FOV depends on the camera mode (resolution, which lens, zoom). A value measured in one mode is wrong in
another, so each calibration shot is a photo taken with the app ("Save sample"), whose sidecar records the track settings
the camera was in, and every shot must have been taken in the same state. The result is written with that state, and
the server refuses frames from a camera in any other state (anchoring.check_capture).
"""
from __future__ import annotations

import math
import statistics

STATE_KEYS = ("width", "height", "zoom", "facingMode", "deviceId")


class CalibrationError(ValueError):
    pass


def hfov_from_shot(object_cm: float, distance_cm: float, pixels: float, image_width: float) -> float:
    """A pinhole camera: an object `object_cm` wide at `distance_cm` spans `pixels` of an `image_width` image."""
    if min(object_cm, distance_cm, pixels, image_width) <= 0:
        raise CalibrationError("object width, distance, pixels and image width must all be positive")
    focal_px = pixels * distance_cm / object_cm
    return math.degrees(2 * math.atan(image_width / (2 * focal_px)))


def capture_state(sidecar: dict) -> dict:
    """The camera state a saved sample was taken in, from its sidecar. Fails loudly when none was recorded."""
    cam = sidecar.get("camera") or {}
    s = cam.get("settings")
    if not s or s.get("width") is None or s.get("height") is None:
        raise CalibrationError("this sample has no camera settings recorded; take it again with the current app (Save sample in the ... menu)")
    state = {k: s[k] for k in STATE_KEYS if s.get(k) is not None}
    state["zoomLocked"] = bool(s.get("zoomLocked"))
    return state


def calibrate(shots: list[dict], object_cm: float) -> dict:
    """shots: [{distance_cm, pixels, sidecar}]. Returns the calibration record. Refuses shots from different camera states."""
    if not shots:
        raise CalibrationError("give at least one shot")
    states = [capture_state(s["sidecar"]) for s in shots]
    first = {k: v for k, v in states[0].items() if k != "zoomLocked"}
    for i, st in enumerate(states[1:], start=2):
        diff = {k: (first.get(k), st.get(k)) for k in set(first) | set(st) if k != "zoomLocked" and first.get(k) != st.get(k)}
        if diff:
            raise CalibrationError(f"shot {i} was taken in a different camera state than shot 1: "
                                   + ", ".join(f"{k} {a} vs {b}" for k, (a, b) in sorted(diff.items())) + ". Retake them without touching zoom or the camera.")
    if not all(st["zoomLocked"] for st in states) and "zoom" not in first:
        raise CalibrationError("zoom could not be locked on this camera and is not reported, so it cannot be guarded; calibration would be unreliable")
    each = []
    for s in shots:
        w = s["sidecar"].get("width")
        if not w:
            raise CalibrationError("a sidecar has no image width")
        each.append(hfov_from_shot(object_cm, s["distance_cm"], s["pixels"], w))
    spread = max(each) - min(each)
    return {"hfov_deg": round(statistics.mean(each), 2), "width": first["width"], "height": first["height"], "zoom": first.get("zoom"),
            "facingMode": first.get("facingMode"), "deviceId": first.get("deviceId"), "shots": [round(v, 2) for v in each],
            "spread_deg": round(spread, 2), "object_cm": object_cm}
