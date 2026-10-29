"""Rooms ("spaces"): a room's memory is its latest saved scan, and rooms never share identity, overlay, guidance or comparison."""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app import main, reality_api as api
from app.mcp_server import build_server
from app.world_model import RoomNameError, WorldModel
from test_scan_service import client, frame_msg, jpeg, make_service, meta, save_drafts  # noqa: F401  (client is a fixture)

W, H = 480, 640


def box(cx=240, cy=320):
    return [cx - 20, cy - 20, cx + 20, cy + 20]


def add_scan(wm, room_id, label="mug", yaw=0.0, status="saved", ts=1.0, frames=1):
    """A scan stored straight in the world model, with one frame (and its JPEG) and one object."""
    sid = wm.start_scan(ts=ts, room_id=room_id, status=status)
    for i in range(frames):
        fid = wm.add_frame(sid, ts + i, yaw, 0.0, W, H, roll=0.0, screen_angle=0.0, jpeg=b"jpg")
        wm.observe(fid, [{"label": label, "box": box()}])
    wm.end_scan(sid, ts + 5)
    return sid


def run(coro):
    return asyncio.run(coro)


def live_scan(client, ws, room_id, yaws=(0,), save=True):
    """A scan through the WebSocket, the way the phone makes it; returns the summary."""
    ws.send_text(json.dumps({"type": "scan_start", "room_id": room_id}))
    started = ws.receive_json()
    assert started["type"] == "scan_started" and started["room_id"] == room_id
    for i, yaw in enumerate(yaws):
        ws.send_bytes(frame_msg(i + 1, 2, meta(yaw), jpeg()))
    ws.send_text(json.dumps({"type": "scan_end"}))
    while True:
        m = ws.receive_json()
        if m["type"] == "scan_summary":
            break
    if save:
        assert client.post(f"/api/rooms/{room_id}/scans/{m['scan_id']}/save").status_code == 200
    return m


# ------------------------------------------------------------------------------------------------ rooms and the active room

def test_create_rename_activate_and_names(client):
    r = client.post("/api/rooms", json={"name": "  Living   Room "})
    assert r.status_code == 201 and r.json()["name"] == "Living Room" and r.json()["active"] is False
    desk = client.post("/api/rooms", json={"name": "Desk", "active": True}).json()
    assert desk["active"] is True
    assert client.post("/api/rooms", json={"name": "living room"}).status_code == 409  # names are unique, case aside
    assert client.post("/api/rooms", json={"name": "   "}).status_code == 400
    assert client.post("/api/rooms", json={"name": "x" * 61}).status_code == 400

    rooms = client.get("/api/rooms").json()
    assert rooms["active_id"] == desk["id"] and sum(r["active"] for r in rooms["rooms"]) == 1  # exactly one active room
    lr = next(r for r in rooms["rooms"] if r["name"] == "Living Room")
    assert client.patch(f"/api/rooms/{lr['id']}", json={"active": True}).json()["active"] is True
    assert [r["active"] for r in client.get("/api/rooms").json()["rooms"] if r["id"] == desk["id"]] == [False]  # the old one let go
    assert client.patch(f"/api/rooms/{lr['id']}", json={"name": "Lounge"}).json()["name"] == "Lounge"
    assert client.patch(f"/api/rooms/{lr['id']}", json={"name": "Desk"}).status_code == 409
    assert client.patch(f"/api/rooms/{lr['id']}", json={"active": False}).json()["active"] is False
    assert client.get("/api/rooms").json()["active_id"] is None
    assert client.patch("/api/rooms/999", json={"name": "x"}).status_code == 404 and client.get("/api/rooms/999").status_code == 404


def test_creating_a_room_does_not_make_it_active(client):
    before = client.get("/api/rooms").json()["active_id"]
    client.post("/api/rooms", json={"name": "Wardrobe"})
    assert client.get("/api/rooms").json()["active_id"] == before  # the user sets the active room explicitly


# ------------------------------------------------------------------------------------------------ migration

