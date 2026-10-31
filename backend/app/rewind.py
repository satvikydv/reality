"""Rewind a room: what an earlier saved scan had that the room's memory does not, as ghosts in today's directions.

The earlier scan is aligned into the current scan's compass frame with the same landmark alignment Changes uses (changes.diff_scans).
A ghost is something that was there then and is not where it was now: gone, or moved (shown at its old place). Directions that the
current scan never looked at are not reported as gone but as "unsure": absence there proves nothing.
"""
from __future__ import annotations

from app.changes import compare_scans


def rewind(svc, room: dict, scan_id: int) -> dict:
    wm = svc.wm
    now_id = wm.latest_scan_id(room["id"])
    base = {"room": {"id": room["id"], "name": room["name"], "active": room["active"]}}
    if now_id is None:
        return {**base, "status": "no_scans", "message": f"{room['name']} has not been scanned yet."}
    then = wm.get_scan(scan_id)
    if then is None:
        return {**base, "status": "no_such_scan", "message": f"No scan {scan_id}."}
    if then["room_id"] != room["id"]:
        return {**base, "status": "wrong_room", "message": f"Scan {scan_id} belongs to {then['room_name']}, not {room['name']}. Rooms are never mixed."}
    if then["status"] != "saved":
        return {**base, "status": "not_saved", "message": "Only saved scans can be rewound to."}
    if scan_id == now_id:
        return {**base, "status": "current", "message": "That is the room's current memory. Pick an earlier scan.", "ghosts": []}
    if then["started_at"] > wm.get_scan(now_id)["started_at"]:
        return {**base, "status": "bad_request", "message": "That scan is newer than the room's memory."}
    d = compare_scans(wm, scan_id, now_id)
    when = then["started_at"]
    ghosts = []
    if d["alignment"]["quality"] != "good":  # nothing can be placed reliably: say so instead of drawing rings in the wrong places
        return {**base, "status": "ok", "then": {"id": then["id"], "started_at": when}, "now": {"id": now_id}, "alignment": d["alignment"], "ghosts": [],
                "summary": d["speech"]}
    for e in d["removed"]:
        ghosts.append({"label": e["label"], "yaw": round(e["anchor"]["yaw"], 1), "pitch": round(e["anchor"]["pitch"], 1), "kind": "gone", "unsure": False, "text": e["speech"]})
    for m in d["moved"] + d["possibly_moved"]:
        ghosts.append({"label": m["label"], "yaw": round(m["from"]["anchor"]["yaw"], 1), "pitch": round(m["from"]["anchor"]["pitch"], 1), "kind": "moved",
                       "unsure": m in d["possibly_moved"], "text": m["speech"]})
    for u in d["cant_compare"]:
        if u.get("was") == "removed?":
            ghosts.append({"label": u["label"], "yaw": round(u["anchor"]["yaw"], 1), "pitch": round(u["anchor"]["pitch"], 1), "kind": "gone", "unsure": True,
                           "text": f"The {u['label']} was there then; that direction was not rescanned, so it may still be."})
    return {**base, "status": "ok", "then": {"id": then["id"], "started_at": when}, "now": {"id": now_id}, "alignment": d["alignment"],
            "ghosts": ghosts, "summary": d["speech"]}
