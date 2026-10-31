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
     then is a translation direction reported. Features inside the boxes of detected objects are kept (at a desk the objects are most of
     the picture) but labelled: when one object holds most of a pair's matches, the pair is refitted without it and the object's matches are
     kept only if they agree with the rest of the scene, so a thing that moved cannot pull the estimate.
  3. The pair's rotation R (camera B to camera A) and the two frames' own poses give the offset between the scans' reference frames:
        D = Basis_A · S · R · S · Basis_Bᵀ        (S flips the image y axis: pixels run down, the pose basis runs up)
     which maps a direction in B's frame into A's frame. It is reported as a yaw / pitch / roll offset, with the yaw in the convention
     of app.changes (B minus A: a direction at yaw y in A has yaw y + offset in B).
  4. The pair estimates are combined with an inlier-weighted median (the yaw on the circle), after dropping pairs far from the median.
     The result carries the inlier count, the spread and a quality: good, weak or failed. Pairs that share a frame share that frame's sensor
     error, so the standard error counts distinct frames, not pairs.
  5. Per frame of scan B, its pose expressed in scan A's frame (from the pairs it is in): this removes B's own sensor noise and any tilt bias
     of the whole scan, and is what app.changes uses to re-anchor B's objects.
  6. Local evidence, per detected object and frame pair: where the object should appear in the other scan's frame, predicted from the matched
     static features around it (they sit at about its depth, so they carry the same parallax). Comparing that with where the other scan
     really detected it measures a move in pixels, with no sensor pose and no assumed viewpoint error involved.

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
ALIGN_VERSION = 3       # bump when results change meaning: stored alignments of an older version are recomputed
# Each pair's offset carries the sensor noise of both frames' poses (a phone's orientation is good to a few degrees), so pairs scatter by a
# few degrees even when the alignment is right. What matters is how well the median is known: its standard error.
GOOD_PAIRS, GOOD_INLIERS, GOOD_SEM_DEG, MAX_SPREAD_DEG = 3, 100, 1.5, 8.0
TRANSLATION_CONSISTENT = 0.7   # share of the unit translation vectors that agree (their mean resultant length) before the direction is believed
# A phone's orientation is good to a degree or two at best, so the scatter of per-pair offsets is never taken as smaller than this when the
# standard error is worked out (two pairs that happen to agree say little).
PAIR_NOISE_FLOOR_DEG = 1.0
DOMINANT_SHARE = 0.5    # one detected object holding more than this share of a pair's matches: refit without it before trusting the pair
DOMINANT_AGREE_DEG = 1.0
ALL_PAIRS_MAX = 64      # when frames_a x frames_b is at most this, every pair is tried in the first pass (small scans: a shortlist misses pairs)
LOCAL_MIN_POINTS = 6    # matched static features around an object needed to predict where it should appear in the other frame
LOCAL_RING = 0.75       # features within this many box sizes of the box (and at least LOCAL_RING_MIN_PX) count as around it
LOCAL_RING_MIN_PX = 60.0
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
    region: np.ndarray | None = None   # (N,) int: index of the detected object's box a feature lies in (padded), -1 for none


def _pad(b, k: float = 0.12):
    pw, ph = k * (b[2] - b[0]), k * (b[3] - b[1])
    return b[0] - pw, b[1] - ph, b[2] + pw, b[3] + ph


def regions(pts: np.ndarray, boxes: list | None) -> np.ndarray:
    """For each point, the index of the first (padded) box that holds it, -1 for none."""
    out = np.full(len(pts), -1, np.int32)
    for i, b in enumerate(boxes or []):
        x0, y0, x1, y1 = _pad(b)
        inside = (out < 0) & (pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1)
        out[inside] = i
    return out


def extract(jpeg: bytes | None, boxes: list | None = None, n_features: int = N_FEATURES, mask_boxes: bool = False) -> Feat | None:
    """SIFT features of a frame, each labelled with the detected object's box (`boxes`, padded) it lies in. With `mask_boxes` there are none
    inside the boxes at all. None when the frame is unusable or has too little texture."""
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
    if boxes and mask_boxes:
        mask = np.full(img.shape, 255, np.uint8)
        for b in boxes:
            x0, y0, x1, y1 = _pad(b)
            mask[max(0, int(y0 * s)):int(y1 * s) + 1, max(0, int(x0 * s)):int(x1 * s) + 1] = 0
    kps, des = cv2.SIFT_create(nfeatures=n_features).detectAndCompute(img, mask)
    if des is None or len(kps) < MIN_MATCHES:
        return None
    pts = np.float32([k.pt for k in kps]) / s
    return Feat(pts, des.astype(np.float32), w0, h0, regions(pts, boxes) if boxes and not mask_boxes else None)


