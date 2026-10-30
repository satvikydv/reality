"""A synthetic room for the alignment tests: a textured panorama that a camera at a fixed spot looks around in.

`render_view` returns the JPEG a phone standing at the centre would take when its camera points at (yaw, pitch, roll), with the same pose
conventions as app.anchoring. Frames made this way agree with their metadata exactly, so a known shift of one scan's yaw metadata must be
recovered from the pictures alone. A view from a different spot cannot be rendered (no depth): translation is covered by the manual protocol.
"""
import cv2
import numpy as np

from app import align as _align
from app import anchoring as A

PANO_W, PANO_H = 2880, 1440

# an alignment result as app.align gives it for two scans that line up well (the same room, no shift); seed it with wm.set_alignment(a, b, GOOD)
GOOD = {"version": _align.ALIGN_VERSION, "method": "pose prior", "quality": "good", "offset_deg": 0.0, "pitch_offset_deg": 0.0, "roll_offset_deg": 0.0, "inliers": 500, "pairs": 6,
        "spread_deg": 0.3, "offset_sem_deg": 0.1, "translation": None, "parallax_deg": 0.2, "reason": None, "frames_a": 6, "frames_b": 6, "pairs_tried": 12}


def make_panorama(seed=1, shapes=900):
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 200, (PANO_H // 8, PANO_W // 8, 3), dtype=np.uint8)
    img = cv2.resize(img, (PANO_W, PANO_H), interpolation=cv2.INTER_CUBIC)
    for _ in range(shapes):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, PANO_W)), int(rng.integers(PANO_H // 4, 3 * PANO_H // 4))
        if rng.random() < 0.5:
            cv2.ellipse(img, (x, y), (int(rng.integers(6, 40)), int(rng.integers(6, 40))), float(rng.integers(0, 180)), 0, 360, c, -1)
        else:
            w, h = int(rng.integers(8, 60)), int(rng.integers(8, 60))
            cv2.rectangle(img, (x, y), (x + w, y + h), c, -1)
    return cv2.GaussianBlur(img, (3, 3), 0)


def render_view(pano, yaw, pitch=0.0, roll=0.0, width=480, height=640, hfov=60.0, quality=92):
    f = A.focal_px(width, hfov)
    right, up, fwd = (np.array(v) for v in A.camera_basis(yaw, pitch, roll))
    u, v = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    x, y = (u - width / 2.0) / f, -(v - height / 2.0) / f
    ray = x[..., None] * right + y[..., None] * up + fwd
    ray /= np.linalg.norm(ray, axis=2, keepdims=True)
    yaw_w = np.degrees(np.arctan2(ray[..., 0], ray[..., 1])) % 360.0
    pitch_w = np.degrees(np.arcsin(np.clip(ray[..., 2], -1, 1)))
    mx = (yaw_w / 360.0 * PANO_W).astype(np.float32)
    my = ((90.0 - pitch_w) / 180.0 * PANO_H).astype(np.float32)
    out = cv2.remap(pano, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def add_scan(wm, room_id, pano, yaws, *, pitch=0.0, meta_yaw_shift=0.0, meta_pitch_shift=0.0, true_jitter=(0.0, 0.0), ts=1.0, width=480, height=640,
             hfov=60.0, status="saved", order=None, objects=(), noise_deg=0.0, noise_seed=0):
    """A scan of the panorama: a frame per true yaw (plus a small jitter), whose stored pose is the true one plus a shift (another session's
    reference frame). `objects` = [(label, true_yaw, true_pitch)] seen straight on in a frame centred on them, at box (220,300,260,340)."""
    sid = wm.start_scan(ts=ts, room_id=room_id, status=status)
    rng = np.random.default_rng(noise_seed)
    noise = lambda: float(rng.normal(0.0, noise_deg)) if noise_deg else 0.0   # sensor noise: each stored pose is a little off its true direction
    frames = [(y + true_jitter[0], pitch + true_jitter[1]) for y in yaws]
    for i in (order if order is not None else range(len(frames))):
        ty, tp = frames[i]
        jpeg = render_view(pano, ty, tp, 0.0, width, height, hfov)
        wm.add_frame(sid, ts + i, (ty + meta_yaw_shift + noise()) % 360.0, tp + meta_pitch_shift + noise() * 0.3, width, height, roll=0.0, screen_angle=0.0, hfov_deg=hfov, jpeg=jpeg)
    for label, oy, op in objects:
        jpeg = render_view(pano, oy, op, 0.0, width, height, hfov)
        fid = wm.add_frame(sid, ts + 100, (oy + meta_yaw_shift) % 360.0, op + meta_pitch_shift, width, height, roll=0.0, screen_angle=0.0, hfov_deg=hfov, jpeg=jpeg)
        wm.observe(fid, [{"label": label, "box": [220.0, 300.0, 260.0, 340.0]}])
    wm.end_scan(sid, ts + 200)
    return sid
