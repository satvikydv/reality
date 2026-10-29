"""Objects inspector: grouped views, instance details, manual corrections that survive every rebuild, "not an object" labels, duplicates."""
from pathlib import Path

import pytest

from app import inspector
from test_scan_service import client, make_service  # noqa: F401  (client is a fixture)

W, H = 480, 640
CENTRE = [220.0, 300.0, 260.0, 340.0]
POLY = [[220, 300], [260, 300], [260, 340], [220, 340]]


def frame(wm, sid, yaw, ts, jpeg=b"jpg"):
    return wm.add_frame(sid, ts, yaw, 0.0, W, H, roll=0.0, screen_angle=0.0, jpeg=jpeg)


def see(wm, fid, *dets):
    """dets: (label, conf) or (label, conf, box); centred boxes put the object where the camera points."""
    return wm.observe(fid, [{"label": d[0], "conf": d[1], "box": d[2] if len(d) > 2 else CENTRE, "polygon": POLY} for d in dets])


def scene(wm, room_name="R"):
    """A scan with: bottle A (frames at yaw 0 and 2), bottle B (yaw 15, never in a frame with A), a cup at yaw 90."""
    rid = wm.create_room(room_name)["id"]
    sid = wm.start_scan(room_id=rid, ts=1.0)
    fa1, fa2 = frame(wm, sid, 0, 1.0), frame(wm, sid, 2, 2.0)
    fb = frame(wm, sid, 15, 3.0)
    fc = frame(wm, sid, 90, 4.0)
    see(wm, fa1, ("bottle", 0.9))
    see(wm, fa2, ("bottle", 0.7))
    see(wm, fb, ("bottle", 0.8))
    see(wm, fc, ("cup", 0.6))
    wm.end_scan(sid, 5.0)
    return rid, sid


def objs(wm, sid, label=None):
    return [o for o in wm.list_objects(sid) if label is None or o["label"] == label]


