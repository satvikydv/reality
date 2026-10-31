"""A synthetic room for the alignment tests: a textured panorama that a camera at a fixed spot looks around in.

`render_view` returns the JPEG a phone standing at the centre would take when its camera points at (yaw, pitch, roll), with the same pose
conventions as app.anchoring. Frames made this way agree with their metadata exactly, so a known shift of one scan's yaw metadata must be
recovered from the pictures alone. The panorama has no depth, so a view from a different spot cannot be rendered with it: `DeskScene` below
is a small world with depth (a desk 40 to 60 cm from the phone, a wall, objects, a laptop screen) for the near field, where a few centimetres
of hand drift cause real parallax.
"""
import math

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
             hfov=60.0, status="saved", order=None, objects=(), noise_deg=0.0, noise_seed=0, sightings=2):
    """A scan of the panorama: a frame per true yaw (plus a small jitter), whose stored pose is the true one plus a shift (another session's
    reference frame). `objects` = [(label, true_yaw, true_pitch)] seen straight on in a frame centred on them, at box (220,300,260,340), and
    (with `sightings` 2, the default) once more from a frame turned 4 degrees, so each object is seen in two frames as a real scan would."""
    sid = wm.start_scan(ts=ts, room_id=room_id, status=status)
    rng = np.random.default_rng(noise_seed)
    noise = lambda: float(rng.normal(0.0, noise_deg)) if noise_deg else 0.0   # sensor noise: each stored pose is a little off its true direction
    frames = [(y + true_jitter[0], pitch + true_jitter[1]) for y in yaws]
    for i in (order if order is not None else range(len(frames))):
        ty, tp = frames[i]
        jpeg = render_view(pano, ty, tp, 0.0, width, height, hfov)
        wm.add_frame(sid, ts + i, (ty + meta_yaw_shift + noise()) % 360.0, tp + meta_pitch_shift + noise() * 0.3, width, height, roll=0.0, screen_angle=0.0, hfov_deg=hfov, jpeg=jpeg)
    for label, oy, op in objects:
        for k in range(sightings):
            cy = oy + 4.0 * k                                                      # the camera's true yaw; the object stays at (oy, op)
            jpeg = render_view(pano, cy, op, 0.0, width, height, hfov)
            u, v = A.direction_to_pixel(oy, op, width, height, cy, op, 0.0, hfov)
            box = [u - 20.0 * width / 480.0, v - 20.0 * height / 640.0, u + 20.0 * width / 480.0, v + 20.0 * height / 640.0] if k else [220.0, 300.0, 260.0, 340.0]
            fid = wm.add_frame(sid, ts + 100 + k, (cy + meta_yaw_shift) % 360.0, op + meta_pitch_shift, width, height, roll=0.0, screen_angle=0.0, hfov_deg=hfov, jpeg=jpeg)
            wm.observe(fid, [{"label": label, "box": box}])
    wm.end_scan(sid, ts + 200)
    return sid


# ================================================================================================ a scene with depth (the near field)
#
# The panorama above has no depth: every view is from one point, so a camera that moves renders nothing new. Real desk scans are made
# 40 to 60 cm from the objects with a hand that drifts a few centimetres between shots, and at that range a few centimetres shift things by
# about ten degrees in the picture while the wall behind barely moves. `DeskScene` is a small ray-cast world (planes, boxes and a distant
# panorama, all in centimetres, east-north-up, the phone near the origin) that renders a camera at any position, so that parallax is real.

