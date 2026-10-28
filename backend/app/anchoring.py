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

import math

DEFAULT_HFOV_DEG = 60.0  # horizontal field of view across the image width as sent [VERIFY: calibrate]


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