def test_scans_from_before_rooms_move_into_unassigned(tmp_path):
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE scans (id INTEGER PRIMARY KEY, name TEXT, source TEXT, started_at REAL NOT NULL, ended_at REAL);
        CREATE TABLE frames (id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL, ts REAL NOT NULL, yaw REAL NOT NULL, pitch REAL NOT NULL,
          roll REAL, screen_angle REAL, source TEXT, api TEXT, width INTEGER NOT NULL, height INTEGER NOT NULL, hfov_deg REAL NOT NULL, path TEXT);
        INSERT INTO scans (id, name, started_at, ended_at) VALUES (1, 'old office scan', 100, 140), (2, 'old second', 200, 240);
        INSERT INTO frames (scan_id, ts, yaw, pitch, width, height, hfov_deg) VALUES (1, 101, 0, 0, 480, 640, 60), (2, 201, 90, 0, 480, 640, 60);
    """)
    con.commit()
    con.close()
    wm = WorldModel(db)
    rooms = wm.list_rooms()
    assert [r["name"] for r in rooms] == ["Unassigned"] and rooms[0]["active"] is False
    assert rooms[0]["n_scans"] == 2 and wm.active_room() is None  # nothing is active until the user chooses
    assert {s["room_id"] for s in wm.list_scans()} == {rooms[0]["id"]} and {s["status"] for s in wm.list_scans()} == {"saved"}
    assert wm.latest_scan_id(rooms[0]["id"]) == 2
    WorldModel(db).close()  # opening again does not create a second Unassigned room or move anything
    assert [r["name"] for r in WorldModel(db).list_rooms()] == ["Unassigned"]


def test_a_fresh_database_has_no_rooms_and_no_unassigned(tmp_path):
    assert WorldModel(tmp_path / "new.sqlite").list_rooms() == []


# ------------------------------------------------------------------------------------------------ isolation

def test_a_scan_in_one_room_is_not_found_in_another(client):
    wm = client.svc.wm
    a = wm.create_room("Room A")["id"]
    b = wm.create_room("Room B")["id"]
    add_scan(wm, a, "mug")
    wm.set_active(b)

    async def go():
        return await client.svc.find("mug", room_id=b), await client.svc.find("mug", room_id=a)

    in_b, in_a = run(go())
    assert in_a["found"] is True and in_a["room_name"] == "Room A"
    assert in_b["found"] is False and in_b["room_name"] == "Room B" and in_b["checked"] == {"scans": 0, "covered_deg": 0, "scan_id": None}
    assert "has not been scanned yet" in in_b["speech"]

    assert run(api.where_is(client.svc, "mug", room="Room B"))["status"] == "no_scans"
    assert run(api.where_is(client.svc, "mug", room="Room A"))["status"] == "found"
    assert api.list_objects(client.svc, room="Room B")["status"] == "no_scans"
    ov = client.get(f"/api/overlay?room_id={b}").json()
    assert ov["objects"] == [] and ov["room"]["id"] == b
    assert [o["label"] for o in client.get(f"/api/overlay?room_id={a}").json()["objects"]] == ["mug"]


def test_a_room_with_other_contents_never_answers_with_another_rooms_object(client):
    wm = client.svc.wm
    a, b = wm.create_room("A")["id"], wm.create_room("B")["id"]
    add_scan(wm, a, "mug")
    add_scan(wm, b, "laptop")
    r = run(api.where_is(client.svc, "mug", room=b))
    assert r["status"] == "not_seen" and r["room"]["id"] == b  # B was scanned, and its memory has no mug
    assert api.list_objects(client.svc, room=b)["objects"][0]["label"] == "laptop"
    assert api.scan_summary(client.svc, room=a)["counts"] == {"mug": 1}


def test_a_scan_id_from_another_room_is_refused(client):
    wm = client.svc.wm
    a, b = wm.create_room("A")["id"], wm.create_room("B")["id"]
    sa = add_scan(wm, a)
    add_scan(wm, b, "laptop")
    assert api.list_objects(client.svc, scan_id=sa, room=b)["status"] == "wrong_room"
    assert api.scan_summary(client.svc, scan_id=sa, room=b)["status"] == "wrong_room"
    assert client.get(f"/api/overlay?room_id={b}&scan_id={sa}").json()["status"] == "wrong_room"


def test_the_memory_is_the_latest_saved_scan_and_older_ones_are_kept(client):
    wm = client.svc.wm
    a = wm.create_room("A", active=True)["id"]
    old = add_scan(wm, a, "scissors", ts=10)
    new = add_scan(wm, a, "mug", ts=20)
    assert wm.latest_scan_id(a) == new
    assert wm.where_is("scissors", scan_id=wm.latest_scan_id(a)) is None  # the older scan (scissors) does not answer for the room
    assert [s["id"] for s in wm.room_scans(a)] == [new, old]  # but it is still there for What changed
    draft = add_scan(wm, a, "keys", ts=30, status="draft")
    assert wm.latest_scan_id(a) == new and [s["status"] for s in wm.room_scans(a)][0] == "draft"  # a draft is not the memory


# ------------------------------------------------------------------------------------------------ save and discard

def test_a_scan_is_a_draft_until_saved_and_discard_deletes_it(client):
    rid = client.svc.wm.active_room()["id"]
    with client.websocket_connect("/ws") as ws:
        m = live_scan(client, ws, rid, save=False)
        assert m["status"] == "draft"
        ws.send_text(json.dumps({"type": "find", "query": "mug", "room_id": rid}))
        assert ws.receive_json()["found"] is False  # not part of the room's memory yet
        assert client.get("/api/overlay").json()["objects"] == []
        sid = m["scan_id"]
        assert client.post(f"/api/rooms/{rid}/scans/{sid}/save").json() == {"saved": sid}
        ws.send_text(json.dumps({"type": "find", "query": "mug", "room_id": rid}))
        assert ws.receive_json()["found"] is True
        assert client.post(f"/api/rooms/{rid}/scans/{sid}/save").json()["already"] is True
        assert client.post(f"/api/rooms/{rid}/scans/{sid}/discard").status_code == 409  # a saved scan is deleted, not discarded

        m2 = live_scan(client, ws, rid, yaws=(40,), save=False)
        assert client.post(f"/api/rooms/{rid}/scans/{m2['scan_id']}/discard").json() == {"discarded": m2["scan_id"]}
    assert [s["id"] for s in client.get(f"/api/rooms/{rid}/scans").json()["scans"]] == [sid]


def test_save_and_discard_check_the_room_and_the_scan(client):
    wm = client.svc.wm
    a, b = wm.create_room("A")["id"], wm.create_room("B")["id"]
    d = add_scan(wm, a, status="draft")
    assert client.post(f"/api/rooms/{b}/scans/{d}/save").status_code == 400  # not B's scan
    assert client.post(f"/api/rooms/{a}/scans/999/save").status_code == 404
    empty = wm.start_scan(room_id=a, status="draft")
    assert client.post(f"/api/rooms/{a}/scans/{empty}/save").status_code == 400  # nothing was stored


def test_a_scan_needs_a_room(client):
    wm = client.svc.wm
    wm.set_active(None)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "scan_start"}))
        m = ws.receive_json()
        assert m["type"] == "error" and "choose a room" in m["message"]
        ws.send_text(json.dumps({"type": "scan_start", "room_id": 999}))
        assert "no such room" in ws.receive_json()["message"]
        ws.send_text(json.dumps({"type": "find", "query": "mug"}))
        r = ws.receive_json()
        assert r["no_room"] is True and r["found"] is False and "Choose a room" in r["speech"]


# ------------------------------------------------------------------------------------------------ delete cascades

def test_deleting_a_room_erases_its_scans_frames_photos_and_objects(client):
    wm = client.svc.wm
    a, b = wm.create_room("A")["id"], wm.create_room("B")["id"]
    sa1, sa2 = add_scan(wm, a, ts=1, frames=2), add_scan(wm, a, ts=10)
    sb = add_scan(wm, b, "laptop")
    files = [Path(f["path"]) for sid in (sa1, sa2) for f in wm.list_frames(sid)]
    keep = Path(wm.list_frames(sb)[0]["path"])
    assert files and all(p.exists() for p in files)
    wm.set_review(a, sa1, sa2, "moved:1:2", "confirmed")

    assert client.delete(f"/api/rooms/{a}").json() == {"deleted": a}
    assert not any(p.exists() for p in files) and keep.exists()  # A's photos are gone, B's are untouched
    for table in ("scans", "frames", "objects", "detections", "observations", "change_reviews"):
        col = "room_id" if table in ("scans", "change_reviews") else "scan_id"
        if table == "observations":
            assert wm.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1  # only B's object's sighting is left
        else:
            assert wm.db.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (a,) if table in ("scans", "change_reviews") else (sa1,)).fetchone()[0] == 0
    assert wm.get_room(a) is None and wm.get_room(b) is not None and wm.latest_scan_id(b) == sb
    assert client.delete(f"/api/rooms/{a}").status_code == 404


def test_deleting_the_active_room_leaves_no_active_room(client):
    rid = client.svc.wm.active_room()["id"]
    add_scan(client.svc.wm, rid)
    client.delete(f"/api/rooms/{rid}")
    assert client.get("/api/rooms").json()["active_id"] is None


def test_delete_all_data_removes_every_room_and_scan(client):
    wm = client.svc.wm
    for n in ("A", "B"):
        add_scan(wm, wm.create_room(n)["id"])
    files = [Path(f["path"]) for s in wm.list_scans() for f in wm.list_frames(s["id"])]
    r = client.delete("/api/data").json()
    assert r["rooms"] >= 2 and wm.list_rooms() == [] and wm.list_scans() == [] and not any(p.exists() for p in files)


def test_deleting_a_scan_from_the_history(client):
    wm = client.svc.wm
    a = wm.create_room("A")["id"]
    s1, s2 = add_scan(wm, a, ts=1), add_scan(wm, a, ts=10)
    b = wm.create_room("B")["id"]
    assert client.delete(f"/api/rooms/{b}/scans/{s1}").status_code == 400  # not B's scan
    assert client.delete(f"/api/rooms/{a}/scans/{s2}").json() == {"deleted": [s2]}
    assert wm.latest_scan_id(a) == s1  # deleting the newest hands the memory back to the one before


# ------------------------------------------------------------------------------------------------ history, baseline, changes

def test_baseline_and_default_comparison(client):
    wm = client.svc.wm
    a = wm.create_room("A")["id"]
    s1, s2, s3 = (add_scan(wm, a, yaw=y, ts=t) for y, t in ((0, 1), (90, 10), (180, 20)))
    assert wm.default_compare(a) == (s2, s3)  # the scan before the latest
    assert client.post(f"/api/rooms/{a}/baseline", json={"scan_id": s1}).json()["baseline"] == s1
    assert wm.default_compare(a) == (s1, s3)
    assert [s["baseline"] for s in client.get(f"/api/rooms/{a}/scans").json()["scans"]] == [0, 0, 1]
    assert client.post(f"/api/rooms/{a}/baseline", json={"scan_id": add_scan(wm, wm.create_room("B")["id"])}).status_code == 400
    assert client.post(f"/api/rooms/{a}/baseline", json={"scan_id": None}).status_code == 200 and wm.default_compare(a) == (s2, s3)


def test_changes_within_a_room_report_the_move_as_an_angle_and_can_be_reviewed(client):
    wm = client.svc.wm
    a = wm.create_room("A")["id"]
    s1, s2 = add_scan(wm, a, yaw=0, ts=1), add_scan(wm, a, yaw=60, ts=10)
    r = client.get(f"/api/rooms/{a}/changes").json()
    assert r["status"] == "ok" and r["room"]["id"] == a and (r["scan_before"], r["scan_after"]) == (s1, s2)
    mv = r["moved"][0]
    assert mv["shift"]["yaw_deg"] == pytest.approx(60, abs=1) and "to the right" in mv["shift"]["words"] and "m" not in mv["shift"]["words"].split()
    assert mv["review"] is None and mv["key"].startswith("moved:")
    ok = client.post(f"/api/rooms/{a}/changes/review", json={"before": s1, "after": s2, "key": mv["key"], "state": "confirmed"}).json()
    assert ok == {"status": "ok", "key": mv["key"], "review": "confirmed"}
    assert client.get(f"/api/rooms/{a}/changes").json()["moved"][0]["review"] == "confirmed"
    client.post(f"/api/rooms/{a}/changes/review", json={"before": s1, "after": s2, "key": mv["key"], "state": "dismissed"})
    assert client.get(f"/api/rooms/{a}/changes").json()["moved"][0]["review"] == "dismissed"
    client.post(f"/api/rooms/{a}/changes/review", json={"before": s1, "after": s2, "key": mv["key"], "state": None})
    assert client.get(f"/api/rooms/{a}/changes").json()["moved"][0]["review"] is None
    assert client.post(f"/api/rooms/{a}/changes/review", json={"before": s1, "after": s2, "key": "k", "state": "maybe"}).status_code == 400


def test_one_saved_scan_cannot_be_compared(client):
    a = client.svc.wm.create_room("A")["id"]
    add_scan(client.svc.wm, a)
    r = client.get(f"/api/rooms/{a}/changes").json()
    assert r["status"] == "need_two_scans" and "two saved scans" in r["message"]


def test_cross_room_compare_is_rejected(client):
    wm = client.svc.wm
    a, b = wm.create_room("A")["id"], wm.create_room("B")["id"]
    sa1, sa2, sb = add_scan(wm, a, ts=1), add_scan(wm, a, ts=10), add_scan(wm, b, ts=5)
    r = client.get(f"/api/changes?before={sa1}&after={sb}")
    assert r.status_code == 400 and "different rooms" in r.json()["detail"]
    assert client.get(f"/api/rooms/{a}/changes?before={sa1}&after={sb}").status_code == 400  # B's scan in A's comparison
    assert client.get(f"/api/rooms/{b}/changes?before={sa1}&after={sa2}").status_code == 400
    assert client.get(f"/api/changes?before={sa1}&after={sa2}").status_code == 200  # same room: fine
    assert api.what_changed(client.svc, sa1, sb, room=a)["status"] == "wrong_room"
    draft = add_scan(wm, a, status="draft", ts=20)
    assert client.get(f"/api/rooms/{a}/changes?before={sa1}&after={draft}").status_code == 400  # an unsaved scan is not compared


# ------------------------------------------------------------------------------------------------ guidance

def test_cross_room_guide_is_rejected(client):
    wm = client.svc.wm
    a, b = wm.create_room("A", active=True)["id"], wm.create_room("B")["id"]
    add_scan(wm, a, "mug")
    ob = wm.list_objects(add_scan(wm, b, "laptop"))[0]["id"]
    oa = wm.list_objects(wm.latest_scan_id(a))[0]["id"]
    ok = client.post("/api/guide", json={"object_id": oa})
    assert ok.status_code == 200 and ok.json()["object"]["label"] == "mug"
    wrong_obj = client.post("/api/guide", json={"object_id": ob})  # an object of B while A is active
    assert wrong_obj.status_code == 400 and "another room" in wrong_obj.json()["detail"]
    not_active = client.post("/api/guide", json={"object_id": ob, "room_id": b})  # B's own object, but B is not the active room
    assert not_active.status_code == 400 and "not the active room" in not_active.json()["detail"]
    assert client.post("/api/guide", json={"object_id": 9999}).status_code == 404
    wm.set_active(b)
    assert client.post("/api/guide", json={"object_id": ob}).status_code == 200  # after switching, B's object is fine


def test_a_stale_object_from_an_older_scan_is_not_a_guide_target(client):
    wm = client.svc.wm
    a = wm.create_room("A", active=True)["id"]
    old = wm.list_objects(add_scan(wm, a, ts=1))[0]["id"]
    add_scan(wm, a, ts=10)
    assert client.post("/api/guide", json={"object_id": old}).status_code == 400


# ------------------------------------------------------------------------------------------------ cross-room find

def test_find_in_all_rooms_is_read_only_and_grouped_per_room(client):
    wm = client.svc.wm
    a, b, c = (wm.create_room(n)["id"] for n in ("A", "B", "C"))
    add_scan(wm, a, "mug")
    add_scan(wm, b, "laptop")
    wm.set_active(c)
    subs = client.svc.bus.subscribe("t", types={"guide_request"})
    r = run(client.svc.find("mug", all_rooms=True))
    assert r["all_rooms"] is True and r["found"] is True
    by = {g["room_name"]: g for g in r["groups"]}
    assert by["A"]["found"] and by["A"]["object"]["label"] == "mug" and by["A"]["replay"]["frame_id"]
    assert not by["B"]["found"] and by["B"]["checked"]["scans"] == 1 and not by["C"]["found"] and by["C"]["checked"]["scans"] == 0
    assert r["groups"][0]["room_name"] == "A"  # rooms with the object come first
    assert wm.active_room()["id"] == c and subs.queue.empty()  # nothing was switched, nothing was sent to the phone
    assert all(k not in by["A"] for k in ("path",))


def test_find_in_all_rooms_over_the_socket_and_search(client):
    wm = client.svc.wm
    a, b = wm.create_room("A")["id"], wm.create_room("B")["id"]
    add_scan(wm, a, "mug")
    add_scan(wm, b, "mug", yaw=90)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "find", "query": "mug", "all_rooms": True}))
        r = ws.receive_json()
        assert r["all_rooms"] and sorted(g["room_name"] for g in r["groups"] if g["found"]) == ["A", "B"]
    s = client.get("/api/search?q=mug").json()
    assert [g["room"]["name"] for g in s["groups"]] == ["A", "B"] or [g["room"]["name"] for g in s["groups"]] == ["B", "A"]
    assert [g["room"]["name"] for g in client.get(f"/api/search?q=mug&room_id={b}").json()["groups"]] == ["B"]
    assert client.get("/api/search?q=laptop").json()["groups"] == []
    assert len(client.get("/api/rooms").json()["recent"]) == 2  # recent objects, each tagged with its room


# ------------------------------------------------------------------------------------------------ room cards and details

def test_room_cards_and_details(client):
    wm = client.svc.wm
    a = wm.create_room("Living Room", active=True)["id"]
    empty = wm.create_room("Wardrobe")["id"]
    add_scan(wm, a, "mug", ts=100, frames=3)
    cards = {r["name"]: r for r in client.get("/api/rooms").json()["rooms"]}
    lr = cards["Living Room"]
    assert lr["active"] and lr["n_objects"] == 1 and lr["n_scans"] == 1 and lr["last_scanned"] == 105 and lr["coverage_pct"] > 0
    assert cards["Wardrobe"]["n_objects"] == 0 and cards["Wardrobe"]["last_scanned"] is None and cards["Wardrobe"]["n_scans"] == 0
    d = client.get(f"/api/rooms/{a}").json()
    assert d["room"]["name"] == "Living Room" and d["recent_objects"][0]["label"] == "mug" and d["labels"] == {"mug": 1}
    assert d["recent_objects"][0]["replay"]["image_url"].startswith("/world/frame/")
    assert client.get(f"/api/rooms/{empty}").json()["recent_objects"] == []


def test_status_names_the_active_room(client):
    wm = client.svc.wm
    add_scan(wm, wm.active_room()["id"])
    s = client.get("/api/status").json()
    assert s["active_room"]["name"] == "Test" and s["rooms"] == 1 and s["last_scan"]["room_name"] == "Test"


# ------------------------------------------------------------------------------------------------ MCP

def mcp(svc, name, args=None):
    from mcp import Client

    async def go():
        async with Client(build_server(lambda: svc)) as c:
            return (await c.call_tool(name, args or {})).structured_content

    return run(go())


def test_mcp_defaults_to_the_active_room_and_can_name_another(client):
    svc, wm = client.svc, client.svc.wm
    a, b = wm.create_room("Kitchen", active=True)["id"], wm.create_room("Desk")["id"]
    add_scan(wm, a, "mug")
    add_scan(wm, b, "laptop")
    assert mcp(svc, "list_objects")["room"]["name"] == "Kitchen"
    assert mcp(svc, "list_objects", {"room": "Desk"})["objects"][0]["label"] == "laptop"  # by name
    assert mcp(svc, "list_objects", {"room": str(b)})["room"]["name"] == "Desk"  # by id
    assert mcp(svc, "scan_summary", {"room": "desk"})["counts"] == {"laptop": 1}
    assert mcp(svc, "where_is", {"query": "mug", "room": "Desk"})["status"] == "not_seen"
    assert mcp(svc, "where_is", {"query": "mug"})["status"] == "found"
    rooms = mcp(svc, "list_rooms")
    assert rooms["active_room_id"] == a and {r["name"] for r in rooms["rooms"]} == {"Kitchen", "Desk", "Test"}


def test_mcp_with_no_active_room_returns_the_room_list(client):
    svc, wm = client.svc, client.svc.wm
    add_scan(wm, wm.active_room()["id"])
    wm.set_active(None)
    for tool, args in (("where_is", {"query": "mug"}), ("list_objects", {}), ("scan_summary", {}), ("what_changed", {}), ("guide_user_to", {"query": "mug"})):
        r = mcp(svc, tool, args)
        assert r["status"] == "no_active_room" and r["rooms"] and "room" in r["message"], tool
    assert mcp(svc, "list_objects", {"room": "Nowhere"})["status"] == "no_such_room"


def test_mcp_cross_room_what_changed_and_guide_are_refused(client):
    svc, wm = client.svc, client.svc.wm
    a, b = wm.create_room("A", active=True)["id"], wm.create_room("B")["id"]
    s1, sb = add_scan(wm, a, ts=1), add_scan(wm, b, ts=2)
    assert mcp(svc, "what_changed", {"scan_before": s1, "scan_after": sb})["status"] == "wrong_room"
    assert mcp(svc, "list_objects", {"scan_id": sb})["status"] == "wrong_room"
    r = mcp(svc, "guide_user_to", {"query": "laptop", "room": "B"})
    assert r["status"] == "wrong_room" and "switch" in r["message"]
    assert mcp(svc, "guide_user_to", {"query": "mug"})["status"] == "no_phone_connected"  # the active room is fine
