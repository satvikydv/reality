"""Advice on whether a scan will compare well later: each check follows a rule the comparison applies, and none of it blocks saving."""
import warnings

import cv2
import numpy as np
import pytest

from app import scan_quality
from app.world_model import WorldModel
from synthetic_world import DeskScene, add_desk_scan, add_scan, make_panorama

warnings.filterwarnings("ignore", category=RuntimeWarning)
PANO = make_panorama()


@pytest.fixture()
def wm(tmp_path):
    w = WorldModel(frames_dir=tmp_path / "frames")
    w.room = w.create_room("Desk", active=True)["id"]
    return w


def by_id(q):
    return {c["id"]: c for c in q["checks"]}


def test_a_full_textured_scan_with_every_object_seen_twice_needs_no_advice(wm):
    sid = add_scan(wm, wm.room, PANO, list(range(0, 360, 30)), objects=[("mug", 80.0, 0.0), ("book", 200.0, 0.0)])
    q = scan_quality.assess(wm, sid)
    assert q["ok"] and q["tips"] == [] and all(c["ok"] for c in q["checks"]) and "good scan" in q["words"]


def test_a_thin_desk_scan_like_the_real_one_is_told_to_take_more_shots_and_pan_slower(wm):
    sid = add_desk_scan(wm, wm.room, DeskScene(seed=3, detail=0.2), yaws=(-30.0, 0.0, 30.0), seed=1, pitch=-42.0, blur=0.8)
    q = scan_quality.assess(wm, sid)
    c = by_id(q)
    assert not q["ok"] and not c["frames"]["ok"] and c["frames"]["value"] == 3
    assert q["tips"][0] == c["frames"]["tip"]                                     # the most useful advice first


def test_objects_seen_only_once_are_told_to_be_in_two_shots(wm):
    sid = add_scan(wm, wm.room, PANO, list(range(0, 360, 30)), objects=[("mug", 80.0, 0.0), ("book", 200.0, 0.0), ("lamp", 300.0, 0.0)], sightings=1)
    c = by_id(scan_quality.assess(wm, sid))
    assert not c["seen_twice"]["ok"] and c["seen_twice"]["value"] == "0 of 3" and "two shots" in c["seen_twice"]["tip"]


def test_shots_too_far_apart_are_told_to_overlap(wm):
    sid = add_scan(wm, wm.room, PANO, [0, 50, 100, 150, 200, 250])                 # 50 degree steps with a 60 degree view
    c = by_id(scan_quality.assess(wm, sid))
    assert not c["overlap"]["ok"] and c["overlap"]["value"] >= 49 and "Pan slower" in c["overlap"]["tip"]


def test_a_plain_view_looking_down_is_told_to_tilt_up_toward_the_far_wall(wm):
    flat = cv2.imencode(".jpg", np.full((640, 480, 3), 120, np.uint8))[1].tobytes()
    sid = wm.start_scan(ts=1.0, room_id=wm.room)
    for i in range(6):
        wm.add_frame(sid, 1.0 + i, i * 20.0, -45.0, 480, 640, roll=0.0, screen_angle=0.0, hfov_deg=60.0, jpeg=flat)
    wm.end_scan(sid, 10.0)
    c = by_id(scan_quality.assess(wm, sid))
    assert not c["background"]["ok"] and "far wall" in c["background"]["tip"]


def test_blurred_shots_are_counted_against_the_scans_own_sharpest(wm):
    sid = add_scan(wm, wm.room, PANO, list(range(0, 360, 30)))
    frames = wm.list_frames(sid)
    for f in frames[:8]:                                                           # 8 badly blurred shots next to the 12 sharp ones
        img = cv2.imdecode(np.frombuffer(wm.frame_jpeg(f["id"]), np.uint8), cv2.IMREAD_COLOR)
        wm.add_frame(sid, f["ts"] + 0.5, f["yaw"], f["pitch"], 480, 640, roll=0.0, screen_angle=0.0, hfov_deg=60.0,
                     jpeg=cv2.imencode(".jpg", cv2.GaussianBlur(img, (0, 0), 6))[1].tobytes())
    c = by_id(scan_quality.assess(wm, sid))
    assert not c["sharp"]["ok"] and "Hold the phone still" in c["sharp"]["tip"]


def test_a_scan_with_no_frames_or_objects_still_gets_an_answer(wm):
    sid = wm.start_scan(ts=1.0, room_id=wm.room)
    wm.end_scan(sid, 2.0)
    q = scan_quality.assess(wm, sid)
    assert not q["ok"] and by_id(q)["frames"]["value"] == 0 and "seen_twice" not in by_id(q)
