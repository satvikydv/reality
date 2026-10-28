"""Which detections are the same object: a pure function of the detections, not of when they arrived.

`cluster()` takes every detection of a scan and returns the objects. The input is first put in capture order
(the frame's capture timestamp, then its pose, then the box), so two runs over the same detections give the same
objects however the frames were delivered or processed. Steps:

  1. Greedy matching in capture order. A detection joins the object whose angular footprint it overlaps most (or
     whose anchor is within `merge_deg`), unless another detection of the same frame already joined that object
     (one view cannot see an object twice), or the appearance differs too much (colour histogram of the mask).
  2. Consolidation: objects of the same label whose footprints overlap are merged, strongest overlap first, under the
     same two rules.
  3. Edge-clipped fragments (small boxes cut off by the image border) join an existing object when they match one.
     Fragments that match nothing form candidates of their own and become objects only when seen in 2+ frames.

Every decision is recorded in `Result.decisions` so it can be logged and audited.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app import anchoring as A


@dataclass(frozen=True)
class Config:
    merge_deg: float = 8.0          # anchors this close are the same object when no footprint is available
    overlap_small: float = 0.5      # footprint overlap, as a share of the smaller footprint
    overlap_large: float = 0.1      # ... and of the larger one (a tiny box inside a huge one is not swallowed)
    use_appearance: bool = True
    appearance_min: float = 0.35    # histogram intersection below this vetoes a geometric match
    promote_fragments: bool = True
    promote_min_frames: int = 2     # an edge-clipped detection becomes an object when seen in this many frames
    order: str = "capture"          # "capture": by timestamp (deterministic); "arrival": by frame id (legacy, order dependent)


@dataclass
class Det:
    """One detection, as stored. `key` orders detections inside a frame; `frame_key` orders frames."""
    id: int
    frame_id: int
    ts: float
    cam_yaw: float
    cam_pitch: float
    label: str
    box: tuple
    conf: float
    yaw: float
    pitch: float
    ext: tuple | None = None
    fragment: bool = False
    hist: list | None = None
    fov_err: float = 0.0
    track_id: int | None = None
    prev_object: int | None = None   # the object this detection belonged to before (keeps ids stable)
    idx: int = 0

    def sort_key(self, order: str):
        if order == "arrival":
            return (self.frame_id, self.idx)
        return (round(self.ts, 6), round(self.cam_yaw, 6), round(self.cam_pitch, 6), self.idx, self.label, tuple(round(b, 3) for b in self.box))


@dataclass
class Cluster:
    label: str
    members: list = field(default_factory=list)
    yaw: float = 0.0
    pitch: float = 0.0
    ext: tuple | None = None
    hist: list | None = None
    frames: set = field(default_factory=set)
    first_key: tuple = ()

    def add(self, d: Det, order: str) -> None:
        if not self.members:
            self.first_key = d.sort_key(order)
        self.members.append(d)
        self.frames.add(d.frame_id)
        self.refresh()

    def refresh(self) -> None:
        ms = sorted(self.members, key=lambda m: m.sort_key("capture"))  # same sum order whatever order members arrived in
        self.yaw, self.pitch = A.mean_direction([(m.yaw, m.pitch, max(m.conf, 1e-6)) for m in ms])
        exts = [m.ext for m in self.members if m.ext]
        self.ext = max(exts, key=A.extent_area) if exts else None
        hs = [m.hist for m in self.members if m.hist]
        self.hist = [sum(h[i] for h in hs) / len(hs) for i in range(len(hs[0]))] if hs else None


@dataclass
class Result:
    clusters: list           # list[Cluster], in a deterministic order
    pending: list            # fragment detections not (yet) part of any object
    decisions: list          # list[dict]


def hist_similarity(a: list | None, b: list | None) -> float | None:
    """Histogram intersection of two normalised colour histograms: 1 identical, 0 disjoint. None if either is missing."""
    if not a or not b or len(a) != len(b):
        return None
    return sum(min(x, y) for x, y in zip(a, b))


def mask_histogram(img_bgr, polygon, box, bins_hue: int = 12) -> list | None:
    """Normalised colour histogram of the pixels inside a mask outline (or the box when there is none): hue x two
    saturation levels for coloured pixels plus three brightness bins for grey ones. Cheap, and stable across views."""
    import cv2
    import numpy as np

    h, w = img_bgr.shape[:2]
    m = np.zeros((h, w), np.uint8)
    if polygon and len(polygon) >= 3:
        cv2.fillPoly(m, [np.round(np.array(polygon, np.float32)).astype(np.int32)], 1)
    else:
        x1, y1, x2, y2 = (int(round(v)) for v in box)
        m[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = 1
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    sel = m.astype(bool)
    if sel.sum() < 16:
        return None
    hh, ss, vv = hsv[..., 0][sel].astype(int), hsv[..., 1][sel].astype(int), hsv[..., 2][sel].astype(int)
    grey = (ss < 40) | (vv < 50)
    out = np.zeros(bins_hue * 2 + 3)
    col = ~grey
    if col.any():
        idx = (hh[col] * bins_hue // 180) * 2 + (ss[col] >= 140).astype(int)
        np.add.at(out, np.minimum(idx, bins_hue * 2 - 1), 1)
    if grey.any():
        np.add.at(out, bins_hue * 2 + np.minimum(2, vv[grey] // 86), 1)
    return [round(float(x), 4) for x in out / out.sum()]


def fov_error_deg(offset_deg: float, rel_error: float) -> float:
    """Anchor error from a wrong field of view: a ray `offset_deg` from the optical axis is placed with an error
    proportional to that offset, and the proportion is the FOV's relative error. Zero on the axis."""
    return abs(offset_deg) * rel_error


