"""What-changed between two scans (F13): added, removed and moved objects.

Objects are matched across scans by label (app.world_model.label_match_score) and angular position.
Three things keep this honest:
  - Yaw alignment. A relative sensor has an arbitrary yaw zero per session, so the second scan is
    shifted into the first one's frame by an offset estimated from objects that appear in both
    (majority vote, so a few moved objects do not spoil it). Pitch is gravity-referenced and needs no shift.
  - Coverage. An object is only called removed (or added) if the other scan's frames actually looked
    at that direction; otherwise it is reported as unverified.
  - Tolerance. Anchors differ by sensor drift and FOV error, so a match closer than `move_deg` counts
    as unchanged. The default is a placeholder until the drift measurements are in [VERIFY].

Limits: a label that disappears in one place and appears in another reads as "moved"; a swap of two
identical objects is invisible; appearance is not compared.
"""
from __future__ import annotations

import json
import math
import os

from app import align as align_mod
from app import anchoring as A
from app import scale
from app.world_model import WorldModel, describe_offset, label_match_score, plural

MOVE_DEG = 15.0
ALIGN_TOL_DEG = 10.0


def estimate_yaw_offset(a_objs: list[dict], b_objs: list[dict], tol_deg: float = ALIGN_TOL_DEG, min_inliers: int = 2) -> dict:
    """Yaw offset (b minus a, degrees in [-180, 180)) that best lines up objects with matching labels.
    `confident` needs at least `min_inliers` distinct objects of `a` agreeing; with fewer, a moved
    object cannot be told from a rotated frame and the caller should not trust the offset."""
    cands = [(a, b, A.wrap180(b["yaw"] - a["yaw"])) for a in a_objs for b in b_objs if label_match_score(a["label"], b["label"]) > 0]
    if not cands:
        return {"offset_deg": 0.0, "inliers": 0, "candidates": 0, "confident": False}

    def consistent(off):
        return [(a, b, o) for a, b, o in cands if abs(A.wrap180(o - off)) <= tol_deg and abs(a["pitch"] - b["pitch"]) <= tol_deg]

    best, best_key = None, None
    for _, _, off in cands:
        group = consistent(off)
        key = (len({a["id"] for a, _, _ in group}), -abs(off))  # most agreeing objects, then the smallest rotation
        if best_key is None or key > best_key:
            best, best_key = group, key
    mean_yaw, _ = A.mean_direction([(o % 360.0, 0.0, 1.0) for _, _, o in best]) if best else (0.0, 0.0)
    inliers = best_key[0]
    return {"offset_deg": A.wrap180(mean_yaw) if best else 0.0, "inliers": inliers, "candidates": len(cands), "confident": inliers >= min_inliers}


def _grouped(entries: list[dict], many: str) -> list[str]:
    """One sentence per label: the entry's own sentence for a single object, a count for several."""
    by_label: dict[str, list[dict]] = {}
    for e in entries:
        by_label.setdefault(e["label"], []).append(e)
    return [g[0]["speech"] if len(g) == 1 else many.format(n=len(g), things=plural(label)) for label, g in by_label.items()]


def _covered(frames: list[dict], yaw: float, pitch: float, shift: float, margin: float = 0.05) -> bool:
    """Does any frame (its yaw shifted by `shift`) see the direction inside the image?"""
    for f in frames:
        px = A.direction_to_pixel(yaw, pitch, f["width"], f["height"], f["yaw"] + shift, f["pitch"],
                                  A.image_roll(f["roll"], f["screen_angle"]), f["hfov_deg"])
        if px and f["width"] * margin <= px[0] <= f["width"] * (1 - margin) and f["height"] * margin <= px[1] <= f["height"] * (1 - margin):
            return True
    return False


