"""Pixel <-> world direction math for the room scan (F10, F12).

Frame of reference: earth east-north-up. A direction is (yaw, pitch) in degrees: yaw is the compass
azimuth clockwise from north in [0, 360), pitch is elevation with +90 straight up. The camera pose is
the rear camera's pointing direction (yaw, pitch) plus roll about that axis, exactly as produced by
web/js/orientation.js (roll positive = clockwise as seen from behind the phone).

The camera is a pinhole with square pixels; lens distortion is ignored. Translation is unknown, so a
direction is all we can anchor to. Assumptions to check on the phone [VERIFY]:
  - the JPEG's "up" follows the screen (Chrome swaps video dimensions with screen rotation), so the
    image roll is the sensor roll plus screen.orientation.angle;
  - DEFAULT_HFOV_DEG is a placeholder; calibrate per phone.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

# Horizontal field of view across the image width as sent. One value for the whole system: the server anchors
# detections with it and the phone projects them back with it (served by /api/overlay). It is only valid for the
# camera settings it was measured at (resolution, zoom), so a calibration records them and frames from a camera in
# another state are refused (see check_capture). Without a calibration the value is the 60 degree placeholder.
CALIBRATION_FILE = Path(os.environ.get("FOV_CALIBRATION") or Path(__file__).resolve().parents[2] / "data" / "fov_calibration.json")


def load_calibration() -> dict | None:
    """{hfov_deg, width, height, zoom, ...} from HFOV_DEG (no capture settings known) or data/fov_calibration.json."""
    env = os.environ.get("HFOV_DEG")
    if env:
        return {"hfov_deg": float(env), "width": None, "height": None, "zoom": None, "source": "HFOV_DEG"}
    try:
        d = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        return {**d, "hfov_deg": float(d["hfov_deg"]), "source": str(CALIBRATION_FILE)}
    except (OSError, ValueError, KeyError):
        return None


CALIBRATION = load_calibration()
DEFAULT_HFOV_DEG = CALIBRATION["hfov_deg"] if CALIBRATION else 60.0
FOV_CALIBRATED = CALIBRATION is not None
# How wrong the FOV may be, as a share of the angle from the optical axis: a calibrated value is trusted to ~2%,
# the placeholder only to ~10%. Anchor error from this grows with the distance from the image centre.
FOV_REL_ERROR = 0.02 if FOV_CALIBRATED else 0.10


def check_capture(camera: dict | None, calibration: dict | None = None) -> str | None:
    """None when the camera that took a frame matches the one the FOV was calibrated for, else a sentence saying
    what differs. `camera` is the frame's camera meta ({video_w, video_h, settings: {width, height, zoom}})."""
    cal = CALIBRATION if calibration is None else calibration
    if not cal or not cal.get("width") or not cal.get("height") or not camera:
        return None  # nothing recorded about the calibration camera (or the frame): cannot compare
    s = camera.get("settings") or {}
    w, h = s.get("width") or camera.get("video_w"), s.get("height") or camera.get("video_h")
    diffs = []
    if w and h and (w, h) != (cal["width"], cal["height"]):
        diffs.append(f"resolution {w}x{h} (calibrated at {cal['width']}x{cal['height']})")
    z, cz = s.get("zoom"), cal.get("zoom")
    if z is not None and cz is not None and abs(z - cz) > 0.01:
        diffs.append(f"zoom {z:g} (calibrated at {cz:g})")
    return ("the camera is not in the state the FOV was calibrated for: " + ", ".join(diffs)) if diffs else None


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def image_roll(sensor_roll_deg: float | None, screen_angle_deg: float | None = 0.0) -> float:
    """Roll of the image about the viewing axis. A missing sensor roll is treated as 0."""
    return (sensor_roll_deg or 0.0) + (screen_angle_deg or 0.0)


def _unit(yaw_deg: float, pitch_deg: float) -> tuple[float, float, float]:
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (math.cos(p) * math.sin(y), math.cos(p) * math.cos(y), math.sin(p))


direction_vector = _unit


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


dot = _dot


def _to_yaw_pitch(v) -> tuple[float, float]:
    n = math.sqrt(_dot(v, v))
    return (math.degrees(math.atan2(v[0], v[1])) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, v[2] / n)))))