def uncertainty_deg(spread_deg: float, fov_err_deg: float) -> float:
    """One number for how far an object's stored direction may be from the truth: sightings that disagree
    (spread) and the systematic FOV error combined as independent errors."""
    return math.hypot(spread_deg, fov_err_deg)


def _geometry(d: Det, c: Cluster, cfg: Config):
    """How well detection `d` fits cluster `c` by position: (rank, value, kind) or None. Overlap beats anchor distance."""
    if d.ext and c.ext:
        small, large = A.extent_overlap(d.ext, c.ext)
        if small >= cfg.overlap_small and large >= cfg.overlap_large:
            return (1, small, "overlap")
    sep = A.angular_distance(d.yaw, d.pitch, c.yaw, c.pitch)
    if sep < cfg.merge_deg:
        return (0, 1.0 - sep / cfg.merge_deg, "anchor")
    return None


def _best(d: Det, clusters: list, cfg: Config, decisions: list):
    """The cluster `d` should join, or None. Records a veto for every geometric match refused on appearance."""
    best, best_rank = None, None
    for i, c in enumerate(clusters):
        if c.label != d.label or d.frame_id in c.frames:
            continue
        g = _geometry(d, c, cfg)
        if g is None:
            continue
        sim = hist_similarity(d.hist, c.hist) if cfg.use_appearance else None
        if sim is not None and sim < cfg.appearance_min:
            decisions.append({"action": "veto", "det": d.id, "label": d.label, "cluster": i, "geometry": g[2], "value": round(g[1], 3), "similarity": round(sim, 3)})
            continue
        rank = (g[0], g[1], -i)
        if best_rank is None or rank > best_rank:
            best, best_rank, info = c, rank, (g, sim)
    return (best, info) if best is not None else (None, None)