def diff_scans(wm: WorldModel, scan_a: int, scan_b: int, yaw_offset: float | None = None, move_deg: float = MOVE_DEG,
               max_spoken: int = 6) -> dict:
    """Compare scan `scan_b` (now) against `scan_a` (before). `yaw_offset` = b's yaw frame minus a's;
    None estimates it from shared objects (falls back to 0 when not confident)."""
    objs_a, objs_b = wm.list_objects(scan_a), wm.list_objects(scan_b)
    frames_a, frames_b = wm.list_frames(scan_a), wm.list_frames(scan_b)
    if yaw_offset is None:
        est = estimate_yaw_offset(objs_a, objs_b)
        off = est["offset_deg"] if est["confident"] else 0.0
        alignment = {**est, "offset_deg": off, "method": "landmarks" if est["confident"] else "none"}
    else:
        off, alignment = yaw_offset, {"offset_deg": yaw_offset, "method": "given"}

    pairs = sorted(((A.angular_distance(a["yaw"] + off, a["pitch"], b["yaw"], b["pitch"]), a, b)
                    for a in objs_a for b in objs_b if label_match_score(a["label"], b["label"]) > 0), key=lambda t: t[0])
    used_a, used_b, matched = set(), set(), []
    for sep, a, b in pairs:
        if a["id"] not in used_a and b["id"] not in used_b:
            used_a.add(a["id"])
            used_b.add(b["id"])
            matched.append((sep, a, b))

    start_b = wm.scan_start_pose(scan_b)

    def place(o: dict, shift: float) -> dict:
        """An object described in scan b's yaw frame (`shift` converts from the object's own scan)."""
        yaw = (o["yaw"] + shift) % 360.0
        where = describe_offset(A.wrap180(yaw - start_b["yaw"]), o["pitch"]) if start_b else "at an unknown direction"
        nbrs = wm.neighbors(o["id"])
        rel = f", {nbrs[0]['relation']} the {nbrs[0]['label']}" if nbrs else ""
        return {"object_id": o["id"], "label": o["label"], "anchor": {"yaw": yaw, "pitch": o["pitch"]}, "where": where + rel,
                "replay": wm.replay(o["id"])}

    moved, unchanged = [], 0
    for sep, a, b in matched:
        if sep <= move_deg:
            unchanged += 1
            continue
        before, after = place(a, off), place(b, 0.0)
        moved.append({"label": b["label"], "from": before, "to": after, "sep_deg": sep,
                      "speech": f"The {b['label']} moved: it was {before['where']}, now {after['where']}."})

    removed, added, unverified = [], [], []
    for a in objs_a:
        if a["id"] in used_a:
            continue
        e = place(a, off)
        if _covered(frames_b, e["anchor"]["yaw"], e["anchor"]["pitch"], 0.0):
            removed.append({**e, "speech": f"The {a['label']} is gone; it was {e['where']}."})
        else:
            unverified.append({**e, "was": "removed?", "reason": "that direction was not rescanned"})
    for b in objs_b:
        if b["id"] in used_b:
            continue
        e = place(b, 0.0)
        if _covered(frames_a, e["anchor"]["yaw"], e["anchor"]["pitch"], off):
            added.append({**e, "speech": f"A {b['label']} is new, {e['where']}."})
        else:
            unverified.append({**e, "was": "added?", "reason": "that direction was not in the earlier scan"})

    parts = [x["speech"] for x in moved] + _grouped(removed, "{n} {things} are gone.") + _grouped(added, "{n} {things} are new.")
    if len(parts) > max_spoken:
        parts = parts[:max_spoken] + [f"And {len(parts) - max_spoken} more change{'s' if len(parts) - max_spoken != 1 else ''}."]
    if not parts:
        parts = ["Nothing changed."]
    if unverified:
        parts.append(f"{len(unverified)} object{'s' if len(unverified) != 1 else ''} could not be checked because that part of the room was not scanned both times.")
    return {"scan_a": scan_a, "scan_b": scan_b, "alignment": alignment, "added": added, "removed": removed, "moved": moved,
            "unchanged": unchanged, "unverified": unverified, "speech": " ".join(parts)}


# ================================================================================================================ parallax-aware comparison

def alignment_for(wm: WorldModel, scan_a: int, scan_b: int, refresh: bool = False) -> dict:
    """How scan B's frame differs from scan A's, from the images (app.align). Computed once for a pair of finished scans and kept."""
    if not refresh:
        cached = wm.get_alignment(scan_a, scan_b)
        if cached is not None and cached.get("version") == align_mod.ALIGN_VERSION:
            return cached
    res = align_mod.align_scans(wm, scan_a, scan_b)
    sa, sb = wm.get_scan(scan_a), wm.get_scan(scan_b)
    if sa and sb and sa["ended_at"] and sb["ended_at"]:
        wm.set_alignment(scan_a, scan_b, res)
    return res