def camera_basis(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0):
    """(right, up, forward) unit vectors of the image in earth coordinates."""
    fwd = _unit(yaw_deg, pitch_deg)
    y = math.radians(yaw_deg)
    right_ref = (math.cos(y), -math.sin(y), 0.0)  # right of the heading; defined even when looking straight up/down
    up_ref = _cross(right_ref, fwd)
    r = math.radians(roll_deg)
    c, s = math.cos(r), math.sin(r)
    up = tuple(c * u + s * rr for u, rr in zip(up_ref, right_ref))
    right = tuple(c * rr - s * u for u, rr in zip(up_ref, right_ref))
    return right, up, fwd


def focal_px(width: int, hfov_deg: float) -> float:
    return (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def pixel_to_direction(u: float, v: float, width: int, height: int, yaw_deg: float, pitch_deg: float,
                       roll_deg: float = 0.0, hfov_deg: float = DEFAULT_HFOV_DEG) -> tuple[float, float]:
    """World (yaw, pitch) of the ray through pixel (u, v); origin top-left, v down."""
    f = focal_px(width, hfov_deg)
    x = (u - width / 2.0) / f
    y = -(v - height / 2.0) / f
    right, up, fwd = camera_basis(yaw_deg, pitch_deg, roll_deg)
    ray = tuple(x * right[i] + y * up[i] + fwd[i] for i in range(3))
    return _to_yaw_pitch(ray)


def direction_to_pixel(yaw_deg: float, pitch_deg: float, width: int, height: int, cam_yaw_deg: float,
                       cam_pitch_deg: float, cam_roll_deg: float = 0.0,
                       hfov_deg: float = DEFAULT_HFOV_DEG) -> tuple[float, float] | None:
    """Pixel where a world direction appears, or None if it is behind the camera. May lie outside the image."""
    right, up, fwd = camera_basis(cam_yaw_deg, cam_pitch_deg, cam_roll_deg)
    d = _unit(yaw_deg, pitch_deg)
    z = _dot(d, fwd)
    if z <= 1e-6:
        return None
    f = focal_px(width, hfov_deg)
    return (width / 2.0 + f * _dot(d, right) / z, height / 2.0 - f * _dot(d, up) / z)


def angular_distance(yaw1: float, pitch1: float, yaw2: float, pitch2: float) -> float:
    """Great-circle separation in degrees."""
    c = _dot(_unit(yaw1, pitch1), _unit(yaw2, pitch2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def mean_direction(items: list[tuple[float, float, float]]) -> tuple[float, float]:
    """Weighted mean of (yaw, pitch, weight) directions, safe across the 0/360 wrap."""
    v = [0.0, 0.0, 0.0]
    for yaw, pitch, w in items:
        u = _unit(yaw, pitch)
        for i in range(3):
            v[i] += w * u[i]
    return _to_yaw_pitch(tuple(v))


def box_anchor(box: tuple[float, float, float, float], width: int, height: int, yaw_deg: float, pitch_deg: float,
               roll_deg: float = 0.0, hfov_deg: float = DEFAULT_HFOV_DEG) -> tuple[float, float]:
    """(yaw, pitch) anchor of a detection box (x1, y1, x2, y2 in pixels), using the box centre."""
    return pixel_to_direction((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0, width, height,
                              yaw_deg, pitch_deg, roll_deg, hfov_deg)


def box_extent(box: tuple[float, float, float, float], width: int, height: int, yaw_deg: float, pitch_deg: float,
               roll_deg: float = 0.0, hfov_deg: float = DEFAULT_HFOV_DEG) -> tuple[float, float, float, float, float]:
    """Angular footprint of a box: (yaw0, dyaw_lo, dyaw_hi, pitch_lo, pitch_hi), yaw offsets measured from yaw0
    (the box centre's yaw) so the 0/360 wrap never splits it."""
    c, _ = box_anchor(box, width, height, yaw_deg, pitch_deg, roll_deg, hfov_deg)
    ys, ps = [], []
    for u, v in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        y, p = pixel_to_direction(u, v, width, height, yaw_deg, pitch_deg, roll_deg, hfov_deg)
        ys.append(wrap180(y - c))
        ps.append(p)
    return c, min(ys), max(ys), min(ps), max(ps)


def extent_area(e) -> float:
    return max(0.0, e[2] - e[1]) * max(0.0, e[4] - e[3])


def extent_overlap(a, b) -> tuple[float, float]:
    """How much two angular footprints overlap: (share of the smaller one covered, share of the larger one covered)."""
    s = wrap180(a[0] - b[0])
    iy = min(s + a[2], b[2]) - max(s + a[1], b[1])
    ip = min(a[4], b[4]) - max(a[3], b[3])
    if iy <= 0 or ip <= 0:
        return 0.0, 0.0
    inter, aa, ab = iy * ip, extent_area(a), extent_area(b)
    return (inter / min(aa, ab) if min(aa, ab) > 0 else 0.0), (inter / max(aa, ab) if max(aa, ab) > 0 else 0.0)


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _basis_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float):
    """Columns are the image's (right, up, forward) in earth coordinates: earth = B @ camera."""
    right, up, fwd = camera_basis(yaw_deg, pitch_deg, roll_deg)
    return [[right[i], up[i], fwd[i]] for i in range(3)]


def _k(width: int, height: int, hfov_deg: float):
    """Intrinsics as a 3x3 acting on camera coordinates (x right, y up, z forward); image y points down, hence -f."""
    f = focal_px(width, hfov_deg)
    return [[f, 0.0, width / 2.0], [0.0, -f, height / 2.0], [0.0, 0.0, 1.0]]


def _k_inv(width: int, height: int, hfov_deg: float):
    f = focal_px(width, hfov_deg)
    return [[1.0 / f, 0.0, -width / (2.0 * f)], [0.0, -1.0 / f, height / (2.0 * f)], [0.0, 0.0, 1.0]]


def homography(cap: dict, cur: dict) -> list[list[float]]:
    """H = K_cur R K_cap^-1: where a pixel of the capture image lands in the current view, for a camera that only
    rotates (translation is unknown and ignored). `cap` and `cur` are {yaw, pitch, roll, width, height, hfov}."""
    b_cap = _basis_matrix(cap["yaw"], cap["pitch"], cap.get("roll", 0.0))
    b_cur = _basis_matrix(cur["yaw"], cur["pitch"], cur.get("roll", 0.0))
    r = _matmul([[b_cur[j][i] for j in range(3)] for i in range(3)], b_cap)  # B_cur^T B_cap
    return _matmul(_matmul(_k(cur["width"], cur["height"], cur["hfov"]), r), _k_inv(cap["width"], cap["height"], cap["hfov"]))


def warp_points(h: list[list[float]], pts: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    """Apply a homography to pixels. None if any point ends up behind the current camera (no honest outline then)."""
    out = []
    for u, v in pts:
        x = h[0][0] * u + h[0][1] * v + h[0][2]
        y = h[1][0] * u + h[1][1] * v + h[1][2]
        w = h[2][0] * u + h[2][1] * v + h[2][2]
        if w <= 1e-6:
            return None
        out.append((x / w, y / w))
    return out


def footprint_outline(ext, cur: dict, per_edge: int = 4) -> list[tuple[float, float]] | None:
    """Outline of an object's angular footprint `ext` (yaw0, dyaw_lo, dyaw_hi, pitch_lo, pitch_hi) in the current view:
    the rectangle's edges sampled `per_edge` times each, then projected. None if any sample is behind the camera."""
    yaw0, lo, hi, plo, phi = ext
    n = max(1, per_edge)
    bottom = [(lo + (hi - lo) * i / n, plo) for i in range(n)]
    right = [(hi, plo + (phi - plo) * i / n) for i in range(n)]
    top = [(hi - (hi - lo) * i / n, phi) for i in range(n)]
    left = [(lo, phi - (phi - plo) * i / n) for i in range(n)]
    edge = bottom + right + top + left  # once around the rectangle, so the outline is a simple polygon
    out = []
    for dy, p in edge:
        px = direction_to_pixel(yaw0 + dy, p, cur["width"], cur["height"], cur["yaw"], cur["pitch"], cur.get("roll", 0.0), cur["hfov"])
        if px is None:
            return None
        out.append(px)
    return out
