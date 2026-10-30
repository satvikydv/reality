"""Viewpoint-robust alignment of two scans of one room, from their images.

Two scans of a room are made at different times, often from a slightly different spot and with a different start heading (a phone's
relative sensor has an arbitrary yaw zero per session). Matching objects by direction alone then confuses a moved scan with moved objects.
This module recovers how the two scans' reference frames differ from the pictures themselves:

  1. For each frame of scan B, candidate frames of scan A are picked by a pose prior (the viewing directions should roughly agree, with
     an assumed offset between the frames, 0 by default). If that finds nothing, a coarse search over frame pairs finds a first offset
     and the prior is refined from it.
  2. For a pair: SIFT features (OpenCV), Lowe ratio test with a mutual check, then two models fitted with RANSAC: a pure rotation (own
     implementation, bearing vectors and a Kabsch fit) and the essential matrix with the calibrated intrinsics (OpenCV). The rotation
     model wins unless the essential matrix explains clearly more matches, which is what a real change of viewpoint looks like; only
     then is a translation direction reported. Boxes of detected objects are masked out, so things that moved do not pull the estimate.
  3. The pair's rotation R (camera B to camera A) and the two frames' own poses give the offset between the scans' reference frames:
        D = Basis_A · S · R · S · Basis_Bᵀ        (S flips the image y axis: pixels run down, the pose basis runs up)
     which maps a direction in B's frame into A's frame. It is reported as a yaw / pitch / roll offset, with the yaw in the convention
     of app.changes (B minus A: a direction at yaw y in A has yaw y + offset in B).
  4. The pair estimates are combined with an inlier-weighted median (the yaw on the circle), after dropping pairs far from the median.
     The result carries the inlier count, the spread and a quality: good, weak or failed.

Everything is CPU and uses only OpenCV and NumPy.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass

import cv2
import numpy as np

from app import anchoring as A

MAX_SIDE = 640          # frames are matched at this size (longest side); points are mapped back to frame pixels
N_FEATURES = 900
RATIO = 0.75            # Lowe ratio test
MIN_MATCHES = 16        # fewer matches than this: no estimate for the pair
MIN_PAIR_INLIERS = 20   # a pair below this is not used
ROT_THRESH_DEG = 0.5    # a match agrees with a rotation when the rays differ by less than this
EPI_THRESH_FRAC = 0.0023  # epipolar threshold as a share of the frame width (1.5 px at 640)
E_PREFERRED = 1.15      # the essential matrix must explain this many times the rotation model's matches to be preferred
ALIGN_VERSION = 2       # bump when results change meaning: stored alignments of an older version are recomputed
# Each pair's offset carries the sensor noise of both frames' poses (a phone's orientation is good to a few degrees), so pairs scatter by a
# few degrees even when the alignment is right. What matters is how well the median is known: its standard error.
GOOD_PAIRS, GOOD_INLIERS, GOOD_SEM_DEG, MAX_SPREAD_DEG = 3, 100, 1.5, 8.0
TRANSLATION_CONSISTENT = 0.7   # share of the unit translation vectors that agree (their mean resultant length) before the direction is believed
PRIOR_WINDOW_DEG = 70.0
PER_FRAME = 3           # candidate A frames per B frame in the pose-prior pass
SEARCH_B, SEARCH_A = 12, 24   # frames sampled from each scan by the coarse search
TIME_BUDGET_S = 25.0
S = np.diag([1.0, -1.0, 1.0])


# ------------------------------------------------------------------------------------------------ features

@dataclass
class Feat:
    pts: np.ndarray        # (N, 2) float32, pixels of the frame
    desc: np.ndarray       # (N, 128) float32
    width: int
    height: int


def extract(jpeg: bytes | None, boxes: list | None = None, n_features: int = N_FEATURES) -> Feat | None:
    """SIFT features of a frame, none inside `boxes` (detected objects, padded). None when the frame is unusable or has too little texture."""
    if not jpeg:
        return None
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h0, w0 = img.shape
    s = min(1.0, MAX_SIDE / max(h0, w0))
    if s < 1.0:
        img = cv2.resize(img, (round(w0 * s), round(h0 * s)), interpolation=cv2.INTER_AREA)
    mask = None
    if boxes:
        mask = np.full(img.shape, 255, np.uint8)
        for b in boxes:
            pw, ph = 0.12 * (b[2] - b[0]), 0.12 * (b[3] - b[1])
            x0, y0 = max(0, int((b[0] - pw) * s)), max(0, int((b[1] - ph) * s))
            x1, y1 = int((b[2] + pw) * s) + 1, int((b[3] + ph) * s) + 1
            mask[y0:y1, x0:x1] = 0
    kps, des = cv2.SIFT_create(nfeatures=n_features).detectAndCompute(img, mask)
    if des is None or len(kps) < MIN_MATCHES:
        return None
    return Feat(np.float32([k.pt for k in kps]) / s, des.astype(np.float32), w0, h0)


def match(fb: Feat, fa: Feat) -> tuple[np.ndarray, np.ndarray]:
    """Matched points (pb, pa) between B and A: ratio test, and the best match must be mutual."""
    bf = cv2.BFMatcher(cv2.NORM_L2)
    fwd = bf.knnMatch(fb.desc, fa.desc, k=2)
    back = bf.match(fa.desc, fb.desc)
    best_back = {m.queryIdx: m.trainIdx for m in back}
    ib, ia = [], []
    for pair in fwd:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < RATIO * n.distance and best_back.get(m.trainIdx) == m.queryIdx:
            ib.append(m.queryIdx)
            ia.append(m.trainIdx)
    return fb.pts[ib], fa.pts[ia]


# ------------------------------------------------------------------------------------------------ one pair

def intrinsics(width: int, height: int, hfov_deg: float) -> np.ndarray:
    f = A.focal_px(width, hfov_deg)
    return np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])


def bearings(pts: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Unit rays (OpenCV camera: x right, y down, z forward) through pixels."""
    x = np.column_stack([(pts[:, 0] - k[0, 2]) / k[0, 0], (pts[:, 1] - k[1, 2]) / k[1, 1], np.ones(len(pts))])
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def kabsch(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """The rotation R that best maps rays p onto rays q (least squares): R p ≈ q."""
    u, _, vt = np.linalg.svd(p.T @ q)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1.0, 1.0, d]) @ u.T