# alignment internals that are large and only of use here: kept out of API answers
ALIGN_INTERNAL = ("local", "frame_poses", "pair_estimates")


def public_alignment(al: dict) -> dict:
    return {k: v for k, v in al.items() if k not in ALIGN_INTERNAL}


def _unreliable(al: dict) -> str:
    if al["quality"] == "failed":
        return "the two scans could not be lined up from their pictures, so this cannot be called a move"
    return "the two scans only lined up roughly, so this cannot be called a move"


# ---------------------------------------------------------------------------------------------------------------- evidence thresholds
# Local evidence (app.align): the texture around an object, at about its depth, says where the object should appear from the other viewpoint.
LOCAL_MIN_DEG = 3.0        # a local prediction is never trusted to better than this (box centres jitter, a tall object stands above the desk)
LOCAL_SPREAD_K = 3.0       # ... nor to better than this many times the scatter of the features it was fitted to
LOCAL_SIZE_SHARE = 0.7     # ... nor to better than this share of the object's size in the picture (see docs/decisions.md, 2026-10-31)
LOCAL_MOVED_K = 2.5        # a residual beyond this many local tolerances is a move the viewpoint cannot explain
# Removed and new need evidence: Spark and SAM do not list the same things every time, so one missed sighting is not "gone".
MIN_FRAMES_FOR_GONE = 2    # the object must have been seen in at least this many frames of its own scan
MAX_VIEWPOINT_CM = 60.0    # the viewpoint change measured from static objects is believed up to this (beyond it, the distances are suspect)
# "Moved, low confidence": the only claim allowed when the alignment is weak (never when it failed, never for a repeated label). The owner can
# turn it off with MOVED_LOW_CONFIDENCE=0. See docs/decisions.md, 2026-10-31.
MOVED_LOW_CONFIDENCE = os.getenv("MOVED_LOW_CONFIDENCE", "1") not in ("0", "false", "no", "off")
LOW_CONF_K = 3.0           # the shift must exceed this many tolerances ...
LOW_CONF_UNC_FLOOR = 2.5   # ... plus the alignment's uncertainty, never taken as less than this (a weak alignment rests on one or two pairs)
LOW_CONF_MAX_OFFSET = 10.0 # the images' offset must agree with the compass (both scans used it) to this many degrees ...
LOW_CONF_MIN_INLIERS = 40  # ... and rest on at least this many matching points


def _det_rows(wm: WorldModel, scan_id: int) -> dict[int, list[dict]]:
    """Detections of a scan by object: id, frame, box."""
    out: dict[int, list[dict]] = {}
    for r in wm.db.execute("SELECT id, frame_id, box, object_id FROM detections WHERE scan_id=? AND object_id IS NOT NULL", (scan_id,)):
        out.setdefault(r["object_id"], []).append({"id": r["id"], "frame_id": r["frame_id"], "box": json.loads(r["box"])})
    return out


def _reanchor(objs_b: list[dict], dets_b: dict[int, list[dict]], frames_b: dict[int, dict], al: dict, off: float, pitch_bias: float) -> dict[int, tuple[float, float]]:
    """Each object of scan B as a direction in scan A's frame. Its detections are re-projected through their frames' poses as the images place
    them (`frame_poses`), which removes each frame's own sensor error and the scan's tilt bias; frames without one use the scan's offset and
    tilt bias. The change is applied to the object's stored anchor, so the memory's own way of combining sightings is kept."""
    poses = al.get("frame_poses") or {}
    out = {}
    for b in objs_b:
        dy, dp, n = 0.0, 0.0, 0
        for d in dets_b.get(b["id"], []):
            f, pose = frames_b.get(d["frame_id"]), poses.get(str(d["frame_id"]))
            if f is None or pose is None:
                continue
            roll = A.image_roll(f["roll"], f["screen_angle"])
            y0, p0 = A.box_anchor(tuple(d["box"]), f["width"], f["height"], f["yaw"], f["pitch"], roll, f["hfov_deg"])
            y1, p1 = A.box_anchor(tuple(d["box"]), f["width"], f["height"], pose["yaw"], pose["pitch"], pose["roll"], f["hfov_deg"])
            dy, dp, n = dy + A.wrap180(y1 - y0), dp + (p1 - p0), n + 1
        if n:
            out[b["id"]] = ((b["yaw"] + dy / n) % 360.0, b["pitch"] + dp / n)
        else:
            out[b["id"]] = ((b["yaw"] - off) % 360.0, b["pitch"] - pitch_bias)
    return out


