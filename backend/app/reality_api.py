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
from app.changes import compare_scans
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


def _room_ref(room: dict) -> dict:
    return {"id": room["id"], "name": room["name"], "active": room["active"]}


def room_list(svc: ScanService) -> dict:
    """Every room with what its memory holds. The answer when a call names no room and none is active."""
    rooms = svc.wm.list_rooms()
    return {"status": "ok", "active_room_id": next((r["id"] for r in rooms if r["active"]), None),
            "rooms": [{"id": r["id"], "name": r["name"], "active": r["active"], "objects": r["n_objects"], "scans": r["n_scans"],
                       "last_scanned": r["last_scanned"], "coverage_pct": r["coverage_pct"]} for r in rooms]}


def resolve_room(svc: ScanService, room) -> tuple[dict | None, dict | None]:
    """(room, error). `room` is an id or a name; None means the room the user chose (the active room). Rooms never share
    anything, so a call must be about exactly one of them: no active room and none named returns the room list instead."""
    if room is None or room == "":
        r = svc.wm.active_room()
        if r is None:
            return None, {**room_list(svc), "status": "no_active_room",
                          "message": "No room is active. Pass `room` (an id or a name from `rooms`), or ask the user to choose a room."}
        return r, None
    r = svc.wm.resolve_room(room)
    if r is None:
        return None, {**room_list(svc), "status": "no_such_room", "message": f"There is no room {room!r}. The rooms are listed in `rooms`."}
    return r, None


def _memory(svc: ScanService, room: dict, scan_id: int | None) -> tuple[dict | None, dict | None]:
    """(scan, error): the room's memory (its latest saved scan), or `scan_id` if that scan belongs to this room."""
    if scan_id is not None:
        s = svc.wm.get_scan(scan_id)
        if s is None:
            return None, {"status": "no_such_scan", "message": f"No scan {scan_id}."}
        if s["room_id"] != room["id"]:
            return None, {"status": "wrong_room", "room": _room_ref(room), "message": f"Scan {scan_id} belongs to {s['room_name']}, not {room['name']}. Rooms are never mixed."}
        return s, None
    sid = svc.wm.latest_scan_id(room["id"])
    if sid is None:
        return None, {"status": "no_scans", "room": _room_ref(room), "message": f"{room['name']} has not been scanned yet."}
    return svc.wm.get_scan(sid), None


def _coverage(svc: ScanService, scan_id: int) -> dict:
    return coverage(svc.wm.list_frames(scan_id))


def _pitch_note(cov: dict | None) -> str:
    """Say so when the scan never looked down or up: the object may be on the floor or a high shelf."""
    if not cov:
        return ""
    unseen = [w for name, w in (("low", "down toward the floor"), ("high", "up toward the ceiling")) if cov["bands"][name]["covered_deg"] == 0]
    return f" The scan also never looked {' or '.join(unseen)}." if unseen else ""


async def where_is(svc: ScanService, query: str, room=None) -> dict:
    """Where something is in one room, from a plain-language description ("the thing I charge my laptop with")."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    scan, err = _memory(svc, rm, None)
    if err:
        return {**err, "query": query}
    r = await semantic_find(svc.reasoner, svc.wm, query, scan["id"])
    res, found, now = r["resolution"], r["found"], time.time()
    base = {"room": _room_ref(rm), "query": query, "concept": res["concept"], "alternates": res["alternates"], "interpretation_confidence": res["confidence"]}
    if not res["concept"]:
        return {**base, "status": "unresolved", "message": "That does not describe a findable physical object."}
    if not found:
        cov = _coverage(svc, scan["id"])
        covered = cov["covered_deg"]
        return {**base, "status": "not_seen", "latest_scan_id": scan["id"], "latest_scan_coverage": cov,
                "message": f"No {res['concept']} in {rm['name']}'s memory. The latest saved scan covered {covered} of 360 degrees, so it may be in an area not scanned."
                           + _pitch_note(cov)}
    o = found["object"]
    off = found["from_scan_start"] or {}
    return {**base, "status": "found", "object": _object(o, now), "scan_id": found["scan_id"], "seen_in_latest_scan": found["is_latest_scan"],
            "compass": {"yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1)},
            "from_scan_start": {"yaw_deg": round(off.get("dyaw", 0.0), 1), "pitch_deg": round(off.get("dpitch", 0.0), 1)} if off else None,
            "neighbors": [{"label": n["label"], "relation": n["relation"], "separation_deg": round(n["sep_deg"], 1)} for n in found["neighbors"][:5]],
            "sighting": public_replay(found["replay"]), "answer": found["speech"]}


def list_objects(svc: ScanService, label: str | None = None, scan_id: int | None = None, room=None) -> dict:
    """Every object in a scan of one room (default: its memory, the latest saved scan), optionally only those matching `label`."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    scan, err = _memory(svc, rm, scan_id)
    if err:
        return err
    now = time.time()
    objs = [o for o in svc.wm.list_objects(scan["id"]) if not label or label_match_score(label, o["label"]) > 0]
    return {"status": "ok", "room": _room_ref(rm), "scan_id": scan["id"], "count": len(objs), "objects": [_object(o, now) for o in objs]}


