"""Objects inspector: what the memory believes about one scan, grouped so a person can review it, and how it came to believe it.

Read-only views over the World Model. The corrections themselves (merge, detach, delete, "not an object") are WorldModel methods, because
the memory has to re-apply them on every rebuild.
"""
from __future__ import annotations

import json
import time
from collections import Counter

from app import anchoring as A
from app import memory
from app.reality_api import public_replay
from app.world_model import WorldModel

DUP_DEG = 20.0          # same-label objects whose directions are this close (and never seen in one frame) are possible duplicates
DUP_MIN_SCORE = 0.2


def _ref(wm: WorldModel, scan_id: int) -> dict | None:
    s = wm.get_scan(scan_id)
    return None if s is None else {"id": s["id"], "status": s["status"], "room_id": s["room_id"], "room_name": s["room_name"],
                                   "started_at": s["started_at"], "ended_at": s["ended_at"], "n_frames": s["n_frames"]}


def _object_stats(wm: WorldModel, scan_id: int) -> dict[int, dict]:
    """Per object: how many sightings and their mean confidence, from the detections themselves."""
    return {r["object_id"]: {"sightings": r["n"], "avg_confidence": r["avg"]} for r in wm.db.execute(
        "SELECT object_id, COUNT(*) AS n, AVG(conf) AS avg FROM detections WHERE scan_id=? AND object_id IS NOT NULL GROUP BY object_id", (scan_id,))}


def _instance(wm: WorldModel, o: dict, stats: dict) -> dict:
    st = stats.get(o["id"], {"sightings": o["n_obs"], "avg_confidence": o["conf_best"]})
    return {"id": o["id"], "label": o["label"], "sightings": st["sightings"], "avg_confidence": round(st["avg_confidence"], 3), "best_confidence": round(o["conf_best"], 3),
            "yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1), "uncertainty_deg": round(o["uncertainty_deg"] or o["spread_deg"], 1),
            "first_seen": o["first_seen"], "last_seen": o["last_seen"], "thumb": public_replay(wm.replay(o["id"]))}


def scan_objects(wm: WorldModel, scan_id: int) -> dict | None:
    """The scan's objects grouped by label: instances, sightings and mean confidence per label, each instance with a thumbnail,
    plus the labels hidden as "not an object" in this room and how many possible duplicates there are."""
    ref = _ref(wm, scan_id)
    if ref is None:
        return None
    with wm.lock:
        stats = _object_stats(wm, scan_id)
        groups: dict[str, list] = {}
        for o in wm.list_objects(scan_id):
            groups.setdefault(o["label"], []).append(_instance(wm, o, stats))
        out = []
        for label, inst in groups.items():
            n = sum(i["sightings"] for i in inst)
            out.append({"label": label, "instances": len(inst), "sightings": n,
                        "avg_confidence": round(sum(i["avg_confidence"] * i["sightings"] for i in inst) / max(n, 1), 3), "objects": inst})
        out.sort(key=lambda g: (-g["instances"], -g["sightings"], g["label"]))
        hidden = []
        for label in wm.not_object_labels(ref["room_id"]):
            n = wm.db.execute("SELECT COUNT(*) AS n FROM detections WHERE scan_id=? AND label=?", (scan_id, label)).fetchone()["n"]
            hidden.append({"label": label, "sightings": n})
        return {"scan": ref, "labels": out, "instances": sum(g["instances"] for g in out), "sightings": sum(g["sightings"] for g in out),
                "hidden_labels": hidden, "possible_duplicates": len(possible_duplicates(wm, scan_id))}


def describe(d: dict) -> str:
    """A decision of the memory, in a sentence."""
    a = d["action"]
    if a == "new":
        return "Started a new object: nothing close enough to join."
    if a == "merge":
        s = f"Joined an object by {d.get('geometry')} ({d.get('value')})"
        return s + (f", appearance {d['similarity']}." if d.get("similarity") is not None else ".")
    if a == "veto":
        return f"Not joined to a nearby object: the colours differ too much (similarity {d.get('similarity')}), even though the {d.get('geometry')} matched."
    if a == "merge_objects":
        return f"Two objects were merged: their footprints overlap {d.get('overlap')}" + (f", appearance {d['similarity']}." if d.get("similarity") is not None else ".")
    if a == "veto_merge":
        return f"Two objects were kept apart: they overlap {d.get('overlap')} but the colours differ too much (similarity {d.get('similarity')})."
    if a == "merge_fragment":
        return "A partly visible view at the edge of the image joined this object."
    if a == "promote":
        return f"Partly visible views seen in {d.get('frames')} frames became an object."
    if a == "hold_fragment":
        return f"A partly visible view seen in only {d.get('frames')} frame(s) was held back, not made an object."
    if a == "manual_merge":
        return "Merged by hand."
    if a == "detached":
        return "Detached by hand: this sighting stands alone."
    if a == "excluded":
        return "Ignored: the object was deleted by hand, or its label is marked as not an object here."
    return a


def _relevant(d: dict, dets: set[int]) -> bool:
    return d.get("det") in dets or bool(dets & set(d.get("dets") or ())) or bool(dets & set(d.get("into") or ()))