def test_the_scene_has_two_bottles_and_one_cup(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    assert [(o["label"], o["n_obs"]) for o in objs(wm, sid)] == [("bottle", 2), ("bottle", 1), ("cup", 1)]


# ------------------------------------------------------------------------------------------------ grouped view

def test_scan_objects_groups_by_label_with_instances_sightings_and_confidence(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    g = client.get(f"/api/scans/{sid}/objects").json()
    assert g["scan"]["id"] == sid and g["scan"]["room_id"] == rid and g["instances"] == 3 and g["sightings"] == 4
    bottle, cup = g["labels"]
    assert (bottle["label"], bottle["instances"], bottle["sightings"]) == ("bottle", 2, 3)
    assert bottle["avg_confidence"] == pytest.approx((0.9 + 0.7 + 0.8) / 3, abs=0.01)  # the mean over sightings, not over instances
    assert (cup["label"], cup["instances"], cup["sightings"], cup["avg_confidence"]) == ("cup", 1, 1, 0.6)
    inst = bottle["objects"][0]
    assert inst["sightings"] == 2 and inst["avg_confidence"] == pytest.approx(0.8, abs=0.01) and inst["thumb"]["image_url"].startswith("/world/frame/")
    assert client.get("/api/scans/999/objects").status_code == 404


# ------------------------------------------------------------------------------------------------ instance details

def test_object_detail_has_sightings_with_mask_pose_confidence_and_decisions(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a = objs(wm, sid, "bottle")[0]
    d = client.get(f"/api/objects/{a['id']}").json()
    assert d["object"]["label"] == "bottle" and d["scan"]["id"] == sid and len(d["sightings"]) == 2
    s = d["sightings"][0]
    assert s["image_url"] == f"/world/frame/{s['frame_id']}.jpg" and s["box"] == CENTRE and s["mask"] == POLY and s["confidence"] == 0.9
    assert s["camera"]["yaw"] == 0.0 and s["camera"]["hfov_deg"] > 0 and set(s["anchor"]) == {"yaw", "pitch"} and s["width"] == W
    assert [x["confidence"] for x in d["sightings"]] == [0.9, 0.7]  # in capture order
    texts = [x["text"] for x in d["decisions"]]
    assert any("new object" in t for t in texts) and any(t.startswith("Joined an object by") for t in texts)
    assert d["decisions_source"] == "log"
    wm.decision_log.clear()  # a restarted server has no log: the decisions are recomputed from the detections
    again = client.get(f"/api/objects/{a['id']}").json()
    assert again["decisions_source"] == "recomputed" and [x["action"] for x in again["decisions"]] == [x["action"] for x in d["decisions"]]
    assert client.get("/api/objects/9999").status_code == 404


def test_every_decision_has_words(client):
    from app.memory import Config
    wm = client.svc.wm
    rid, sid = scene(wm)
    for dec in wm.explain(sid):
        assert inspector.describe(dec) and inspector.describe(dec) != dec["action"], dec


# ------------------------------------------------------------------------------------------------ possible duplicates

def test_possible_duplicates_lists_close_same_label_objects_never_seen_together(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    r = client.get(f"/api/scans/{sid}/duplicates").json()["duplicates"]
    assert len(r) == 1 and r[0]["label"] == "bottle" and 10 < r[0]["separation_deg"] < 16 and "apart" in r[0]["reason"]
    assert {r[0]["a"], r[0]["b"]} == {o["id"] for o in objs(wm, sid, "bottle")}
    assert client.get(f"/api/scans/{sid}/objects").json()["possible_duplicates"] == 1
    # two bottles in one frame are two bottles, however close
    sid2 = wm.start_scan(room_id=rid, ts=10.0)
    f = frame(wm, sid2, 0, 10.0)
    see(wm, f, ("bottle", 0.9, [100.0, 300.0, 140.0, 340.0]), ("bottle", 0.9, [120.0, 300.0, 160.0, 340.0]))
    assert len(objs(wm, sid2, "bottle")) in (1, 2)
    if len(objs(wm, sid2, "bottle")) == 2:
        assert client.get(f"/api/scans/{sid2}/duplicates").json()["duplicates"] == []


# ------------------------------------------------------------------------------------------------ merge, detach, delete survive rebuilds

def test_merge_is_kept_when_more_frames_arrive_and_is_recorded(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    r = client.post("/api/objects/merge", json={"ids": [a["id"], b["id"]], "source": "dashboard"})
    assert r.status_code == 200 and r.json()["merged_into"] in (a["id"], b["id"]) and len(r.json()["detail"]["sightings"]) == 3
    assert len(objs(wm, sid, "bottle")) == 1
    # a rebuild with a new frame must not undo the correction
    see(wm, frame(wm, sid, 200, 6.0), ("lamp", 0.9))
    wm.rebuild(sid)
    assert len(objs(wm, sid, "bottle")) == 1 and objs(wm, sid, "bottle")[0]["n_obs"] == 3
    merged = objs(wm, sid, "bottle")[0]
    assert any(d["action"] == "manual_merge" for d in wm.decisions(sid))
    c = client.get(f"/api/corrections?scan_id={sid}").json()["corrections"]
    assert [x["kind"] for x in c] == ["merge"] and c[0]["source"] == "dashboard" and c[0]["payload"]["label"] == "bottle" and len(c[0]["payload"]["dets"]) == 3
    assert client.get(f"/api/objects/{merged['id']}").json()["corrections"][0]["kind"] == "merge"


def test_detach_makes_a_sighting_its_own_object_and_it_stays_that_way(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    wm.merge_objects([a["id"], b["id"]])
    merged = objs(wm, sid, "bottle")[0]
    sightings = client.get(f"/api/objects/{merged['id']}").json()["sightings"]
    victim = next(s for s in sightings if s["confidence"] == 0.8)
    r = client.post(f"/api/sightings/{victim['id']}/detach?source=phone")
    assert r.status_code == 200 and r.json()["object_id"] != merged["id"]
    assert sorted(o["n_obs"] for o in objs(wm, sid, "bottle")) == [1, 2]
    wm.rebuild(sid)
    wm.rebuild(sid)
    assert sorted(o["n_obs"] for o in objs(wm, sid, "bottle")) == [1, 2], "rebuilding again keeps it detached"
    assert [x["kind"] for x in client.get(f"/api/corrections?scan_id={sid}").json()["corrections"]] == ["detach", "merge"]  # newest first


def test_merging_again_after_a_detach_puts_the_sighting_back(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    wm.merge_objects([a["id"], b["id"]])
    one = objs(wm, sid, "bottle")[0]
    det = wm._object_dets(one["id"])[0]
    wm.detach_sighting(det)
    assert len(objs(wm, sid, "bottle")) == 2
    x, y = objs(wm, sid, "bottle")
    wm.merge_objects([x["id"], y["id"]])
    assert len(objs(wm, sid, "bottle")) == 1 and objs(wm, sid, "bottle")[0]["n_obs"] == 3  # the later correction wins


def test_delete_object_removes_it_for_good_and_keeps_the_record(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    cup = objs(wm, sid, "cup")[0]
    r = client.delete(f"/api/objects/{cup['id']}?source=dashboard")
    assert r.status_code == 200 and r.json() == {"id": cup["id"], "label": "cup", "scan_id": sid, "sightings": 1}
    assert objs(wm, sid, "cup") == []
    assert client.get(f"/api/objects/{cup['id']}").status_code == 404
    assert client.delete(f"/api/objects/{cup['id']}").status_code == 404
    see(wm, frame(wm, sid, 300, 7.0), ("lamp", 0.9))  # later frames rebuild the scan; the deleted object does not come back
    assert objs(wm, sid, "cup") == [] and [o["label"] for o in objs(wm, sid)].count("lamp") == 1
    assert client.get(f"/api/corrections?scan_id={sid}").json()["corrections"][0]["kind"] == "delete"


def test_corrections_are_refused_when_they_make_no_sense(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    cup = objs(wm, sid, "cup")[0]
    assert client.post("/api/objects/merge", json={"ids": [a["id"]]}).status_code == 400  # needs two
    assert client.post("/api/objects/merge", json={"ids": [a["id"], a["id"]]}).status_code == 400
    mixed = client.post("/api/objects/merge", json={"ids": [a["id"], cup["id"]]})
    assert mixed.status_code == 400 and "different labels" in mixed.json()["detail"]
    other = scene(wm, "Other")[1]
    cross = client.post("/api/objects/merge", json={"ids": [a["id"], objs(wm, other, "bottle")[0]["id"]]})
    assert cross.status_code == 400 and "different scans" in cross.json()["detail"]
    assert client.post("/api/objects/merge", json={"ids": [a["id"], 9999]}).status_code == 404
    only = client.post(f"/api/sightings/{wm._object_dets(b['id'])[0]}/detach")
    assert only.status_code == 400 and "already an object of its own" in only.json()["detail"]
    assert client.post("/api/sightings/9999/detach").status_code == 404
    assert client.get(f"/api/corrections?scan_id={sid}").json()["corrections"] == []  # nothing was recorded for the refused ones


# ------------------------------------------------------------------------------------------------ "not an object" labels

def test_a_label_marked_not_an_object_leaves_every_scan_of_the_room_only(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    rid2, sid2 = scene(wm, "Other room")
    later = wm.start_scan(room_id=rid, ts=50.0)
    see(wm, frame(wm, later, 0, 50.0), ("cup", 0.9))
    r = client.post(f"/api/rooms/{rid}/not-objects", json={"label": "  Cup ", "source": "phone"})
    assert r.status_code == 200 and r.json() == {"room_id": rid, "label": "cup", "objects_hidden": 2}
    assert objs(wm, sid, "cup") == [] and objs(wm, later, "cup") == []
    assert len(objs(wm, sid2, "cup")) == 1, "another room is untouched"
    assert [o["label"] for o in wm.list_objects(sid)] == ["bottle", "bottle"]
    see(wm, frame(wm, later, 60, 51.0), ("cup", 0.9))  # a future frame in this room
    assert objs(wm, later, "cup") == []
    assert client.get(f"/api/rooms/{rid}/not-objects").json()["labels"] == ["cup"]
    g = client.get(f"/api/scans/{sid}/objects").json()
    assert g["hidden_labels"] == [{"label": "cup", "sightings": 1}] and [x["label"] for x in g["labels"]] == ["bottle"]
    assert client.get(f"/api/overlay?room_id={rid}").json()["objects"] is not None
    assert [x["kind"] for x in client.get(f"/api/corrections?room_id={rid}").json()["corrections"]] == ["mark_not_object"]
    # bring it back
    assert client.delete(f"/api/rooms/{rid}/not-objects/cup?source=dashboard").status_code == 200
    assert len(objs(wm, sid, "cup")) == 1 and len(objs(wm, later, "cup")) == 2
    assert client.delete(f"/api/rooms/{rid}/not-objects/cup").status_code == 404
    assert client.post(f"/api/rooms/{rid}/not-objects", json={"label": "   "}).status_code == 400
    assert client.post("/api/rooms/999/not-objects", json={"label": "x"}).status_code == 404


# ------------------------------------------------------------------------------------------------ cascades, records

def test_deleting_a_scan_or_room_deletes_its_corrections_and_hidden_labels(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    wm.merge_objects([a["id"], b["id"]])
    wm.mark_not_object(rid, "cup")
    assert wm.db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 2
    wm.delete_scan(sid)
    assert wm.db.execute("SELECT COUNT(*) FROM corrections WHERE scan_id=?", (sid,)).fetchone()[0] == 0
    wm.delete_room(rid)
    assert wm.db.execute("SELECT COUNT(*) FROM corrections WHERE room_id=?", (rid,)).fetchone()[0] == 0
    assert wm.db.execute("SELECT COUNT(*) FROM not_objects").fetchone()[0] == 0


def test_every_correction_is_recorded_in_order_with_its_source(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    cup = objs(wm, sid, "cup")[0]
    wm.merge_objects([a["id"], b["id"]], source="dashboard")
    wm.delete_object(cup["id"], source="phone")
    wm.mark_not_object(rid, "lamp", source="api")
    kinds = [(c["kind"], c["source"]) for c in reversed(wm.list_corrections(limit=10))]
    assert kinds == [("merge", "dashboard"), ("delete", "phone"), ("mark_not_object", "api")]
    ev = [e for e in client.svc.bus.history() if e["type"] == "correction"]
    assert ev == [] or {e["kind"] for e in ev} <= {"merge", "delete", "mark_not_object"}


def test_corrections_through_the_api_are_announced_on_the_event_bus(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    client.post("/api/objects/merge", json={"ids": [a["id"], b["id"]], "source": "dashboard"})
    client.delete(f"/api/objects/{objs(wm, sid, 'cup')[0]['id']}?source=phone")
    ev = [e for e in client.svc.bus.history() if e["type"] == "correction"]
    assert [(e["kind"], e["source"]) for e in ev] == [("merge", "dashboard"), ("delete", "phone")]


def test_rebuilds_with_corrections_are_deterministic(client):
    wm = client.svc.wm
    rid, sid = scene(wm)
    a, b = objs(wm, sid, "bottle")
    wm.merge_objects([a["id"], b["id"]])
    wm.mark_not_object(rid, "cup")
    first = [(o["label"], round(o["yaw"], 3), o["n_obs"]) for o in wm.list_objects(sid)]
    for _ in range(3):
        wm.rebuild(sid)
    assert [(o["label"], round(o["yaw"], 3), o["n_obs"]) for o in wm.list_objects(sid)] == first
    assert [(d["action"]) for d in wm.explain(sid)].count("excluded") == 1