def scan_summary(svc: ScanService, scan_id: int | None = None, room=None) -> dict:
    """What a scan of one room found: counts per kind, coverage of the room, and when it happened."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    scan, err = _memory(svc, rm, scan_id)
    if err:
        return err
    objs = svc.wm.list_objects(scan["id"])
    labels = [o["label"] for o in objs]
    return {"status": "ok", "room": _room_ref(rm), "scan_id": scan["id"], "name": scan["name"], "started_at": scan["started_at"], "ended_at": scan["ended_at"],
            "frames": scan["n_frames"], "object_count": len(objs), "counts": dict(Counter(labels).most_common()),
            "coverage": _coverage(svc, scan["id"]), "summary": f"{count_phrase(labels)}." if labels else "No objects found."}


def _shift(a: dict, b: dict) -> dict:
    """How far a moved object turned, as an angle only (nothing here knows a distance)."""
    dyaw, dpitch = A.wrap180(b["yaw"] - a["yaw"]), b["pitch"] - a["pitch"]
    words = f"{round(abs(dyaw))}° to the {'right' if dyaw > 0 else 'left'}"
    if abs(dpitch) >= 5:
        words += f" and {round(abs(dpitch))}° {'up' if dpitch > 0 else 'down'}"
    return {"yaw_deg": round(dyaw, 1), "pitch_deg": round(dpitch, 1), "words": words}


def _annotate(d: dict, reviews: dict[str, str]) -> None:
    """Give every change a stable key, the angular shift for moves, and the user's confirm / dismiss mark."""
    for kind in ("moved", "possibly_moved"):
        for e in d[kind]:
            e["key"] = f"{kind}:{e['from']['object_id']}:{e['to']['object_id']}"
            e["shift"] = _shift(e["from"]["anchor"], e["to"]["anchor"])
    for kind in ("removed", "added", "cant_compare"):
        for e in d[kind]:
            e["key"] = f"{kind}:{e['object_id']}"
    for kind in ("moved", "possibly_moved", "removed", "added"):
        for e in d[kind]:
            e["review"] = reviews.get(e["key"])


async def ask_room(svc: ScanService, question: str, room=None) -> dict:
    """A question about one room, answered from its memory (and what changed between its saved scans), with the objects it is about."""
    from app import ask as ask_mod
    rm, err = resolve_room(svc, room)
    if err:
        return err
    sid = svc.wm.latest_scan_id(rm["id"])
    return await ask_mod.ask_room(svc, svc.reasoner, question, rm, svc.wm.get_scan(sid) if sid is not None else None)


def list_notes(svc: ScanService, room=None, include_done: bool = False) -> dict:
    rm, err = resolve_room(svc, room)
    if err:
        return err
    return {"status": "ok", "room": _room_ref(rm), "notes": svc.wm.list_notes(rm["id"], include_done)}


def leave_note(svc: ScanService, text: str, yaw: float, pitch: float, room=None, label: str | None = None, author: str = "agent") -> dict:
    """Leave a note in a room, anchored to a direction. The person sees it over the camera, where it was left."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    try:
        return {"status": "ok", "room": _room_ref(rm), "note": svc.wm.add_note(rm["id"], text, yaw, pitch, label, author)}
    except ValueError as e:
        return {"status": "bad_request", "message": str(e)}


def reference_frame(svc: ScanService, room: dict) -> dict | None:
    """The reference keyframe of a room for lining up a rescan: the first frame of its memory (latest saved scan) that has a picture."""
    sid = svc.wm.latest_scan_id(room["id"])
    if sid is None:
        return None
    return next((f for f in svc.wm.list_frames(sid) if f.get("path")), None)


def reference(svc: ScanService, room=None) -> dict:
    """Where the phone gets the first scan's keyframe from, to show as a faded outline while lining up a rescan."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    f = reference_frame(svc, rm)
    if f is None:
        return {"status": "no_reference", "room": _room_ref(rm), "message": f"{rm['name']} has no saved scan to line up with."}
    return {"status": "ok", "room": _room_ref(rm), "scan_id": f["scan_id"], "frame_id": f["id"], "width": f["width"], "height": f["height"], "hfov_deg": f["hfov_deg"],
            "outline_url": f"/api/rooms/{rm['id']}/reference.png?frame={f['id']}"}


