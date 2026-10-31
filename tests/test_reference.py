"""Lining up a rescan: the room's reference keyframe as an outline, and live guidance from camera frames sent over the socket."""
import numpy as np
import cv2

from app import scale
from app.scan import Listing, ScanPipeline
from synthetic_world import add_scan, make_panorama, render_view
from test_scan_service import FakeReasoner, client, frame_msg, make_service, meta  # noqa: F401  (client is a fixture)

PANO = make_panorama()
YAWS = list(range(0, 360, 40))


def room_with_scan(wm, name="Study"):
    rid = wm.create_room(name, active=True)["id"]
    sid = add_scan(wm, rid, PANO, YAWS, ts=1.0)
    return rid, sid


def test_a_room_with_a_saved_scan_offers_its_first_keyframe_as_an_outline(client):
    wm = client.svc.wm
    rid, sid = room_with_scan(wm)
    d = client.get(f"/api/rooms/{rid}/reference").json()
    first = wm.list_frames(sid)[0]
    assert d["status"] == "ok" and d["scan_id"] == sid and d["frame_id"] == first["id"] and d["outline_url"].endswith(f"?frame={first['id']}")
    r = client.get(d["outline_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_UNCHANGED)
    assert img.shape[2] == 4 and img[..., 3].max() == 255
    assert client.get(f"/api/rooms/{rid}/reference.png?frame=999999").status_code == 404


def test_a_room_without_a_scan_has_no_reference_and_unknown_rooms_are_404(client):
    rid = client.svc.wm.create_room("Empty")["id"]
    assert client.get(f"/api/rooms/{rid}/reference").json()["status"] == "no_reference"
    assert client.get(f"/api/rooms/{rid}/reference.png").status_code == 404
    assert client.get("/api/rooms/999/reference").status_code == 404


def test_a_draft_scan_is_not_a_reference(client):
    wm = client.svc.wm
    rid = wm.create_room("Draft")["id"]
    add_scan(wm, rid, PANO, YAWS, ts=1.0, status="draft")
    assert client.get(f"/api/rooms/{rid}/reference").json()["status"] == "no_reference"


def probe(client, ws, rid, yaw, seq=1, pano=PANO):
    ws.send_bytes(frame_msg(seq, 4, {"room_id": rid}, render_view(pano, yaw, 0.0, 0.0, 480, 640, 60.0)))
    return ws.receive_json()


def test_live_frames_are_answered_with_words_for_turning_and_within_when_lined_up(client):
    rid, _ = room_with_scan(client.svc.wm)
    with client.websocket_connect("/ws") as ws:
        same = probe(client, ws, rid, 0.0)
        assert same["type"] == "align_hint" and same["room_id"] == rid and same["ok"] and same["within"] and same["words"].startswith("Lined up")
        off = probe(client, ws, rid, 9.0, seq=2)
        assert off["ok"] and not off["within"] and off["turn_deg"] < -5 and "turn left" in off["words"].lower()
        away = probe(client, ws, rid, 180.0, seq=3)                      # looking at something else: no overlap with the reference
        assert away["ok"] is False and away["within"] is False and "faded outline" in away["words"]


def test_a_probe_for_a_room_without_a_reference_says_so_and_never_breaks_the_socket(client):
    rid = client.svc.wm.create_room("Empty")["id"]
    with client.websocket_connect("/ws") as ws:
        none = probe(client, ws, rid, 0.0)
        assert none["no_reference"] is True and none["within"] is False
        ws.send_bytes(frame_msg(2, 4, {"room_id": rid}, b"not a jpeg"))
        assert ws.receive_json()["type"] == "align_hint"
        ws.send_bytes(frame_msg(3, 4, {"room_id": 12345}, render_view(PANO, 0.0)))
        assert ws.receive_json()["no_reference"] is True
        ws.send_text('{"type": "find", "query": "x"}')                    # the socket still works afterwards
        assert ws.receive_json()["type"] in ("find_result", "error")


def test_the_name_pass_stores_the_sizes_spark_gives_and_still_accepts_plain_names(tmp_path):
    import asyncio
    from app.reasoner.base import Result

    class Sizes(FakeReasoner):
        async def respond(self, messages, *, schema=None, **kw):
            if "objects" in schema["properties"]:
                return Result(text="", parsed={"objects": [{"name": "Mug", "size_cm": 11.0}, {"name": "phone charger", "size_cm": None}, "keys"]})
            return await super().respond(messages, schema=schema, **kw)

    svc = make_service(tmp_path)
    pipe = ScanPipeline(svc.wm, Sizes(), None)
    names, usage = asyncio.run(pipe.list_names("data:image/jpeg;base64,AA==", []))
    assert names == ["mug", "phone charger", "keys"] and usage["sizes"] == {"mug": 11.0}
    svc.wm.record_label_size("mug", usage["sizes"]["mug"])
    assert scale.size_for(svc.wm, "mug") == (11.0, "spark")
    assert Listing.model_validate({"objects": ["a", "b"]}).objects[0].size_cm is None