def _kinds(objs: list[dict], dets: dict[int, list[dict]], tol_of) -> tuple[dict[str, int], dict[int, int]]:
    """How many distinct things of each label a scan holds, and for each object the group it belongs to. Two objects of one label that were never seen in the same frame and lie within
    the viewpoint tolerance of each other may be one thing that hand drift split in two (near the phone a few centimetres shift it past the
    memory's merge distance), so they count once; objects seen together in a frame, or far apart, are several."""
    out, group_of = {}, {}
    by_label: dict[str, list[dict]] = {}
    for o in objs:
        by_label.setdefault(o["label"], []).append(o)
    for label, group in by_label.items():
        parent = list(range(len(group)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        frames = [{d["frame_id"] for d in dets.get(o["id"], [])} for o in group]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                near = A.angular_distance(group[i]["yaw"], group[i]["pitch"], group[j]["yaw"], group[j]["pitch"]) <= max(8.0, tol_of(group[i]), tol_of(group[j]))
                if near and not (frames[i] & frames[j]):
                    parent[root(i)] = root(j)
        out[label] = len({root(i) for i in range(len(group))})
        group_of.update({o["id"]: group[root(i)]["id"] for i, o in enumerate(group)})
    return out, group_of


def _local_index(al: dict) -> dict[tuple[int, int], dict]:
    """(detection id, frame it was predicted into): the prediction."""
    return {(p["det"], p["into"]): p for p in al.get("local") or []}


def _local_evidence(loc: dict, dets_a: list[dict], dets_b: list[dict], frames: dict[int, dict]) -> dict | None:
    """How far an object is from where the texture around it says it should be, in degrees, over every frame pair that has both a prediction
    and a detection of the other object in the predicted frame. Pure image measurements: no sensor pose and no assumed viewpoint error."""
    res, tols, sizes, views = [], [], [], 0
    for src, dst in ((dets_a, dets_b), (dets_b, dets_a)):
        for s_det in src:
            for d_det in dst:
                p = loc.get((s_det["id"], d_det["frame_id"]))
                f = frames.get(d_det["frame_id"])
                if p is None or f is None or align_mod.box_clipped(d_det["box"], f["width"], f["height"]):
                    continue
                foc = A.focal_px(f["width"], f["hfov_deg"])
                bx = d_det["box"]
                r_px = math.hypot(p["px"][0] - (bx[0] + bx[2]) / 2.0, p["px"][1] - (bx[1] + bx[3]) / 2.0)
                size = math.degrees(math.atan2(max(bx[2] - bx[0], bx[3] - bx[1]), foc))
                res.append(math.degrees(math.atan2(r_px, foc)))
                tols.append(max(LOCAL_MIN_DEG, LOCAL_SPREAD_K * math.degrees(math.atan2(p["spread_px"], foc)), LOCAL_SIZE_SHARE * size))
                sizes.append(size)
                views += 1
    if not res:
        return None
    mid = lambda v: sorted(v)[len(v) // 2]
    return {"residual_deg": round(mid(res), 1), "tolerance_deg": round(mid(tols), 1), "size_deg": round(mid(sizes), 1), "views": views}


def _seen_there(loc: dict, dets: list[dict], frames_other: dict[int, dict]) -> bool:
    """Did the other scan look at this object's spot closely enough to tell? Some frame of it has a prediction for one of the object's
    detections (texture around it matched), landing inside that frame."""
    for d in dets:
        for (det, into), p in loc.items():
            f = frames_other.get(into)
            if det == d["id"] and f is not None and 0.05 * f["width"] <= p["px"][0] <= 0.95 * f["width"] and 0.05 * f["height"] <= p["px"][1] <= 0.95 * f["height"]:
                return True
    return False


def compare_scans(wm: WorldModel, scan_a: int, scan_b: int, align: dict | None = None, viewpoint_cm: float | None = None,
                  max_spoken: int = 6, low_confidence: bool | None = None) -> dict:
    """Compare scan `scan_b` (now) against `scan_a` (before), allowing for the viewpoint having moved.

    The offset between the scans' frames comes from the images (`alignment_for`; `align` overrides it); where the images placed a frame of B,
    B's objects are re-anchored through that pose. Each pair of same-label objects is classified by how far apart they are, against how far a
    viewpoint error of `viewpoint_cm` (default 15) alone could move that object in the picture (app.scale: nearer objects shift more):
    unchanged, possibly moved (up to twice that, or that plus the uncertainty of the distance, the anchors and the alignment) and moved
    (beyond). Where the texture around an object was matched in both scans (local evidence), the object's offset from where that texture says
    it should be decides instead: within the local tolerance it is unchanged, far beyond it moved. New and removed need the object seen in at
    least two frames of its scan and the other scan to have looked at that spot. When the alignment is weak or failed nothing is called moved,
    new or removed ("can't compare"); the one exception is "moved, low confidence" (`low_confidence`, default MOVED_LOW_CONFIDENCE).
    """
    vp = scale.VIEWPOINT_CM if viewpoint_cm is None else viewpoint_cm
    low_conf_on = MOVED_LOW_CONFIDENCE if low_confidence is None else low_confidence
    objs_a, objs_b = wm.list_objects(scan_a), wm.list_objects(scan_b)
    frames_a, frames_b = wm.list_frames(scan_a), wm.list_frames(scan_b)
    fa_by, fb_by = {f["id"]: f for f in frames_a}, {f["id"]: f for f in frames_b}
    al = dict(align if align is not None else alignment_for(wm, scan_a, scan_b))
    confident = al["quality"] == "good"
    if al["quality"] in ("good", "weak"):
        off, source = al["offset_deg"], "images"
    else:  # no usable pictures: the objects' own directions can still suggest an offset, but never enough to call anything moved
        est = estimate_yaw_offset(objs_a, objs_b)
        off, source = (est["offset_deg"], "landmarks") if est["confident"] else (0.0, "none")
    # where the images placed B's frames is used when the alignment is good, or weak but agreeing with the compass (else it may be a wrong pair)
    use_poses = confident or (al["quality"] == "weak" and abs(off) <= LOW_CONF_MAX_OFFSET)
    pitch_bias = (al.get("pitch_bias_deg") or 0.0) if use_poses else 0.0
    al.update(offset_used_deg=round(off, 2), offset_source=source, viewpoint_cm=vp, low_confidence_enabled=low_conf_on)
    sem = al.get("offset_sem_deg")
    if sem is None:  # a result without it: the spread over the square root of the pairs
        sem = (al.get("spread_deg") or 0.0) / math.sqrt(max(1, al.get("pairs") or 1))
    trans = al.get("translation") if (al.get("translation") or {}).get("trusted") else None   # a direction the pairs disagree on is not used
    dets_a, dets_b = _det_rows(wm, scan_a), _det_rows(wm, scan_b)

    def distance(o: dict) -> dict:
        size, src = scale.size_for(wm, o["label"])
        return scale.distance_estimate(wm, o, size, src) or {"distance_cm": 150.0, "lo_cm": 75.0, "hi_cm": 300.0, "rel_err": 0.5, "size_cm": size, "size_source": "unknown"}

    mult, group_of = {}, {}                                                                        # labels that appear more than once in either scan
    for objs, dets in ((objs_a, dets_a), (objs_b, dets_b)):
        kinds, groups = _kinds(objs, dets, lambda o: scale.tolerance_deg(distance(o)["distance_cm"], vp))
        group_of.update(groups)
        for label, n in kinds.items():
            mult[label] = max(mult.get(label, 0), n)
    b_in_a = _reanchor(objs_b, dets_b, fb_by, {**al, "frame_poses": al.get("frame_poses") if use_poses else {}}, off, pitch_bias)
    loc = _local_index(al) if use_poses else {}
    # a weak alignment may still allow "moved, low confidence": it must agree with the compass and rest on enough matching points
    low_conf_ok = low_conf_on and al["quality"] == "weak" and abs(off) <= LOW_CONF_MAX_OFFSET and (al.get("inliers") or 0) >= LOW_CONF_MIN_INLIERS
    low_unc = max(LOW_CONF_UNC_FLOOR, sem)

    pairs = sorted(((A.angular_distance(a["yaw"], a["pitch"], *b_in_a[b["id"]]), a, b)
                    for a in objs_a for b in objs_b if label_match_score(a["label"], b["label"]) > 0), key=lambda t: t[0])
    used_a, used_b, matched = set(), set(), []
    for sep, a, b in pairs:
        if a["id"] not in used_a and b["id"] not in used_b:
            used_a.add(a["id"])
            used_b.add(b["id"])
            matched.append((sep, a, b))

    start_b = wm.scan_start_pose(scan_b)

    def place(o: dict, shift: float) -> dict:
        yaw = (o["yaw"] + shift) % 360.0
        where = describe_offset(A.wrap180(yaw - start_b["yaw"]), o["pitch"]) if start_b else "at an unknown direction"
        nbrs = wm.neighbors(o["id"])
        rel = f", {nbrs[0]['relation']} the {nbrs[0]['label']}" if nbrs else ""
        return {"object_id": o["id"], "label": o["label"], "anchor": {"yaw": yaw, "pitch": o["pitch"]}, "where": where + rel, "replay": wm.replay(o["id"])}

    # First pass: the local evidence of every matched pair. Objects the texture around them shows as static say how far the viewpoint really
    # moved (their shift times their distance); the model below then allows at least that, not only the assumed `viewpoint_cm`.
    rows = []
    for sep, a, b in matched:
        ea, eb = distance(a), distance(b)
        est = min(ea, eb, key=lambda e: e["distance_cm"])                  # the nearer estimate: nearer things shift more
        lev = _local_evidence(loc, dets_a.get(a["id"], []), dets_b.get(b["id"], []), {**fa_by, **fb_by})
        rows.append((sep, a, b, est, lev))
    # the far end of the distance band: the viewpoint change is over- rather than under-estimated, which only widens tolerances
    seen_vp = [r[3]["hi_cm"] * math.tan(math.radians(min(r[0], 60.0))) for r in rows if r[4] is not None and r[4]["residual_deg"] <= r[4]["tolerance_deg"]]
    vp_used = min(MAX_VIEWPOINT_CM, max([vp] + seen_vp))
    al.update(viewpoint_cm_measured=round(max(seen_vp), 1) if seen_vp else None, viewpoint_cm_used=round(vp_used, 1))

    moved, possibly, low, unchanged, cant = [], [], [], 0, []
    for sep, a, b, est, lev in rows:
        across = scale.across_share(trans, a["yaw"], a["pitch"])
        tol, band = scale.band_deg(est, vp_used, across)
        tol_assumed = scale.band_deg(est, vp, across)[0]                       # with only the assumed viewpoint error
        anchor_unc = 0.5 * math.hypot(a.get("uncertainty_deg") or a["spread_deg"], b.get("uncertainty_deg") or b["spread_deg"])
        margin = math.hypot(band, sem, anchor_unc)
        upper = max(2.0 * tol, tol + margin)
        info = {"sep_deg": round(sep, 1), "tolerance_deg": round(tol, 1), "uncertainty_deg": round(margin, 1), "distance_cm": est["distance_cm"],
                "distance_lo_cm": est["lo_cm"], "distance_hi_cm": est["hi_cm"], "size_source": est["size_source"], "local": lev}
        # Local evidence decides at its clear ends: within its tolerance the viewpoint explains the shift (unchanged); far beyond it the object
        # is not where its surroundings say, a move, but called one only if the model also allows it (else "possibly"). In between, the model.
        # A measured viewpoint change larger than assumed widens the model's tolerance, but must not bury a move the local evidence shows: then the
        # model only has to agree under the assumed error.
        local_far = lev is not None and lev["residual_deg"] > LOCAL_MOVED_K * lev["tolerance_deg"]
        if (lev is not None and lev["residual_deg"] <= lev["tolerance_deg"]) or (sep <= (tol_assumed if local_far else tol) and not (local_far and sep > tol_assumed)):
            unchanged += 1
            continue
        before, after = place(a, off), place(b, 0.0)
        repeated = mult.get(a["label"], 1) > 1
        far = (local_far and sep > tol_assumed) or (not local_far and sep > upper)
        if not confident:
            if low_conf_ok and not repeated and (lev["residual_deg"] > LOW_CONF_K * lev["tolerance_deg"] + low_unc and sep > tol + low_unc if lev is not None
                                                 else sep > LOW_CONF_K * tol + low_unc):
                low.append({"label": b["label"], "from": before, "to": after, **info, "low_confidence": True,
                            "speech": f"The {b['label']} seems to have moved: it was {before['where']}, now {after['where']}. The two scans only lined up roughly, so check it."})
            else:
                cant.append({**before, "was": "moved?", "to": after, "reason": _unreliable(al), **info})
        elif not far:
            possibly.append({"label": b["label"], "from": before, "to": after, **info,
                             "speech": f"The {b['label']} may have moved: it was {before['where']}, now {after['where']}, but that is within what a different spot could explain."})
        elif repeated:  # several of the same thing: which one is which cannot be known, so a big difference is not proof of a move
            possibly.append({"label": b["label"], "from": before, "to": after, **info, "ambiguous": True,
                             "speech": f"One of the {plural(b['label'])} may have moved: it was {before['where']}, now {after['where']}, but there are several and they may have been mixed up."})
        else:
            moved.append({"label": b["label"], "from": before, "to": after, **info,
                          "speech": f"The {b['label']} moved: it was {before['where']}, now {after['where']}."})

    removed, added = [], []

    matched_groups = {group_of.get(a["id"]) for _, a, _ in matched} | {group_of.get(b["id"]) for _, _, b in matched}

    def gone_or_new(o: dict, e: dict, was: str, other_frames: list[dict], other_by: dict, shift: float, own_dets: list[dict]) -> str | None:
        """None when `o` may be called removed / new; else why not."""
        if not confident:
            return _unreliable(al)
        if group_of.get(o["id"]) in matched_groups:       # a twin in its own scan was compared: probably the same thing, split by the hand moving
            return f"probably the same {o['label']} as one that was compared (seen twice from slightly different spots)"
        if not _covered(other_frames, e["anchor"]["yaw"], e["anchor"]["pitch"], shift):
            return "that direction was not rescanned" if was == "removed?" else "that direction was not in the earlier scan"
        if (o.get("n_obs") or 1) < MIN_FRAMES_FOR_GONE:
            return ("seen only once in the earlier scan, so missing it now may just be a missed detection" if was == "removed?"
                    else "seen only once in the new scan, so it may be a one-off detection")
        if al.get("local") is not None and not _seen_there(loc, own_dets, other_by):
            return "the other scan did not see that spot clearly enough to say"
        return None

    for a in objs_a:
        if a["id"] in used_a:
            continue
        e = place(a, off)
        why = gone_or_new(a, e, "removed?", frames_b, fb_by, 0.0, dets_a.get(a["id"], []))
        if why is None:
            removed.append({**e, "speech": f"The {a['label']} is gone; it was {e['where']}."})
        else:
            cant.append({**e, "was": "removed?", "reason": why})
    for b in objs_b:
        if b["id"] in used_b:
            continue
        e = place(b, 0.0)
        why = gone_or_new(b, e, "added?", frames_a, fa_by, off, dets_b.get(b["id"], []))
        if why is None:
            added.append({**e, "speech": f"A {b['label']} is new, {e['where']}."})
        else:
            cant.append({**e, "was": "added?", "reason": why})

    parts = [x["speech"] for x in moved] + _grouped(possibly, "{n} {things} may have moved, but a different spot could explain it.") \
        + _grouped(removed, "{n} {things} are gone.") + _grouped(added, "{n} {things} are new.")
    if len(parts) > max_spoken:
        parts = parts[:max_spoken] + [f"And {len(parts) - max_spoken} more change{'s' if len(parts) - max_spoken != 1 else ''}."]
    if not confident and low:
        parts = ["The two scans lined up only roughly, so I can only point out a change too big to be the viewpoint."] + [x["speech"] for x in low[:max_spoken]]
    elif not confident:
        parts = ["The two scans could not be lined up reliably" + (" from their pictures" if al["quality"] == "failed" else "") + ", so I cannot say what moved."]
    elif not parts:
        parts = ["Nothing changed."]
    if cant:
        parts.append(f"{len(cant)} object{'s' if len(cant) != 1 else ''} could not be compared.")
    return {"scan_a": scan_a, "scan_b": scan_b, "alignment": al, "added": added, "removed": removed, "moved": moved, "possibly_moved": possibly,
            "moved_low_confidence": low, "cant_compare": cant, "unverified": cant, "unchanged": unchanged, "speech": " ".join(parts)}