def match(fb: Feat, fa: Feat) -> tuple[np.ndarray, np.ndarray]:
    """Matched points (pb, pa) between B and A: ratio test, and the best match must be mutual."""
    ib, ia = match_idx(fb, fa)
    return fb.pts[ib], fa.pts[ia]


def match_idx(fb: Feat, fa: Feat) -> tuple[np.ndarray, np.ndarray]:
    """Indices (into fb, into fa) of the matches: ratio test, and the best match must be mutual."""
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
    return np.array(ib, np.int64), np.array(ia, np.int64)


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
    pb: np.ndarray | None = None  # the inlier matches, pixels of B and of A
    pa: np.ndarray | None = None
    dropped_region: int | None = None   # an object's box whose matches disagreed with the rest of the scene and were left out
    mask: np.ndarray | None = None      # which of the matches given to the fit are inliers


def _fit(pb: np.ndarray, pa: np.ndarray, kb: np.ndarray, ka: np.ndarray, width_a: int, same_camera: bool) -> PairEstimate | None:
    """Rotation and essential-matrix fits of matched points; the rotation wins unless the essential matrix explains clearly more."""
    if len(pb) < MIN_MATCHES:
        return None
    rb, ra = bearings(pb, kb), bearings(pa, ka)
    rot = rotation_ransac(rb, ra)
    rot_n = int(rot[1].sum()) if rot else 0
    ess = None
    if len(pb) >= 8 and same_camera:
        try:
            thr = max(0.8, EPI_THRESH_FRAC * width_a)
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
    return PairEstimate(r, n, len(pb), model, t, math.degrees(math.atan(float(np.median(resid)) / ka[0, 0])), float(ka[0, 0]), pb[mask], pa[mask],
                        mask=mask)


def rotation_angle_deg(r1: np.ndarray, r2: np.ndarray) -> float:
    c = (np.trace(r1.T @ r2) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def estimate_pair(fb: Feat, fa: Feat, hfov_b: float, hfov_a: float) -> PairEstimate | None:
    ib, ia = match_idx(fb, fa)
    if len(ib) < MIN_MATCHES:
        return None
    pb, pa = fb.pts[ib], fa.pts[ia]
    kb, ka = intrinsics(fb.width, fb.height, hfov_b), intrinsics(fa.width, fa.height, hfov_a)
    same = abs(kb[0, 0] - ka[0, 0]) < 1e-6 * ka[0, 0] and fb.width == fa.width
    est = _fit(pb, pa, kb, ka, fa.width, same)
    if est is None or fa.region is None:
        return est
    # Features on detected objects are used, but one object must not carry the pair: if it moved, its own matches agree with each other and
    # would describe its motion, not the camera's. Refit without its matches; keep the full fit only when the rest of the scene agrees with it.
    reg = fa.region[ia]
    inl = reg[est.mask]
    vals, counts = np.unique(inl[inl >= 0], return_counts=True)
    if not len(vals) or counts.max() <= DOMINANT_SHARE * est.inliers:
        return est
    top = int(vals[counts.argmax()])
    keep = reg != top
    alt = _fit(pb[keep], pa[keep], kb, ka, fa.width, same)
    if alt is None:
        return None                                  # nothing but that object to go on: whether it moved cannot be told
    if rotation_angle_deg(alt.r, est.r) <= DOMINANT_AGREE_DEG:
        return est
    alt.dropped_region = top
    return alt


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


def pose_of(b: np.ndarray) -> tuple[float, float, float]:
    """(yaw, pitch, roll) in degrees of a camera basis (columns right, up, forward in earth axes): the inverse of anchoring.camera_basis."""
    u, f = b[:, 1], b[:, 2]
    yaw = math.degrees(math.atan2(f[0], f[1])) % 360.0
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(f[2])))))
    y = math.radians(yaw)
    right_ref = np.array([math.cos(y), -math.sin(y), 0.0])
    up_ref = np.cross(right_ref, f)
    return yaw, pitch, math.degrees(math.atan2(float(u @ right_ref), float(u @ up_ref)))


def box_clipped(box, width: int, height: int, margin: float = 2.0) -> bool:
    return box[0] <= margin or box[1] <= margin or box[2] >= width - margin or box[3] >= height - margin