def object_detail(wm: WorldModel, object_id: int) -> dict | None:
    """One instance: where it is, every sighting (frame, box, mask polygon, camera pose, SAM confidence), the decisions that made it
    one object, and the corrections made to it by hand."""
    with wm.lock:
        o = wm.get_object(object_id)
        if o is None:
            return None
        rows = wm.db.execute(
            "SELECT d.id, d.frame_id, d.box, d.conf, d.yaw, d.pitch, d.poly, d.fragment, d.track_id, d.fov_err, f.ts, f.yaw AS cam_yaw, f.pitch AS cam_pitch,"
            " f.roll, f.screen_angle, f.hfov_deg, f.width, f.height FROM detections d JOIN frames f ON f.id=d.frame_id"
            " WHERE d.object_id=? ORDER BY f.ts, d.id", (object_id,)).fetchall()
        sightings = [{"id": r["id"], "frame_id": r["frame_id"], "image_url": f"/world/frame/{r['frame_id']}.jpg", "box": json.loads(r["box"]),
                      "width": r["width"], "height": r["height"], "confidence": round(r["conf"], 3), "mask": json.loads(r["poly"]) if r["poly"] else None,
                      "anchor": {"yaw": round(r["yaw"], 1), "pitch": round(r["pitch"], 1)}, "fragment": bool(r["fragment"]), "track_id": r["track_id"],
                      "fov_error_deg": round(r["fov_err"], 1),
                      "camera": {"yaw": round(r["cam_yaw"], 1), "pitch": round(r["cam_pitch"], 1), "roll": A.image_roll(r["roll"], r["screen_angle"]),
                                 "hfov_deg": r["hfov_deg"], "ts": r["ts"]}} for r in rows]
        dets = {s["id"] for s in sightings}
        log = wm.decisions(o["scan_id"]) or wm.explain(o["scan_id"])
        decisions = [{**d, "text": describe(d)} for d in log if _relevant(d, dets)]
        corrections = [c for c in wm.list_corrections(scan_id=o["scan_id"], limit=500) if dets & set(c["payload"].get("dets") or [c["payload"].get("det")])]
        stats = _object_stats(wm, o["scan_id"])
        return {"scan": _ref(wm, o["scan_id"]), "object": _instance(wm, o, stats), "spread_deg": round(o["spread_deg"], 1), "fov_error_deg": round(o["fov_err_deg"], 1),
                "sightings": sightings, "decisions": decisions, "corrections": corrections,
                "decisions_source": "log" if wm.decisions(o["scan_id"]) else "recomputed"}


def possible_duplicates(wm: WorldModel, scan_id: int, max_deg: float = DUP_DEG) -> list[dict]:
    """Pairs of same-label objects that may be one thing: close in direction, never seen in the same frame (two objects seen together are
    two objects). Strongest first, with the numbers behind each suggestion."""
    with wm.lock:
        objs = wm.list_objects(scan_id)
        frames: dict[int, set] = {}
        hists: dict[int, list] = {}
        for r in wm.db.execute("SELECT object_id, frame_id, hist FROM detections WHERE scan_id=? AND object_id IS NOT NULL", (scan_id,)):
            frames.setdefault(r["object_id"], set()).add(r["frame_id"])
            if r["hist"]:
                hists.setdefault(r["object_id"], []).append(json.loads(r["hist"]))
        mean = {k: [sum(h[i] for h in v) / len(v) for i in range(len(v[0]))] for k, v in hists.items()}
        out = []
        for i, a in enumerate(objs):
            for b in objs[i + 1:]:
                if a["label"] != b["label"] or frames.get(a["id"], set()) & frames.get(b["id"], set()):
                    continue
                sep = A.angular_distance(a["yaw"], a["pitch"], b["yaw"], b["pitch"])
                ea, eb = (a["state"] or {}).get("ext"), (b["state"] or {}).get("ext")
                overlap = max(A.extent_overlap(tuple(ea), tuple(eb))) if ea and eb else 0.0
                if sep > max_deg and overlap < 0.1:
                    continue
                sim = memory.hist_similarity(mean.get(a["id"]), mean.get(b["id"]))
                score = max(1 - sep / max_deg, overlap)
                if sim is not None and sim < wm.memory_cfg.appearance_min:
                    score *= 0.5  # the colours disagree: more likely two things
                if score < DUP_MIN_SCORE:
                    continue
                why = [f"{sep:.0f}° apart"] + ([f"footprints overlap {overlap:.2f}"] if overlap else []) + ([f"appearance {sim:.2f}"] if sim is not None else [])
                out.append({"a": a["id"], "b": b["id"], "label": a["label"], "separation_deg": round(sep, 1), "overlap": round(overlap, 2),
                            "appearance": None if sim is None else round(sim, 2), "score": round(score, 2), "reason": ", ".join(why),
                            "thumbs": [public_replay(wm.replay(a["id"])), public_replay(wm.replay(b["id"]))]})
        out.sort(key=lambda d: (-d["score"], d["a"], d["b"]))
        return out


def label_counts(wm: WorldModel, scan_id: int) -> dict[str, int]:
    return dict(Counter(o["label"] for o in wm.list_objects(scan_id)))
