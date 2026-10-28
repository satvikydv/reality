"""Reality API: the room memory as questions another agent can ask. `mcp_server.py` exposes these as MCP tools.

Every answer says how far to trust it: when the object was last seen, how many frames saw it, how much the
anchors disagreed, and, when nothing is found, how much of the room the latest scan actually covered. An
agent must be able to tell "not seen" from "not there".

All functions are read-only and return plain JSON-able dicts with a `status`.
"""
from __future__ import annotations

import time
from collections import Counter

from app import anchoring as A
from app.changes import diff_scans
from app.resolver import semantic_find
from app.coverage import coverage
from app.scan_service import ScanService, count_phrase
from app.world_model import label_match_score

FRAME_URL = "/world/frame/{}.jpg"


def public_replay(rp: dict | None) -> dict | None:
    """A sighting without server file paths: which frame, where the box is, how to fetch the photo."""
    if not rp:
        return None
    return {"frame_id": rp["frame_id"], "box": rp["box"], "width": rp["width"], "height": rp["height"], "image_url": FRAME_URL.format(rp["frame_id"])}


def _clean(v):
    """Replace every `replay` with its public form and drop file paths, recursively."""
    if isinstance(v, dict):
        return {k: (public_replay(x) if k == "replay" and isinstance(x, dict) else _clean(x)) for k, x in v.items() if k != "path"}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def _object(o: dict, now: float) -> dict:
    unc = o.get("uncertainty_deg") or o["spread_deg"]
    return {"id": o["id"], "label": o["label"], "yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1), "frames_seen": o["n_obs"],
            "supporting_frames": o["n_obs"], "confidence": round(o["conf_best"], 2), "anchor_spread_deg": round(o["spread_deg"], 1),
            "fov_error_deg": round(o.get("fov_err_deg") or 0.0, 1), "uncertainty_deg": round(unc, 1),
            "last_seen": o["last_seen"], "age_seconds": round(max(0.0, now - o["last_seen"]))}


def _latest_scan(svc: ScanService, scan_id: int | None) -> dict | None:
    scans = [s for s in svc.wm.list_scans() if s["n_frames"] > 0]  # an empty scan (aborted, no frames) says nothing
    if scan_id is not None:
        return next((s for s in svc.wm.list_scans() if s["id"] == scan_id), None)
    return scans[0] if scans else None


def _coverage(svc: ScanService, scan_id: int) -> dict:
    return coverage(svc.wm.list_frames(scan_id))


def _pitch_note(cov: dict | None) -> str:
    """Say so when the scan never looked down or up: the object may be on the floor or a high shelf."""
    if not cov:
        return ""
    unseen = [w for name, w in (("low", "down toward the floor"), ("high", "up toward the ceiling")) if cov["bands"][name]["covered_deg"] == 0]
    return f" The scan also never looked {' or '.join(unseen)}." if unseen else ""