def local_predictions(src: np.ndarray, dst: np.ndarray, dets: list[tuple[int, list]], width: int, height: int) -> list[dict]:
    """Where each detection's box centre should appear in the other frame, from the matched static features in a ring around its box (not on
    it: if the object moved, its own features moved with it). A similarity transform is fitted to those features, which lie at roughly the
    object's depth and so carry the same parallax. Boxes cut by the frame edge are skipped (their centre is not the object's)."""
    out = []
    for det_id, box in dets:
        if box_clipped(box, width, height):
            continue
        x0, y0, x1, y1 = _pad(box)
        ring = max(LOCAL_RING_MIN_PX, LOCAL_RING * max(box[2] - box[0], box[3] - box[1]))
        dx = np.maximum(np.maximum(x0 - src[:, 0], src[:, 0] - x1), 0.0)
        dy = np.maximum(np.maximum(y0 - src[:, 1], src[:, 1] - y1), 0.0)
        outside = (src[:, 0] < x0) | (src[:, 0] > x1) | (src[:, 1] < y0) | (src[:, 1] > y1)
        sel = outside & (np.hypot(dx, dy) <= ring)
        if sel.sum() < LOCAL_MIN_POINTS:
            continue
        m, inl = cv2.estimateAffinePartial2D(src[sel].astype(np.float32), dst[sel].astype(np.float32), method=cv2.RANSAC, ransacReprojThreshold=3.0)
        if m is None or inl is None or int(inl.sum()) < LOCAL_MIN_POINTS:
            continue
        keep = inl.reshape(-1) > 0
        moved = (np.column_stack([src[sel][keep], np.ones(int(keep.sum()))]) @ m.T) - dst[sel][keep]
        c = m @ np.array([(box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0, 1.0])
        out.append({"det": int(det_id), "px": [round(float(c[0]), 1), round(float(c[1]), 1)], "n": int(keep.sum()),
                    "spread_px": round(float(np.median(np.linalg.norm(moved, axis=1))), 2)})
    return out


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
    # Pairs that share a frame share its sensor error, so they are not independent: count distinct frames on the thinner side, not pairs.
    n_eff = len(use)
    if all("a" in e and "b" in e for e in use):
        n_eff = min(n_eff, len({e["a"] for e in use}), len({e["b"] for e in use}))
    sem = 1.2533 * max(spread, PAIR_NOISE_FLOOR_DEG) / math.sqrt(n_eff)   # standard error of a median of n roughly normal values
    return {"yaw": wrap180(m0 + med), "pitch": weighted_median([e["pitch"] for e in use], w), "roll": weighted_median([e["roll"] for e in use], w),
            "spread": spread, "sem": sem, "n_eff": n_eff, "used": use}


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

    def __init__(self, wm, scan_a: int, scan_b: int, boxes: bool, deadline: float, mask_objects: bool = False):
        self.wm, self.deadline, self.mask_objects = wm, deadline, mask_objects
        self.frames_a, self.frames_b = wm.list_frames(scan_a), wm.list_frames(scan_b)
        self.dets: dict[int, list[tuple[int, list]]] = {}            # frame id: [(detection id, box)]
        if boxes:
            for sid in (scan_a, scan_b):
                for row in wm.db.execute("SELECT id, frame_id, box FROM detections WHERE scan_id=? ORDER BY id", (sid,)):
                    self.dets.setdefault(row["frame_id"], []).append((row["id"], json.loads(row["box"])))
        self.feats: dict[int, Feat | None] = {}
        self.done: dict[tuple[int, int], dict | None] = {}

    def feat(self, f: dict) -> Feat | None:
        if f["id"] not in self.feats:
            self.feats[f["id"]] = extract(self.wm.frame_jpeg(f["id"]), [b for _, b in self.dets.get(f["id"], [])], mask_boxes=self.mask_objects)
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
                           "model": est.model, "parallax_deg": est.parallax_deg, "t_world": None if tw is None else tw.tolist(),
                           "b_pose": pose_of(basis(fa) @ S @ est.r @ S),            # where frame B pointed, in scan A's frame
                           "dropped_object": None if est.dropped_region is None else self.dets[fa["id"]][est.dropped_region][0],
                           "local": [{**p, "into": fb["id"]} for p in local_predictions(est.pa, est.pb, self.dets.get(fa["id"], []), fa["width"], fa["height"])]
                           + [{**p, "into": fa["id"]} for p in local_predictions(est.pb, est.pa, self.dets.get(fb["id"], []), fb["width"], fb["height"])]}
        self.done[key] = out
        return out