def rotation_ransac(rb: np.ndarray, ra: np.ndarray, iters: int = 300, thresh_deg: float = ROT_THRESH_DEG, seed: int = 7):
    """Pure-rotation model: R with R·rb ≈ ra for as many matches as possible. Returns (R, inlier mask) or None."""
    n = len(rb)
    if n < 3:
        return None
    rng = np.random.default_rng(seed)
    cos_t = math.cos(math.radians(thresh_deg))
    best, best_mask = None, None
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        r = kabsch(rb[idx], ra[idx])
        mask = np.einsum("ij,ij->i", rb @ r.T, ra) > cos_t
        if best_mask is None or mask.sum() > best_mask.sum():
            best, best_mask = r, mask
    for _ in range(3):  # refit on the inliers, then re-count
        if best_mask.sum() < 3:
            return None
        best = kabsch(rb[best_mask], ra[best_mask])
        best_mask = np.einsum("ij,ij->i", rb @ best.T, ra) > cos_t
    return best, best_mask


@dataclass
class PairEstimate:
    r: np.ndarray                 # camera B to camera A (OpenCV axes)
    inliers: int
    matches: int
    model: str                    # "rotation" or "essential"
    t: np.ndarray | None          # unit direction of camera B's centre as seen from camera A, in A's camera axes (essential model only)
    parallax_deg: float           # how far matched features sit from where the rotation alone puts them (median, inliers): size of the viewpoint change
    f: float


