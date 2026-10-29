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

import math

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
        if cached is not None:
            return cached
    res = align_mod.align_scans(wm, scan_a, scan_b)
    sa, sb = wm.get_scan(scan_a), wm.get_scan(scan_b)
    if sa and sb and sa["ended_at"] and sb["ended_at"]:
        wm.set_alignment(scan_a, scan_b, res)
    return res


def _unreliable(al: dict) -> str:
    if al["quality"] == "failed":
        return "the two scans could not be lined up from their pictures, so this cannot be called a move"
    return "the two scans only lined up roughly, so this cannot be called a move"


def compare_scans(wm: WorldModel, scan_a: int, scan_b: int, align: dict | None = None, viewpoint_cm: float | None = None,
                  max_spoken: int = 6) -> dict:
    """Compare scan `scan_b` (now) against `scan_a` (before), allowing for the viewpoint having moved.

    The offset between the scans' frames comes from the images (`alignment_for`; `align` overrides it). Each pair of same-label objects is
    classified by how far apart they are after the offset, against how far a viewpoint error of `viewpoint_cm` (default 15) alone could
    move that object in the picture (app.scale: nearer objects shift more): unchanged, possibly moved (up to twice that, or that plus the
    uncertainty of the distance, the anchors and the alignment) and moved (beyond). Unmatched objects are new or removed only when the other
    scan looked at that direction. When the alignment is weak or failed nothing is called moved, new or removed: it is "can't compare".
    """
    vp = scale.VIEWPOINT_CM if viewpoint_cm is None else viewpoint_cm
    objs_a, objs_b = wm.list_objects(scan_a), wm.list_objects(scan_b)
    frames_a, frames_b = wm.list_frames(scan_a), wm.list_frames(scan_b)
    al = dict(align if align is not None else alignment_for(wm, scan_a, scan_b))
    confident = al["quality"] == "good"
    if al["quality"] in ("good", "weak"):
        off, source = al["offset_deg"], "images"
    else:  # no usable pictures: the objects' own directions can still suggest an offset, but never enough to call anything moved
        est = estimate_yaw_offset(objs_a, objs_b)
        off, source = (est["offset_deg"], "landmarks") if est["confident"] else (0.0, "none")
    al.update(offset_used_deg=round(off, 2), offset_source=source, viewpoint_cm=vp)
    spread = al.get("spread_deg") or 0.0

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
        yaw = (o["yaw"] + shift) % 360.0
        where = describe_offset(A.wrap180(yaw - start_b["yaw"]), o["pitch"]) if start_b else "at an unknown direction"
        nbrs = wm.neighbors(o["id"])
        rel = f", {nbrs[0]['relation']} the {nbrs[0]['label']}" if nbrs else ""
        return {"object_id": o["id"], "label": o["label"], "anchor": {"yaw": yaw, "pitch": o["pitch"]}, "where": where + rel, "replay": wm.replay(o["id"])}

    def distance(o: dict) -> dict:
        size, src = scale.size_for(wm, o["label"])
        return scale.distance_estimate(wm, o, size, src) or {"distance_cm": 150.0, "lo_cm": 75.0, "hi_cm": 300.0, "rel_err": 0.5, "size_cm": size, "size_source": "unknown"}

    moved, possibly, unchanged, cant = [], [], 0, []
    for sep, a, b in matched:
        ea, eb = distance(a), distance(b)
        est = min(ea, eb, key=lambda e: e["distance_cm"])                  # the nearer estimate: nearer things shift more
        across = scale.across_share(al.get("translation"), a["yaw"], a["pitch"])
        tol, band = scale.band_deg(est, vp, across)
        anchor_unc = 0.5 * math.hypot(a.get("uncertainty_deg") or a["spread_deg"], b.get("uncertainty_deg") or b["spread_deg"])
        margin = math.hypot(band, spread, anchor_unc)
        upper = max(2.0 * tol, tol + margin)
        info = {"sep_deg": round(sep, 1), "tolerance_deg": round(tol, 1), "uncertainty_deg": round(margin, 1), "distance_cm": est["distance_cm"],
                "distance_lo_cm": est["lo_cm"], "distance_hi_cm": est["hi_cm"], "size_source": est["size_source"]}
        if sep <= tol:
            unchanged += 1
            continue
        before, after = place(a, off), place(b, 0.0)
        if not confident:
            cant.append({**before, "was": "moved?", "to": after, "reason": _unreliable(al), **info})
        elif sep <= upper:
            possibly.append({"label": b["label"], "from": before, "to": after, **info,
                             "speech": f"The {b['label']} may have moved: it was {before['where']}, now {after['where']}, but that is within what a different spot could explain."})
        else:
            moved.append({"label": b["label"], "from": before, "to": after, **info,
                          "speech": f"The {b['label']} moved: it was {before['where']}, now {after['where']}."})

    removed, added = [], []
    for a in objs_a:
        if a["id"] in used_a:
            continue
        e = place(a, off)
        if not confident:
            cant.append({**e, "was": "removed?", "reason": _unreliable(al)})
        elif _covered(frames_b, e["anchor"]["yaw"], e["anchor"]["pitch"], 0.0):
            removed.append({**e, "speech": f"The {a['label']} is gone; it was {e['where']}."})
        else:
            cant.append({**e, "was": "removed?", "reason": "that direction was not rescanned"})
    for b in objs_b:
        if b["id"] in used_b:
            continue
        e = place(b, 0.0)
        if not confident:
            cant.append({**e, "was": "added?", "reason": _unreliable(al)})
        elif _covered(frames_a, e["anchor"]["yaw"], e["anchor"]["pitch"], off):
            added.append({**e, "speech": f"A {b['label']} is new, {e['where']}."})
        else:
            cant.append({**e, "was": "added?", "reason": "that direction was not in the earlier scan"})

    parts = [x["speech"] for x in moved] + [x["speech"] for x in possibly] + _grouped(removed, "{n} {things} are gone.") + _grouped(added, "{n} {things} are new.")
    if len(parts) > max_spoken:
        parts = parts[:max_spoken] + [f"And {len(parts) - max_spoken} more change{'s' if len(parts) - max_spoken != 1 else ''}."]
    if not confident:
        parts = ["The two scans could not be lined up reliably" + (" from their pictures" if al["quality"] == "failed" else "") + ", so I cannot say what moved."]
    elif not parts:
        parts = ["Nothing changed."]
    if cant:
        parts.append(f"{len(cant)} object{'s' if len(cant) != 1 else ''} could not be compared.")
    return {"scan_a": scan_a, "scan_b": scan_b, "alignment": al, "added": added, "removed": removed, "moved": moved, "possibly_moved": possibly,
            "cant_compare": cant, "unverified": cant, "unchanged": unchanged, "speech": " ".join(parts)}
