"""Guided retrieval math (F12): from the current camera pose to a stored (yaw, pitch) anchor.

The overlay arrow and the spoken hint come from the target direction expressed in the image's own
axes (right, up, forward), not from raw yaw and pitch differences, so they stay correct when the
phone is rolled or pointed steeply up or down.

Yaw frames: the anchor was stored in the scan's yaw frame. With the relative sensor that frame is
arbitrary per session, so pass `yaw_offset` = live yaw minus scan yaw for the same physical heading
(estimate it with app.changes.estimate_yaw_offset from objects seen again in the live view; 0 with
the absolute source). Translation is unknown: this only works if the user stays roughly where the
scan was taken.
"""
from __future__ import annotations

import math

from app import anchoring as A

ARRIVE_DEG = 6.0
TURN_AROUND_DEG = 110.0
FAR_DEG = 45.0


def guide(target_yaw: float, target_pitch: float, cam_yaw: float, cam_pitch: float, cam_roll: float,
          width: int, height: int, hfov_deg: float = A.DEFAULT_HFOV_DEG, yaw_offset: float = 0.0,
          margin: float = 0.1, arrive_deg: float = ARRIVE_DEG, uncertainty_deg: float = 0.0) -> dict:
    """Guidance from the live camera to the target.

    `cam_roll` is the image roll (A.image_roll: sensor roll plus screen angle). Returns:
      distance_deg  angle between where the camera points and the target
      dyaw, dpitch  signed heading difference (+right, +up), for display only
      arrow_deg     direction of the target in the image, clockwise from up, [0, 360)
      in_view       target projects inside the image (inset by `margin` of each side)
      pixel         (u, v) of the target, or None when it is behind the camera
      arrived       in view and within `arrive_deg`
      cone_deg      half-angle of the arrow's cone: how far the arrow may be off because the stored direction is only
                    known to `uncertainty_deg` (asin(u / distance); 90 when the target is closer than its uncertainty)
      instruction   short spoken hint
    """
    t_yaw = (target_yaw + yaw_offset) % 360.0
    right, up, fwd = A.camera_basis(cam_yaw, cam_pitch, cam_roll)
    d = A.direction_vector(t_yaw, target_pitch)
    x, y = A.dot(d, right), A.dot(d, up)
    arrow = math.degrees(math.atan2(x, y)) % 360.0 if math.hypot(x, y) > 1e-9 else 90.0
    px = A.direction_to_pixel(t_yaw, target_pitch, width, height, cam_yaw, cam_pitch, cam_roll, hfov_deg)
    in_view = px is not None and width * margin <= px[0] <= width * (1 - margin) and height * margin <= px[1] <= height * (1 - margin)
    distance = A.angular_distance(cam_yaw, cam_pitch, t_yaw, target_pitch)
    arrived = in_view and distance <= arrive_deg
    return {"distance_deg": distance, "dyaw": A.wrap180(t_yaw - cam_yaw), "dpitch": target_pitch - cam_pitch,
            "arrow_deg": arrow, "in_view": in_view, "pixel": px, "arrived": arrived, "cone_deg": cone_deg(distance, uncertainty_deg),
            "instruction": instruction(distance, x, y, in_view, arrived)}


def cone_deg(distance_deg: float, uncertainty_deg: float) -> float:
    """Half-angle, in degrees, of the cone the arrow could really point into when the target direction is uncertain by
    `uncertainty_deg`. Narrow for a far target, wide for a near one, and the whole half-turn once it is as close as the error."""
    if uncertainty_deg <= 0:
        return 0.0
    if distance_deg <= uncertainty_deg:
        return 90.0
    return math.degrees(math.asin(uncertainty_deg / distance_deg))


def instruction(distance_deg: float, x: float, y: float, in_view: bool, arrived: bool) -> str:
    """Spoken hint. (x, y) is the target direction in image axes (+x right, +y up)."""
    if arrived:
        return "That's it."
    if distance_deg > TURN_AROUND_DEG:
        return "Turn around."
    if abs(x) >= abs(y):
        word, verb = ("right" if x > 0 else "left"), "Turn"
    else:
        word, verb = ("up" if y > 0 else "down"), "Tilt"
    if in_view:
        return f"Almost there, a little {word}."
    return f"{verb} {word}." if distance_deg >= FAR_DEG else f"A bit more {word}."