def what_changed(svc: ScanService, scan_a: int | None = None, scan_b: int | None = None, room=None) -> dict:
    """Compare two scans of ONE room: default the room's latest saved scan against its baseline (else the scan before it).
    Scans of different rooms are never compared (status wrong_room)."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    saved = [s for s in svc.wm.room_scans(rm["id"]) if s["status"] == "saved"]
    if scan_a is None or scan_b is None:
        pair = svc.wm.default_compare(rm["id"])
        if pair is None:
            return {"status": "need_two_scans", "room": _room_ref(rm), "scans_available": len(saved),
                    "message": f"Comparing needs two saved scans of {rm['name']}; scan it again after changing something."}
        scan_a, scan_b = (scan_a or pair[0]), (scan_b or pair[1])
    for sid in (scan_a, scan_b):
        s = svc.wm.get_scan(sid)
        if s is None:
            return {"status": "no_such_scan", "message": f"No scan {sid}."}
        if s["room_id"] != rm["id"]:
            return {"status": "wrong_room", "room": _room_ref(rm), "message": f"Scan {sid} is from {s['room_name']}, not {rm['name']}. Scans of different rooms cannot be compared."}
        if s["status"] != "saved":
            return {"status": "not_saved", "room": _room_ref(rm), "message": f"Scan {sid} has not been saved."}
    d = compare_scans(svc.wm, scan_a, scan_b)
    cant = _clean(d["cant_compare"])
    out = {"status": "ok", "room": _room_ref(rm), "scan_before": scan_a, "scan_after": scan_b, "summary": d["speech"], "alignment": _clean(d["alignment"]),
           "added": _clean(d["added"]), "removed": _clean(d["removed"]), "moved": _clean(d["moved"]), "possibly_moved": _clean(d["possibly_moved"]),
           "cant_compare": cant, "unverified": cant,   # the same list: "unverified" is the older name of "cant_compare"
           "unchanged_count": d["unchanged"], "scans": [{"id": s["id"], "started_at": s["started_at"], "baseline": bool(s["baseline"])} for s in saved]}
    _annotate(out, svc.wm.reviews(rm["id"], scan_a, scan_b))
    return out


def review_change(svc: ScanService, room, scan_a: int, scan_b: int, key: str, state: str | None) -> dict:
    """Confirm or dismiss one change (state None clears the mark)."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    if state not in ("confirmed", "dismissed", None):
        return {"status": "bad_request", "message": "state must be confirmed, dismissed or null"}
    for sid in (scan_a, scan_b):
        s = svc.wm.get_scan(sid)
        if s is None or s["room_id"] != rm["id"]:
            return {"status": "wrong_room", "room": _room_ref(rm), "message": f"Scan {sid} is not a scan of {rm['name']}."}
    svc.wm.set_review(rm["id"], scan_a, scan_b, key, state)
    return {"status": "ok", "key": key, "review": state}


def get_frame(svc: ScanService, frame_id: int) -> bytes | None:
    """The stored JPEG of a keyframe (it may show people); None if unknown."""
    return svc.wm.frame_jpeg(frame_id)


def overlay(svc: ScanService, scan_id: int | None = None, room=None) -> dict:
    """Everything the phone needs to draw one room's objects over the live camera. Positions stay angular (no metric
    distances exist). `age_s` is measured on the server clock; `hfov_deg` is the one FOV both sides must use."""
    cal = A.CALIBRATION or {}
    base = {"hfov_deg": A.DEFAULT_HFOV_DEG, "fov_calibrated": A.FOV_CALIBRATED,
            # the camera state the FOV is valid for (None where it was not recorded): the phone refuses to draw outside it
            "calibration": {k: cal.get(k) for k in ("width", "height", "zoom")} if cal.get("width") else None}
    rm, err = resolve_room(svc, room)
    if err:
        return {**base, "status": err["status"], "message": err["message"], "scan_id": None, "room": None, "objects": [], "notes": []}
    scan, err = _memory(svc, rm, scan_id)
    if err:
        return {**base, "status": err["status"], "message": err["message"], "scan_id": None, "room": _room_ref(rm), "objects": [], "notes": svc.wm.list_notes(rm["id"])}
    now = time.time()
    objs = [{**{k: v for k, v in o.items() if k != "last_seen"}, "age_s": round(max(0.0, now - o["last_seen"]), 1)}
            for o in svc.wm.overlay_objects(scan["id"])]
    return {**base, "status": "ok", "room": _room_ref(rm), "scan_id": scan["id"], "objects": objs, "notes": svc.wm.list_notes(rm["id"])}