def _summary(ests: list[dict], method: str, run: _Run) -> dict:
    base = {"version": ALIGN_VERSION, "method": method, "frames_a": len(run.frames_a), "frames_b": len(run.frames_b), "pairs_tried": sum(1 for _ in run.done)}
    c = combine(ests)
    if c is None:
        return {**base, "quality": "failed", "offset_deg": 0.0, "pitch_offset_deg": 0.0, "roll_offset_deg": 0.0, "inliers": 0, "pairs": 0, "spread_deg": None,
                "offset_sem_deg": None, "translation": None, "parallax_deg": None, "pitch_bias_deg": None, "roll_bias_deg": None, "frame_poses": {}, "local": [],
                "frames_used_a": 0, "frames_used_b": 0, "dropped_objects": [],
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
    poses, bias_p, bias_r, bias_w = _frame_poses(used, {f["id"]: f for f in run.frames_b})
    # the offset is B minus A (the convention of app.changes); the estimate above is A minus B
    return {**base, "quality": q, "offset_deg": round(wrap180(-c["yaw"]), 2), "pitch_offset_deg": round(-c["pitch"], 2), "roll_offset_deg": round(-c["roll"], 2),
            "inliers": inliers, "pairs": len(used), "frames_used_a": len({e["a"] for e in used}), "frames_used_b": len({e["b"] for e in used}),
            "spread_deg": round(c["spread"], 2), "offset_sem_deg": round(c["sem"], 2), "translation": trans, "parallax_deg": round(par, 2),
            "pitch_bias_deg": None if not bias_w else round(weighted_median(bias_p, bias_w), 2),
            "roll_bias_deg": None if not bias_w else round(weighted_median(bias_r, bias_w), 2),
            "frame_poses": poses, "local": [p for e in used for p in e.get("local", [])],
            "dropped_objects": sorted({e["dropped_object"] for e in used if e.get("dropped_object") is not None}),
            "reason": None if q == "good" else "too few matching pairs, too few matching points, or pairs that disagree too much: the alignment is only a rough guide",
            "pair_estimates": [{"a": e["a"], "b": e["b"], "inliers": e["inliers"], "yaw": round(wrap180(-e["yaw"]), 2), "model": e["model"]} for e in sorted(used, key=lambda e: -e["inliers"])[:20]]}


def _frame_poses(used: list[dict], frames_b: dict[int, dict]) -> tuple[dict, list, list, list]:
    """Per frame of scan B, its pose in scan A's frame: the inlier-weighted median over the pairs it is in (yaw on the circle). Also each
    frame's stored pitch and roll minus that, from which the scan's tilt bias is the median: a phone held the same way but whose sensor reads a
    few degrees high shows up there, not in the yaw offset."""
    by_b: dict[int, list[dict]] = {}
    for e in used:
        if e.get("b_pose") is not None:
            by_b.setdefault(e["b"], []).append(e)
    poses, bias_p, bias_r, bias_w = {}, [], [], []
    for fid, es in by_b.items():
        w = [e["w"] for e in es]
        y0 = es[int(np.argmax(w))]["b_pose"][0]
        yaw = (y0 + weighted_median([wrap180(e["b_pose"][0] - y0) for e in es], w)) % 360.0
        pitch = weighted_median([e["b_pose"][1] for e in es], w)
        roll = weighted_median([e["b_pose"][2] for e in es], w)
        poses[str(fid)] = {"yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2), "pairs": len(es), "inliers": int(sum(w))}
        fb = frames_b.get(fid)
        if fb is not None:
            bias_p.append(fb["pitch"] - pitch)
            bias_r.append(wrap180(A.image_roll(fb.get("roll"), fb.get("screen_angle")) - roll))
            bias_w.append(sum(w))
    return poses, bias_p, bias_r, bias_w


def align_scans(wm, scan_a: int, scan_b: int, *, prior_offset: float = 0.0, window_deg: float = PRIOR_WINDOW_DEG, per_frame: int = PER_FRAME,
                use_boxes: bool = True, time_budget_s: float = TIME_BUDGET_S, mask_objects: bool = False) -> dict:
    """How scan B's reference frame differs from scan A's, from the images. `prior_offset` is the offset (B minus A, degrees) the pose prior
    assumes for choosing which frames to compare; 0 when both scans used the compass. `mask_objects` drops every feature inside a detected
    object's box (the behaviour before ALIGN_VERSION 3, kept for comparison: at a desk it leaves too little to match). See the module docstring
    for the output."""
    run = _Run(wm, scan_a, scan_b, use_boxes, time.monotonic() + time_budget_s, mask_objects)
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

    if len(run.frames_a) * len(run.frames_b) <= ALL_PAIRS_MAX:   # a small scan: try every pair, a shortlist by pose would miss some
        ests = [p for fb in run.frames_b for fa in run.frames_a if (p := run.pair(fa, fb)) is not None]
        return _summary(ests, "all pairs", run)
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