def estimate_pair(fb: Feat, fa: Feat, hfov_b: float, hfov_a: float) -> PairEstimate | None:
    pb, pa = match(fb, fa)
    if len(pb) < MIN_MATCHES:
        return None
    kb, ka = intrinsics(fb.width, fb.height, hfov_b), intrinsics(fa.width, fa.height, hfov_a)
    rb, ra = bearings(pb, kb), bearings(pa, ka)
    rot = rotation_ransac(rb, ra)
    rot_n = int(rot[1].sum()) if rot else 0
    ess = None
    if len(pb) >= 8 and abs(kb[0, 0] - ka[0, 0]) < 1e-6 * ka[0, 0] and fb.width == fa.width:
        try:
            thr = max(0.8, EPI_THRESH_FRAC * fa.width)
            e, mask = cv2.findEssentialMat(pb.astype(np.float64), pa.astype(np.float64), ka, method=cv2.RANSAC, prob=0.999, threshold=thr)
            if e is not None and e.shape[0] >= 3:
                n, r, t, mask2 = cv2.recoverPose(e[:3], pb.astype(np.float64), pa.astype(np.float64), ka, mask=mask)
                if n >= MIN_PAIR_INLIERS:
                    ess = (r, t.reshape(3), mask2.reshape(-1) > 0, int(n))
        except cv2.error:
            ess = None
    if rot is None and ess is None:
        return None
    if ess is not None and (rot is None or ess[3] >= E_PREFERRED * rot_n):
        r, t, mask, n = ess[0], ess[1] / (np.linalg.norm(ess[1]) or 1.0), ess[2], ess[3]
        model = "essential"
    else:
        r, mask, t, n, model = rot[0], rot[1], None, rot_n, "rotation"
    if n < MIN_PAIR_INLIERS:
        return None
    pred = (ka @ r @ np.linalg.inv(kb) @ np.column_stack([pb[mask], np.ones(int(mask.sum()))]).T).T
    pred = pred[:, :2] / pred[:, 2:3]
    resid = np.linalg.norm(pred - pa[mask], axis=1)
    return PairEstimate(r, n, len(pb), model, t, math.degrees(math.atan(float(np.median(resid)) / ka[0, 0])), float(ka[0, 0]))


def basis(frame: dict) -> np.ndarray:
    """Columns (right, up, forward) of a frame's pose in earth coordinates, from its own metadata."""
    r, u, f = A.camera_basis(frame["yaw"], frame["pitch"], A.image_roll(frame.get("roll"), frame.get("screen_angle")))
    return np.array([[r[i], u[i], f[i]] for i in range(3)])


def frame_offset(est: PairEstimate, fa: dict, fb: dict) -> np.ndarray:
    """D: maps a direction in scan B's reference frame to scan A's, from one pair: Basis_A · S · R · S · Basis_Bᵀ."""
    return basis(fa) @ S @ est.r @ S @ basis(fb).T


def rot_to_offsets(d: np.ndarray) -> tuple[float, float, float]:
    """(yaw, pitch, roll) in degrees of D, read off where it sends north and east. Yaw is A minus B."""
    north, east = d @ np.array([0.0, 1.0, 0.0]), d @ np.array([1.0, 0.0, 0.0])
    return (math.degrees(math.atan2(north[0], north[1])), math.degrees(math.asin(max(-1.0, min(1.0, north[2])))),
            math.degrees(math.asin(max(-1.0, min(1.0, east[2])))))


# ------------------------------------------------------------------------------------------------ combining

def wrap180(x):
    return (x + 180.0) % 360.0 - 180.0


def weighted_median(values: list[float], weights: list[float]) -> float:
    order = sorted(range(len(values)), key=lambda i: values[i])
    half, acc = sum(weights) / 2.0, 0.0
    for i in order:
        acc += weights[i]
        if acc >= half:
            return values[i]
    return values[order[-1]]


def circular_mean(angles: list[float], weights: list[float]) -> float:
    s = sum(w * math.sin(math.radians(a)) for a, w in zip(angles, weights))
    c = sum(w * math.cos(math.radians(a)) for a, w in zip(angles, weights))
    return math.degrees(math.atan2(s, c))


def combine(estimates: list[dict]) -> dict | None:
    """Inlier-weighted median of per-pair offsets. Each estimate: {yaw, pitch, roll, w}. Pairs far from the median are dropped once."""
    if not estimates:
        return None
    use = estimates
    for _ in range(2):
        w = [e["w"] for e in use]
        m0 = circular_mean([e["yaw"] for e in use], w)
        dev = [wrap180(e["yaw"] - m0) for e in use]
        med = weighted_median(dev, w)
        mad = weighted_median([abs(d - med) for d in dev], w) * 1.4826
        keep = [e for e, d in zip(use, dev) if abs(d - med) <= max(3.0, 3.0 * mad)]
        if len(keep) == len(use) or not keep:
            break
        use = keep
    w = [e["w"] for e in use]
    m0 = circular_mean([e["yaw"] for e in use], w)
    dev = [wrap180(e["yaw"] - m0) for e in use]
    med = weighted_median(dev, w)
    spread = weighted_median([abs(d - med) for d in dev], w) * 1.4826
    sem = 1.2533 * spread / math.sqrt(len(use))        # standard error of a median of n roughly normal values
    return {"yaw": wrap180(m0 + med), "pitch": weighted_median([e["pitch"] for e in use], w), "roll": weighted_median([e["roll"] for e in use], w),
            "spread": spread, "sem": sem, "used": use}


