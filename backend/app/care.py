"""Memory for someone who forgets: a caregiver's view of where a person's important things were last seen.

This is what the last scans saw, not live tracking: every answer says "last seen" and when. Nothing here is visible until the person
turns caregiver sharing on (a setting on their phone); until then the watch list can be edited but shows no places and no photos.
The consent is a gate in this app, not a login: on a shared computer it protects against accidents, not against someone with access to it.

An item raises an alert when it was not seen in any room's last scan, when it was last seen in a room other than the one it usually
lives in, or when its last sighting is older than STALE_DAYS.
"""
from __future__ import annotations

import os
import time

from app.reality_api import _room_ref, public_replay
from app.resolver import resolve_concept
from app.world_model import label_match_score, normalize_label

CONSENT_KEY = "care_consent"
STALE_DAYS = float(os.getenv("CARE_STALE_DAYS", "3"))


def consent(svc) -> dict:
    v = svc.wm.get_setting(CONSENT_KEY)
    return {"on": v is not None, "since": float(v) if v else None}


def set_consent(svc, on: bool, ts: float | None = None) -> dict:
    svc.wm.set_setting(CONSENT_KEY, str(time.time() if ts is None else ts) if on else None)
    return consent(svc)


def _sightings(svc, label: str, now: float) -> list[dict]:
    """Where `label` is in each room's memory (latest saved scan), most recently seen first. Only that scan: older ones are history."""
    out = []
    for r in svc.wm.list_rooms():
        sid = svc.wm.latest_scan_id(r["id"])
        if sid is None:
            continue
        hits = [o for o in svc.wm.list_objects(sid) if label_match_score(label, o["label"]) > 0]
        if not hits:
            continue
        o = max(hits, key=lambda x: (x["last_seen"], x["conf_best"]))
        out.append({"room": _room_ref(svc.wm.get_room(r["id"])), "object_id": o["id"], "label": o["label"], "yaw": round(o["yaw"], 1), "pitch": round(o["pitch"], 1),
                    "confidence": round(o["conf_best"], 2), "last_seen": o["last_seen"], "age_seconds": round(max(0.0, now - o["last_seen"])),
                    "where": (svc.wm.where_is(o["label"], (), sid) or {}).get("speech", ""), "replay": public_replay(svc.wm.replay(o["id"]))})
    return sorted(out, key=lambda s: -s["last_seen"])


def _days(seconds: float) -> str:
    d = seconds / 86400
    return "today" if d < 1 else "1 day ago" if d < 2 else f"{int(d)} days ago"


def item_status(svc, w: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    usual = svc.wm.get_room(w["usual_room_id"]) if w["usual_room_id"] else None
    seen = _sightings(svc, w["label"], now)
    alerts: list[dict] = []
    if not seen:
        scanned = [r for r in svc.wm.list_rooms() if r["scan_id"]]
        cov = ", ".join(f"{r['name']} {r['coverage_pct']}%" for r in scanned) or "no room has been scanned"
        alerts.append({"code": "not_seen", "message": f"Not seen in the last scan of any room (covered: {cov}). It may be in a part nobody scanned."})
    else:
        best = seen[0]
        if usual and best["room"]["id"] != usual["id"]:
            alerts.append({"code": "unusual_place", "message": f"Last seen in {best['room']['name']}, but it usually lives in {usual['name']}."})
        if best["age_seconds"] > STALE_DAYS * 86400:
            alerts.append({"code": "stale", "message": f"Last seen {_days(best['age_seconds'])}."})
    return {"id": w["id"], "label": w["label"], "usual_room": _room_ref(usual) if usual else None, "seen": seen, "last_seen": seen[0]["last_seen"] if seen else None,
            "alerts": alerts, "status": "alert" if alerts else "ok"}


def care_state(svc, now: float | None = None) -> dict:
    c = consent(svc)
    items = svc.wm.list_watches()
    if not c["on"]:
        return {"status": "no_consent", "consent": c, "message": "Caregiver sharing is off. The person turns it on in Settings on their phone; until then no places or photos are shown.",
                "items": [{"id": w["id"], "label": w["label"], "usual_room_id": w["usual_room_id"]} for w in items], "alerts": [], "stale_days": STALE_DAYS}
    st = [item_status(svc, w, now) for w in items]
    return {"status": "ok", "consent": c, "stale_days": STALE_DAYS, "items": st,
            "alerts": [{"item_id": i["id"], "label": i["label"], **a} for i in st for a in i["alerts"]], "as_of": "the last scan of each room"}


async def care_ask(svc, question: str, now: float | None = None) -> dict:
    """'Where did Mum put her pills?': the most recent sighting across rooms, in a sentence, with its photo. Needs consent."""
    c = consent(svc)
    q = " ".join((question or "").split())[:300]
    if not c["on"]:
        return {"status": "no_consent", "question": q, "message": "Caregiver sharing is off."}
    if not q:
        return {"status": "bad_request", "question": q, "message": "Ask a question."}
    now = time.time() if now is None else now
    labels = sorted({o["label"] for r in svc.wm.list_rooms() if r["scan_id"] for o in svc.wm.list_objects(r["scan_id"])})
    nq = normalize_label(q)
    concept = nq if nq in labels else None
    if concept is None:
        res, _ = await resolve_concept(svc.reasoner, q, labels)
        concept = res.concept
    if not concept:
        return {"status": "unresolved", "question": q, "message": "That does not name something to look for."}
    seen = _sightings(svc, concept, now)
    if not seen:
        return {"status": "not_seen", "question": q, "concept": concept, "message": f"No {concept} in the last scan of any room. It may be somewhere nobody scanned.", "seen": []}
    b = seen[0]
    return {"status": "found", "question": q, "concept": concept, "seen": seen,
            "answer": f"{(b['where'] or 'The ' + b['label'] + ' was last seen').rstrip('.')}, in {b['room']['name']}, {_days(b['age_seconds'])}. This is what the last scan saw, not live."}