def _thumb(svc: ScanService, o: dict, room: dict) -> dict:
    return {"id": o["id"], "label": o["label"], "yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1), "last_seen": o["last_seen"],
            "room": _room_ref(room), "replay": public_replay(svc.wm.replay(o["id"]))}


def search(svc: ScanService, q: str = "", room=None, limit: int = 40) -> dict:
    """Objects by name in the rooms' memories, grouped per room (read-only; nothing here opens or changes another room).
    No query lists the most recently seen objects. `room` limits the search to one room."""
    if room not in (None, ""):
        rm, err = resolve_room(svc, room)
        if err:
            return err
        rooms = [rm]
    else:
        rooms = [svc.wm.get_room(r["id"]) for r in svc.wm.list_rooms()]
    q = " ".join((q or "").split())
    groups = []
    for rm in rooms:
        sid = svc.wm.latest_scan_id(rm["id"])
        if sid is None:
            continue
        objs = [o for o in svc.wm.list_objects(sid) if not q or label_match_score(q, o["label"]) > 0 or q.casefold() in o["label"].casefold()]
        objs.sort(key=lambda o: o["last_seen"], reverse=True)
        if objs:
            groups.append({"room": _room_ref(rm), "objects": [_thumb(svc, o, rm) for o in objs[:limit]]})
    groups.sort(key=lambda g: (not g["room"]["active"], g["room"]["name"].casefold()))
    return {"status": "ok", "query": q, "groups": groups}


def room_detail(svc: ScanService, room_id: int) -> dict:
    """A room's page: its stats, a few recent objects with thumbnails, and what the scans look like."""
    rm = svc.wm.get_room(room_id)
    if rm is None:
        return {"status": "no_such_room", "message": f"There is no room {room_id}."}
    stats = svc.wm.room_stats(rm)
    recent = []
    if stats["scan_id"]:
        recent = sorted(svc.wm.list_objects(stats["scan_id"]), key=lambda o: o["last_seen"], reverse=True)[:8]
    return {"status": "ok", "room": stats, "recent_objects": [_thumb(svc, o, rm) for o in recent],
            "labels": dict(Counter(o["label"] for o in svc.wm.list_objects(stats["scan_id"])).most_common()) if stats["scan_id"] else {}}


def recent_objects(svc: ScanService, limit: int = 8) -> list[dict]:
    """The most recently seen objects across all rooms' memories (read-only, each tagged with its room)."""
    objs = [o for g in search(svc, "", None, limit)["groups"] for o in g["objects"]]
    return sorted(objs, key=lambda o: o["last_seen"], reverse=True)[:limit]


def guide_target(svc: ScanService, object_id: int, room=None) -> dict:
    """Check that the phone may be guided to this object: guidance only ever happens inside the active room, to an object of
    that room. Anything else is status wrong_room (HTTP 400)."""
    rm, err = resolve_room(svc, room)
    if err:
        return err
    active = svc.wm.active_room()
    if active is None or active["id"] != rm["id"]:
        return {"status": "wrong_room", "room": _room_ref(rm), "message": f"{rm['name']} is not the active room, so the phone cannot guide you there. Switch to it first."}
    o = svc.wm.get_object(object_id)
    if o is None:
        return {"status": "no_such_object", "message": f"No object {object_id}."}
    if svc.wm.scan_room_id(o["scan_id"]) != rm["id"]:
        return {"status": "wrong_room", "room": _room_ref(rm), "message": "That object is in another room. Guidance never crosses rooms."}
    if o["scan_id"] != svc.wm.latest_scan_id(rm["id"]):
        return {"status": "stale", "room": _room_ref(rm), "message": "That object is from an older scan, not the room's memory."}
    return {"status": "ok", "room": _room_ref(rm), "object": _object(o, time.time())}