def quality_of(pairs: int, inliers: int, sem: float, spread: float = 0.0) -> str:
    if pairs == 0:
        return "failed"
    return "good" if pairs >= GOOD_PAIRS and inliers >= GOOD_INLIERS and sem <= GOOD_SEM_DEG and spread <= MAX_SPREAD_DEG else "weak"


# ------------------------------------------------------------------------------------------------ two scans

def _view_dir(f: dict, prior_offset: float) -> tuple[float, float]:
    """Where a frame of scan B looks in scan A's frame under the assumed offset (A = B minus offset)."""
    return (f["yaw"] - prior_offset) % 360.0, f["pitch"]


def candidates(fb: dict, frames_a: list[dict], prior_offset: float, window: float, n: int) -> list[dict]:
    yb, pb = _view_dir(fb, prior_offset)
    scored = sorted(((A.angular_distance(yb, pb, fa["yaw"], fa["pitch"]), fa) for fa in frames_a), key=lambda t: t[0])
    return [fa for d, fa in scored if d <= window][:n]


def _spread(items: list, k: int) -> list:
    """k items taken evenly across a list."""
    if len(items) <= k:
        return list(items)
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)] if k > 1 else items[:1]


class _Run:
    """Features and pair estimates of one alignment, so a frame is decoded once and a pair is estimated once."""

    def __init__(self, wm, scan_a: int, scan_b: int, boxes: bool, deadline: float):
        self.wm, self.deadline = wm, deadline
        self.frames_a, self.frames_b = wm.list_frames(scan_a), wm.list_frames(scan_b)
        self.boxes = {}
        if boxes:
            for sid in (scan_a, scan_b):
                for row in wm.db.execute("SELECT frame_id, box FROM detections WHERE scan_id=?", (sid,)):
                    self.boxes.setdefault(row["frame_id"], []).append(json.loads(row["box"]))
        self.feats: dict[int, Feat | None] = {}
        self.done: dict[tuple[int, int], dict | None] = {}

    def feat(self, f: dict) -> Feat | None:
        if f["id"] not in self.feats:
            self.feats[f["id"]] = extract(self.wm.frame_jpeg(f["id"]), self.boxes.get(f["id"]))
        return self.feats[f["id"]]

    def pair(self, fa: dict, fb: dict) -> dict | None:
        key = (fa["id"], fb["id"])
        if key in self.done:
            return self.done[key]
        out = None
        if time.monotonic() < self.deadline:
            a, b = self.feat(fa), self.feat(fb)
            if a is not None and b is not None:
                est = estimate_pair(b, a, fb["hfov_deg"], fa["hfov_deg"])
                if est is not None:
                    yaw, pitch, roll = rot_to_offsets(frame_offset(est, fa, fb))
                    tw = None if est.t is None else (basis(fa) @ S @ est.t)
                    out = {"a": fa["id"], "b": fb["id"], "yaw": yaw, "pitch": pitch, "roll": roll, "w": float(est.inliers), "inliers": est.inliers,
                           "model": est.model, "parallax_deg": est.parallax_deg, "t_world": None if tw is None else tw.tolist()}
        self.done[key] = out
        return out