async def where_is(svc: ScanService, query: str) -> dict:
    """Where something is, from a plain-language description ("the thing I charge my laptop with")."""
    r = await semantic_find(svc.reasoner, svc.wm, query)
    res, found, now = r["resolution"], r["found"], time.time()
    base = {"query": query, "concept": res["concept"], "alternates": res["alternates"], "interpretation_confidence": res["confidence"]}
    if not res["concept"]:
        return {**base, "status": "unresolved", "message": "That does not describe a findable physical object."}
    latest = _latest_scan(svc, None)
    if not found:
        cov = _coverage(svc, latest["id"]) if latest else None
        covered = cov["covered_deg"] if cov else 0
        return {**base, "status": "not_seen", "latest_scan_id": latest["id"] if latest else None, "latest_scan_coverage": cov,
                "message": f"No {res['concept']} in memory. The latest scan covered {covered} of 360 degrees, so it may be in an area not scanned."
                           + _pitch_note(cov)}
    o = found["object"]
    off = found["from_scan_start"] or {}
    return {**base, "status": "found", "object": _object(o, now), "scan_id": found["scan_id"], "seen_in_latest_scan": found["is_latest_scan"],
            "compass": {"yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1)},
            "from_scan_start": {"yaw_deg": round(off.get("dyaw", 0.0), 1), "pitch_deg": round(off.get("dpitch", 0.0), 1)} if off else None,
            "neighbors": [{"label": n["label"], "relation": n["relation"], "separation_deg": round(n["sep_deg"], 1)} for n in found["neighbors"][:5]],
            "sighting": public_replay(found["replay"]), "answer": found["speech"]}


def list_objects(svc: ScanService, label: str | None = None, scan_id: int | None = None) -> dict:
    """Every object in a scan (default: the latest), optionally only those matching `label`."""
    scan = _latest_scan(svc, scan_id)
    if scan is None:
        return {"status": "no_scans", "message": "Nothing has been scanned yet." if scan_id is None else f"No scan {scan_id}."}
    now = time.time()
    objs = [o for o in svc.wm.list_objects(scan["id"]) if not label or label_match_score(label, o["label"]) > 0]
    return {"status": "ok", "scan_id": scan["id"], "count": len(objs), "objects": [_object(o, now) for o in objs]}


def scan_summary(svc: ScanService, scan_id: int | None = None) -> dict:
    """What a scan found: counts per kind, coverage of the room, and when it happened."""
    scan = _latest_scan(svc, scan_id)
    if scan is None:
        return {"status": "no_scans", "message": "Nothing has been scanned yet." if scan_id is None else f"No scan {scan_id}."}
    objs = svc.wm.list_objects(scan["id"])
    labels = [o["label"] for o in objs]
    return {"status": "ok", "scan_id": scan["id"], "name": scan["name"], "started_at": scan["started_at"], "ended_at": scan["ended_at"],
            "frames": scan["n_frames"], "object_count": len(objs), "counts": dict(Counter(labels).most_common()),
            "coverage": _coverage(svc, scan["id"]), "summary": f"{count_phrase(labels)}." if labels else "No objects found."}


def what_changed(svc: ScanService, scan_a: int | None = None, scan_b: int | None = None) -> dict:
    """Compare two scans (default: the latest against the one before): added, removed, moved, and what could not be checked."""
    if scan_a is None or scan_b is None:
        scans = [s for s in svc.wm.list_scans() if s["n_frames"] > 0]
        if len(scans) < 2:
            return {"status": "need_two_scans", "scans_available": len(scans), "message": "Comparing needs two scans; scan the room again after changing something."}
        scan_b, scan_a = (scan_b or scans[0]["id"]), (scan_a or scans[1]["id"])
    for sid in (scan_a, scan_b):
        if svc.wm.get_scan(sid) is None:
            return {"status": "no_such_scan", "message": f"No scan {sid}."}
    d = diff_scans(svc.wm, scan_a, scan_b)
    return {"status": "ok", "scan_before": scan_a, "scan_after": scan_b, "summary": d["speech"], "alignment": d["alignment"],
            "added": _clean(d["added"]), "removed": _clean(d["removed"]), "moved": _clean(d["moved"]), "unverified": _clean(d["unverified"]),
            "unchanged_count": d["unchanged"]}


def get_frame(svc: ScanService, frame_id: int) -> bytes | None:
    """The stored JPEG of a keyframe (it may show people); None if unknown."""
    return svc.wm.frame_jpeg(frame_id)


def overlay(svc: ScanService, scan_id: int | None = None) -> dict:
    """Everything the phone needs to draw a scan's objects over the live camera. Positions stay angular (no metric
    distances exist). `age_s` is measured on the server clock; `hfov_deg` is the one FOV both sides must use."""
    scan = _latest_scan(svc, scan_id)
    base = {"hfov_deg": A.DEFAULT_HFOV_DEG, "fov_calibrated": A.FOV_CALIBRATED}
    if scan is None:
        return {**base, "status": "no_scans", "scan_id": None, "objects": []}
    now = time.time()
    objs = [{**{k: v for k, v in o.items() if k != "last_seen"}, "age_s": round(max(0.0, now - o["last_seen"]), 1)}
            for o in svc.wm.overlay_objects(scan["id"])]
    return {**base, "status": "ok", "scan_id": scan["id"], "objects": objs}