def _texture(rng, w, h, base, shapes, lo=10, hi=60):
    img = np.empty((h, w, 3), np.uint8)
    img[:] = base
    noise = rng.integers(-28, 28, (max(2, h // 6), max(2, w // 6), 3))
    img = np.clip(img.astype(int) + cv2.resize(noise.astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC), 0, 255).astype(np.uint8)
    for _ in range(shapes):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.5:
            cv2.ellipse(img, (x, y), (int(rng.integers(lo, hi)) // 2, int(rng.integers(lo, hi)) // 2), float(rng.integers(0, 180)), 0, 360, c, -1)
        else:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(lo, hi)), y + int(rng.integers(lo, hi))), c, -1)
    return cv2.GaussianBlur(img, (3, 3), 0)


class Quad:
    """A textured rectangle: corner `p`, orthogonal edges `e1` (texture x) and `e2` (texture y), owned by an object (or the scene)."""

    def __init__(self, p, e1, e2, tex, owner=None, shade=1.0, live=False):
        self.p, self.e1, self.e2 = (np.asarray(v, np.float32) for v in (p, e1, e2))
        self.tex, self.owner, self.shade, self.live = tex, owner, shade, live
        self.n = np.cross(self.e1, self.e2)


def _box(x, y, z, w, d, h, tex_top, tex_side, owner):
    """A box standing at (x, y) (its centre) on height z: the top and four sides."""
    x0, y0 = x - w / 2.0, y - d / 2.0
    return [Quad((x0, y0, z + h), (w, 0, 0), (0, d, 0), tex_top, owner, 1.0),
            Quad((x0, y0, z), (w, 0, 0), (0, 0, h), tex_side, owner, 0.80),
            Quad((x0, y0 + d, z), (w, 0, 0), (0, 0, h), tex_side, owner, 0.70),
            Quad((x0, y0, z), (0, d, 0), (0, 0, h), tex_side, owner, 0.88),
            Quad((x0 + w, y0, z), (0, d, 0), (0, 0, h), tex_side, owner, 0.76)]


# label: (east, north, width, depth, height) in cm, on the desk top. Sizes follow app.scale.DEFAULT_SIZE_CM so distances come out right.
DESK_OBJECTS = {
    "phone": (-30.0, 34.0, 7.0, 15.0, 1.0),
    "water bottle": (-44.0, 62.0, 7.0, 7.0, 24.0),
    "cup": (45.0, 48.0, 8.0, 8.0, 10.0),
    "mouse": (28.0, 30.0, 6.0, 11.0, 3.5),
    "notebook": (-8.0, 22.0, 25.0, 18.0, 1.5),
}
DESK_Z = -35.0          # the desk top, 35 cm below the phone


class DeskScene:
    """A desk seen from about 35 cm above it: a textured desk top reaching a wall 1.05 m away, objects on the desk, a laptop whose screen can show
    different content each time it is rendered, and the room panorama at 3.5 m beyond. `objects` overrides DESK_OBJECTS (to move one)."""

    def __init__(self, seed=3, objects=None, pano=None, detail=1.0):
        rng = np.random.default_rng(seed)
        self.pano = pano if pano is not None else make_panorama(seed=seed + 10, shapes=int(900 * detail))
        self.objects = dict(DESK_OBJECTS if objects is None else objects)
        n = lambda k: max(1, int(k * detail))                          # `detail` scales how much texture there is (1: busy, 0.1: a plain desk)
        self.quads = [Quad((-90, -5, DESK_Z), (180, 0, 0), (0, 110, 0), _texture(rng, 900, 500, (120, 92, 70), n(260), 6, 30)),
                      Quad((-160, 110, DESK_Z - 40), (320, 0, 0), (0, 0, 220), _texture(rng, 960, 660, (170, 170, 160), n(220), 10, 60))]
        texs = {}
        for label in self.objects:
            texs[label] = (_texture(rng, 200, 200, tuple(int(v) for v in rng.integers(40, 220, 3)), n(30), 8, 40),
                           _texture(rng, 200, 120, tuple(int(v) for v in rng.integers(40, 220, 3)), n(20), 8, 40))
        for label, (x, y, w, d, h) in self.objects.items():
            self.quads += _box(x, y, DESK_Z, w, d, h, *texs[label], owner=label)
        # the laptop: a flat base and a screen leaning back at its far edge; the screen's content is "live" (re-drawn per frame)
        lx, ly = 0.0, 52.0
        self.quads += _box(lx, ly, DESK_Z, 34.0, 24.0, 2.0, _texture(rng, 300, 200, (90, 90, 95), n(40), 4, 20), _texture(rng, 200, 60, (70, 70, 75), n(10), 3, 10), "laptop")
        tilt = math.radians(15.0)
        self.quads.append(Quad((lx - 17.0, ly + 12.0, DESK_Z + 2.0), (34.0, 0, 0), (0, 22.0 * math.sin(tilt), 22.0 * math.cos(tilt)), None, "laptop", 1.0, live=True))
        self.seed = seed

    def render(self, pos, yaw, pitch=0.0, roll=0.0, width=540, height=960, hfov=60.0, quality=90, screen_seed=0, gain=1.0, noise=2.0, blur=0.0,
               with_owner=False):
        """The JPEG a camera at `pos` (cm) pointing at (yaw, pitch, roll) takes; with `with_owner`, also a map of which object each pixel shows."""
        f = A.focal_px(width, hfov)
        right, up, fwd = (np.array(v, np.float32) for v in A.camera_basis(yaw, pitch, roll))
        u, v = np.meshgrid(np.arange(width, dtype=np.float32) + 0.5, np.arange(height, dtype=np.float32) + 0.5)
        x, y = (u - width / 2.0) / f, -(v - height / 2.0) / f
        d = x[..., None] * right + y[..., None] * up + fwd
        d /= np.linalg.norm(d, axis=2, keepdims=True)
        c = np.asarray(pos, np.float32)
        # the room beyond: a sphere of 350 cm around the origin, sampled from the panorama by direction
        r_far = 350.0
        b = d @ c
        t_far = -b + np.sqrt(np.maximum(b * b - (c @ c - r_far * r_far), 0.0))
        hit = c + t_far[..., None] * d
        yaw_w = np.degrees(np.arctan2(hit[..., 0], hit[..., 1])) % 360.0
        pitch_w = np.degrees(np.arcsin(np.clip(hit[..., 2] / r_far, -1, 1)))
        out = cv2.remap(self.pano, (yaw_w / 360.0 * PANO_W).astype(np.float32), ((90.0 - pitch_w) / 180.0 * PANO_H).astype(np.float32),
                        cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP).astype(np.float32)
        zbuf = t_far.copy()
        owner = np.full((height, width), -1, np.int16)
        names = list(self.objects) + ["laptop"]
        srng = np.random.default_rng(1000 + screen_seed)
        for q in self.quads:
            win = self._window(q, c, right, up, fwd, f, width, height)
            if win is None:
                continue
            y0, y1, x0, x1 = win
            dd = d[y0:y1, x0:x1]
            denom = dd @ q.n
            with np.errstate(divide="ignore", invalid="ignore"):
                t = ((q.p - c) @ q.n) / denom
            ok = np.isfinite(t) & (t > 1.0) & (t < zbuf[y0:y1, x0:x1])
            if not ok.any():
                continue
            local = c + t[..., None] * dd - q.p
            s1 = (local @ q.e1) / (q.e1 @ q.e1)
            s2 = (local @ q.e2) / (q.e2 @ q.e2)
            ok &= (s1 >= 0) & (s1 <= 1) & (s2 >= 0) & (s2 <= 1)
            if not ok.any():
                continue
            tex = q.tex if not q.live else _texture(srng, 340, 220, tuple(int(v) for v in srng.integers(30, 230, 3)), 60, 6, 50)
            th, tw = tex.shape[:2]
            img = cv2.remap(tex, (s1 * (tw - 1)).astype(np.float32), ((1.0 - s2) * (th - 1)).astype(np.float32), cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REPLICATE).astype(np.float32) * q.shade
            out[y0:y1, x0:x1][ok] = img[ok]
            zbuf[y0:y1, x0:x1][ok] = t[ok]
            owner[y0:y1, x0:x1][ok] = names.index(q.owner) if q.owner else -1
        if blur > 0:                                                    # a little motion or focus blur
            out = cv2.GaussianBlur(out, (0, 0), blur)
        out = out * gain + np.random.default_rng(screen_seed + 7).normal(0.0, noise, out.shape)
        ok, buf = cv2.imencode(".jpg", np.clip(out, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not with_owner:
            return buf.tobytes()
        return buf.tobytes(), {n: owner == i for i, n in enumerate(names)}

    @staticmethod
    def _window(q, c, right, up, fwd, f, width, height):
        """The pixel rectangle a quad can cover (rows y0:y1, columns x0:x1), or None when it is wholly behind the camera or off screen.
        A quad that crosses the camera's plane gets the whole image."""
        corners = np.array([q.p, q.p + q.e1, q.p + q.e2, q.p + q.e1 + q.e2]) - c
        z = corners @ fwd
        if (z <= 1.0).all():
            return None
        if (z <= 1.0).any():
            return 0, height, 0, width
        u = width / 2.0 + f * (corners @ right) / z
        v = height / 2.0 - f * (corners @ up) / z
        x0, x1 = max(0, int(u.min()) - 2), min(width, int(u.max()) + 3)
        y0, y1 = max(0, int(v.min()) - 2), min(height, int(v.max()) + 3)
        return None if x0 >= x1 or y0 >= y1 else (y0, y1, x0, x1)

    def direction(self, label, pos=(0.0, 0.0, 0.0)):
        """True (yaw, pitch) of an object's centre seen from `pos`."""
        if label == "laptop":
            x, y, z = 0.0, 52.0, DESK_Z + 6.0
        else:
            x, y, _, _, h = self.objects[label]
            z = DESK_Z + h / 2.0
        v = np.array([x, y, z]) - np.asarray(pos, np.float64)
        return float(math.degrees(math.atan2(v[0], v[1])) % 360.0), float(math.degrees(math.asin(v[2] / np.linalg.norm(v))))


def add_desk_scan(wm, room_id, scene, *, yaws=(-34.0, -12.0, 11.0, 33.0), pitch=-40.0, base=(0.0, 0.0, 0.0), drift_cm=3.0, meta_yaw_shift=0.0,
                  meta_pitch_shift=0.0, pose_noise_deg=1.5, pitch_jitter=3.0, ts=1.0, seed=0, width=540, height=960, hfov=60.0, gain=1.0,
                  status="saved", min_px=150, true_hfov=None, blur=0.0, truth=None):
    """A near-field scan of `scene`: one frame per yaw, the phone at `base` plus a hand drift of about `drift_cm` per frame (each axis), its
    true pitch around `pitch`. The stored pose is the true one plus a per-scan offset (`meta_yaw_shift`, `meta_pitch_shift`: another session's
    frame, a tilt bias) and per-frame sensor noise. Every object visible in a frame is detected with the box of its visible pixels, as SAM would.
    `true_hfov` renders with another field of view than the stored `hfov` (an uncalibrated phone). `truth`, a dict, receives each frame's true
    pose and position by frame id."""
    rng = np.random.default_rng(seed)
    sid = wm.start_scan(ts=ts, room_id=room_id, status=status)
    for i, y in enumerate(yaws):
        pos = np.asarray(base, np.float64) + rng.normal(0.0, drift_cm, 3)
        ty, tp = y + float(rng.normal(0.0, 2.0)), pitch + float(rng.normal(0.0, pitch_jitter))
        jpeg, owners = scene.render(pos, ty, tp, 0.0, width, height, true_hfov or hfov, screen_seed=seed * 100 + i, gain=gain, blur=blur, with_owner=True)
        fid = wm.add_frame(sid, ts + i, (ty + meta_yaw_shift + float(rng.normal(0.0, pose_noise_deg))) % 360.0,
                           tp + meta_pitch_shift + float(rng.normal(0.0, pose_noise_deg * 0.5)), width, height, roll=float(rng.normal(0.0, pose_noise_deg * 0.5)),
                           screen_angle=0.0, hfov_deg=hfov, jpeg=jpeg)
        if truth is not None:
            truth[fid] = {"yaw": ty, "pitch": tp, "roll": 0.0, "pos": pos.tolist()}
        dets = []
        for label, m in owners.items():
            if m.sum() >= min_px:
                ys, xs = np.nonzero(m)
                dets.append({"label": label, "box": [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]})
        if dets:
            wm.observe(fid, dets)
    wm.end_scan(sid, ts + 100)
    return sid