def _summary(ests: list[dict], method: str, run: _Run) -> dict:
    base = {"version": ALIGN_VERSION, "method": method, "frames_a": len(run.frames_a), "frames_b": len(run.frames_b), "pairs_tried": sum(1 for _ in run.done)}
    c = combine(ests)
    if c is None:
        return {**base, "quality": "failed", "offset_deg": 0.0, "pitch_offset_deg": 0.0, "roll_offset_deg": 0.0, "inliers": 0, "pairs": 0, "spread_deg": None,
                "offset_sem_deg": None, "translation": None, "parallax_deg": None,
                "reason": "no pair of frames had enough matching texture (blank, blurred or too different views)"}
    used = c["used"]
    inliers = int(sum(e["inliers"] for e in used))
    ts = [(np.array(e["t_world"]), e["w"]) for e in used if e.get("t_world") is not None]
    trans = None
    if ts:
        v = sum(t * w for t, w in ts)
        n = float(np.linalg.norm(v))
        if n > 1e-9:
            consistency = n / sum(w for _, w in ts)        # 1 when every pair gives the same direction, near 0 when they disagree
            v = v / n
            trans = {"east": round(float(v[0]), 3), "north": round(float(v[1]), 3), "up": round(float(v[2]), 3),
                     "yaw_deg": round(math.degrees(math.atan2(v[0], v[1])) % 360.0, 1), "consistency": round(float(consistency), 2),
                     "trusted": bool(consistency >= TRANSLATION_CONSISTENT)}
    q = quality_of(len(used), inliers, c["sem"], c["spread"])
    par = weighted_median([e["parallax_deg"] for e in used], [e["w"] for e in used])
    # the offset is B minus A (the convention of app.changes); the estimate above is A minus B
    return {**base, "quality": q, "offset_deg": round(wrap180(-c["yaw"]), 2), "pitch_offset_deg": round(-c["pitch"], 2), "roll_offset_deg": round(-c["roll"], 2),
            "inliers": inliers, "pairs": len(used), "spread_deg": round(c["spread"], 2), "offset_sem_deg": round(c["sem"], 2), "translation": trans, "parallax_deg": round(par, 2),
            "reason": None if q == "good" else "too few matching pairs, too few matching points, or pairs that disagree too much: the alignment is only a rough guide",
            "pair_estimates": [{"a": e["a"], "b": e["b"], "inliers": e["inliers"], "yaw": round(wrap180(-e["yaw"]), 2), "model": e["model"]} for e in sorted(used, key=lambda e: -e["inliers"])[:20]]}


def align_scans(wm, scan_a: int, scan_b: int, *, prior_offset: float = 0.0, window_deg: float = PRIOR_WINDOW_DEG, per_frame: int = PER_FRAME,
                use_boxes: bool = True, time_budget_s: float = TIME_BUDGET_S) -> dict:
    """How scan B's reference frame differs from scan A's, from the images. `prior_offset` is the offset (B minus A, degrees) the pose prior
    assumes for choosing which frames to compare; 0 when both scans used the compass. See the module docstring for the output."""
    run = _Run(wm, scan_a, scan_b, use_boxes, time.monotonic() + time_budget_s)
    if not run.frames_a or not run.frames_b:
        return {**_summary([], "none", run), "reason": "a scan has no frames"}

    def collect(prior: float, window: float) -> list[dict]:
        out = []
        for fb in run.frames_b:
            for fa in candidates(fb, run.frames_a, prior, window, per_frame):
                p = run.pair(fa, fb)
                if p is not None:
                    out.append(p)
        return out

    def good(items: list[dict]) -> bool:
        c = combine(items)
        return c is not None and quality_of(len(c["used"]), int(sum(e["inliers"] for e in c["used"])), c["sem"], c["spread"]) == "good"

    ests = collect(prior_offset, window_deg)
    method = "pose prior"
    if not good(ests):  # the frames may differ by a heading the prior did not allow for: refine around what was found ...
        c = combine(ests)
        if c is not None:
            ests += collect(wrap180(-c["yaw"]), window_deg)
            method = "pose prior, refined"
        if not good(ests):  # ... and if that is not enough, look at pairs across the whole scans, then refine around the offset they agree on
            method = "search"
            for fb in _spread(run.frames_b, SEARCH_B):
                for fa in _spread(run.frames_a, SEARCH_A):
                    p = run.pair(fa, fb)
                    if p is not None:
                        ests.append(p)
            c = combine(ests)
            if c is not None:
                ests += collect(wrap180(-c["yaw"]), window_deg)
    ests = list({(e["a"], e["b"]): e for e in ests}.values())
    return _summary(ests, method, run)


# ------------------------------------------------------------------------------------------------ live guidance (phone re-photography)