def cluster(dets: list[Det], cfg: Config = Config()) -> Result:
    order = sorted(dets, key=lambda d: d.sort_key(cfg.order))
    decisions: list[dict] = []
    clusters: list[Cluster] = []

    # 1. greedy matching in capture order
    for d in (x for x in order if not x.fragment):
        c, info = _best(d, clusters, cfg, decisions)
        if c is None:
            nc = Cluster(d.label)
            nc.add(d, cfg.order)
            clusters.append(nc)
            decisions.append({"action": "new", "det": d.id, "label": d.label})
        else:
            c.add(d, cfg.order)
            decisions.append({"action": "merge", "det": d.id, "label": d.label, "cluster": clusters.index(c), "geometry": info[0][2],
                              "value": round(info[0][1], 3), "similarity": None if info[1] is None else round(info[1], 3)})

    # 2. consolidation: merge objects that turned out to be the same, strongest footprint overlap first
    pairs = []
    for i, a in enumerate(clusters):
        for j in range(i + 1, len(clusters)):
            b = clusters[j]
            if a.label != b.label or not a.ext or not b.ext:
                continue
            small, large = A.extent_overlap(a.ext, b.ext)
            if small >= cfg.overlap_small and large >= cfg.overlap_large:
                pairs.append((-small, a.first_key, b.first_key, i, j))
    pairs.sort(key=lambda p: p[:3])
    parent = list(range(len(clusters)))

    def root(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for neg, _, _, i, j in pairs:
        ri, rj = root(i), root(j)
        if ri == rj:
            continue
        a, b = clusters[ri], clusters[rj]
        if a.frames & b.frames:
            continue  # seen together in one frame: two objects
        sim = hist_similarity(a.hist, b.hist) if cfg.use_appearance else None
        if sim is not None and sim < cfg.appearance_min:
            decisions.append({"action": "veto_merge", "label": a.label, "clusters": [ri, rj], "overlap": round(-neg, 3), "similarity": round(sim, 3)})
            continue
        keep, gone = (ri, rj) if a.first_key <= b.first_key else (rj, ri)
        for m in clusters[gone].members:
            clusters[keep].members.append(m)
        clusters[keep].frames |= clusters[gone].frames
        clusters[keep].first_key = min(clusters[keep].first_key, clusters[gone].first_key)
        clusters[keep].refresh()
        clusters[gone].members = []
        parent[gone] = keep
        decisions.append({"action": "merge_objects", "label": a.label, "kept": keep, "absorbed": gone, "overlap": round(-neg, 3), "similarity": None if sim is None else round(sim, 3)})
    clusters = sorted((c for c in clusters if c.members), key=lambda c: c.first_key)

    # 3. edge-clipped fragments: join an object when one matches, else wait for a second frame
    pending: list[Cluster] = []
    for d in (x for x in order if x.fragment):
        c, info = _best(d, clusters, cfg, decisions)
        if c is not None:
            c.add(d, cfg.order)
            decisions.append({"action": "merge_fragment", "det": d.id, "label": d.label, "geometry": info[0][2], "value": round(info[0][1], 3)})
            continue
        p, info = _best(d, pending, cfg, decisions)
        if p is None:
            p = Cluster(d.label)
            pending.append(p)
        p.add(d, cfg.order)
    left = []
    for p in sorted(pending, key=lambda c: c.first_key):
        if cfg.promote_fragments and len(p.frames) >= cfg.promote_min_frames:
            clusters.append(p)
            decisions.append({"action": "promote", "label": p.label, "frames": len(p.frames), "dets": [m.id for m in p.members]})
        else:
            left.extend(p.members)
            decisions.append({"action": "hold_fragment", "label": p.label, "frames": len(p.frames), "dets": [m.id for m in p.members]})
    clusters.sort(key=lambda c: c.first_key)
    return Result(clusters, left, decisions)


def summarize(c: Cluster) -> dict:
    """The stored fields of an object, computed from its members alone."""
    ms = sorted(c.members, key=lambda m: m.sort_key("capture"))
    w = sum(max(m.conf, 1e-6) for m in ms)
    spread = max(A.angular_distance(c.yaw, c.pitch, m.yaw, m.pitch) for m in ms)
    fov = sum(m.fov_err * max(m.conf, 1e-6) for m in ms) / w
    last = ms[-1]
    return {"label": c.label, "yaw": c.yaw, "pitch": c.pitch, "weight": sum(m.conf for m in ms), "n_obs": len(ms), "spread_deg": spread,
            "fov_err_deg": fov, "uncertainty_deg": uncertainty_deg(spread, fov), "conf_best": max(m.conf for m in ms),
            "first_seen": ms[0].ts, "last_seen": last.ts, "last_box": list(last.box), "last_frame_id": last.frame_id,
            "ext": c.ext, "track_ids": sorted({m.track_id for m in ms if m.track_id is not None})}