def guidance(est: PairEstimate | None, thresholds: dict | None = None) -> dict:
    """Words and numbers for lining a live camera (B) up with a reference keyframe (A), from one pair estimate.
    turn > 0: turn right; tilt > 0: tilt up; roll > 0: rotate clockwise; move: where to step, as a direction only (the scale is unknown)."""
    th = {"turn": 4.0, "tilt": 4.0, "roll": 4.0, "parallax": 1.5, **(thresholds or {})}
    if est is None:
        return {"ok": False, "within": False, "inliers": 0, "words": "Point the camera at the same view as the faded outline.", "turn_deg": None, "tilt_deg": None,
                "roll_deg": None, "move": None, "parallax_deg": None}
    fwd = est.r.T @ np.array([0.0, 0.0, 1.0])          # the reference's viewing axis, in the live camera's axes
    up = est.r.T @ np.array([0.0, -1.0, 0.0])
    turn = math.degrees(math.atan2(fwd[0], fwd[2]))
    tilt = -math.degrees(math.atan2(fwd[1], math.hypot(fwd[0], fwd[2])))
    roll = math.degrees(math.atan2(up[0], -up[1]))
    move = None
    if est.t is not None:
        v = est.r.T @ (-est.t)                          # from where the live camera is toward where the reference was, in live axes
        ax = {"right": v[0], "left": -v[0], "forward": v[2], "back": -v[2], "down": v[1], "up": -v[1]}
        name, amount = max(ax.items(), key=lambda kv: kv[1])
        if amount > 0.4 and est.parallax_deg > th["parallax"] * 0.5:
            move = name
    within = abs(turn) <= th["turn"] and abs(tilt) <= th["tilt"] and abs(roll) <= th["roll"] and est.parallax_deg <= th["parallax"]
    parts = []
    if abs(turn) > th["turn"]:
        parts.append(f"turn {'right' if turn > 0 else 'left'} {round(abs(turn))} degrees")
    if abs(tilt) > th["tilt"]:
        parts.append(f"tilt {'up' if tilt > 0 else 'down'} {round(abs(tilt))} degrees")
    if abs(roll) > th["roll"]:
        parts.append(f"rotate {'clockwise' if roll > 0 else 'anticlockwise'} {round(abs(roll))} degrees")
    if est.parallax_deg > th["parallax"]:
        parts.append(f"move slightly {move}" if move else "move slightly toward where you first stood")
    words = ", ".join(parts).capitalize() + "." if parts else "Lined up. Start scanning."
    return {"ok": True, "within": within, "inliers": est.inliers, "words": words, "turn_deg": round(turn, 1), "tilt_deg": round(tilt, 1), "roll_deg": round(roll, 1),
            "move": move, "parallax_deg": round(est.parallax_deg, 2)}


class ReferenceProbe:
    """Live comparison of camera frames with one reference keyframe: features of the reference are computed once."""

    def __init__(self, ref_jpeg: bytes, hfov_deg: float, boxes: list | None = None):
        self.hfov = hfov_deg
        self.feat = extract(ref_jpeg, boxes)

    def probe(self, jpeg: bytes, hfov_deg: float | None = None) -> dict:
        if self.feat is None:
            return {**guidance(None), "words": "This room's first scan has too little detail to line up with."}
        live = extract(jpeg, None, n_features=500)
        if live is None:
            return guidance(None)
        if live.width != self.feat.width:  # the live frame is smaller than the stored one: scale the reference's pixels, keep the field of view
            s = live.width / self.feat.width
            ref = Feat(self.feat.pts * s, self.feat.desc, live.width, round(self.feat.height * s))
        else:
            ref = self.feat
        return guidance(estimate_pair(live, ref, hfov_deg or self.hfov, self.hfov))


def edges_png(jpeg: bytes, max_side: int = 640) -> bytes | None:
    """The outline of a keyframe as a transparent PNG (white edges, nothing else), to be laid over the live camera."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    s = min(1.0, max_side / max(img.shape))
    if s < 1.0:
        img = cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    e = cv2.Canny(cv2.GaussianBlur(img, (5, 5), 0), 50, 140)
    e = cv2.dilate(e, np.ones((2, 2), np.uint8))
    out = np.zeros((*e.shape, 4), np.uint8)
    out[..., :3] = 255
    out[..., 3] = e
    ok, buf = cv2.imencode(".png", out)
    return buf.tobytes() if ok else None
