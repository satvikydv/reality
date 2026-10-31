"""Scan alignment from images alone: a known shift of one scan's pose metadata is recovered, textureless frames fail honestly, frame order does not matter."""
import random

import cv2
import numpy as np
import pytest

from app import align
from app.world_model import WorldModel
from synthetic_world import add_scan, make_panorama, render_view

PANO = make_panorama()
YAWS = list(range(0, 360, 40))


@pytest.fixture()
def wm(tmp_path):
    w = WorldModel(frames_dir=tmp_path / "frames")
    w.room = w.create_room("Study", active=True)["id"]
    return w


def two_scans(wm, shift, *, pitch_shift=0.0, jitter=(9.0, 1.0), order=None):
    a = add_scan(wm, wm.room, PANO, YAWS, ts=1.0)
    b = add_scan(wm, wm.room, PANO, YAWS, meta_yaw_shift=shift, meta_pitch_shift=pitch_shift, true_jitter=jitter, ts=500.0, order=order)
    return a, b


@pytest.mark.parametrize("shift", [0.0, 37.0, -25.0])
def test_a_known_yaw_shift_of_scan_b_is_recovered_within_a_degree_from_the_images(wm, shift):
    a, b = two_scans(wm, shift)
    r = align.align_scans(wm, a, b)
    assert r["quality"] == "good", r
    assert abs(align.wrap180(r["offset_deg"] - shift)) <= 1.0, r      # B minus A: B's frames were stored with yaw + shift
    assert r["inliers"] >= 100 and r["pairs"] >= 3 and r["spread_deg"] <= 2.0 and r["method"] == "pose prior"


def test_a_start_heading_rotated_by_ninety_degrees_is_found_by_the_search(wm):
    a, b = two_scans(wm, 110.0)                                   # far outside the pose prior's window: frames facing the same way are 110 degrees apart in metadata
    r = align.align_scans(wm, a, b, window_deg=40.0)
    assert r["quality"] in ("good", "weak") and abs(align.wrap180(r["offset_deg"] - 110.0)) <= 1.5, r
    assert r["method"] in ("search", "pose prior, refined", "pose prior")


def test_a_yaw_only_shift_leaves_the_pitch_and_roll_offsets_near_zero_even_with_a_pitch_error_in_the_metadata(wm):
    a, b = two_scans(wm, 20.0, pitch_shift=2.0)               # every B frame is stored 2 degrees too high: per-frame sensor error, not a different frame
    r = align.align_scans(wm, a, b)
    assert abs(align.wrap180(r["offset_deg"] - 20.0)) <= 1.0 and abs(r["pitch_offset_deg"]) <= 1.0 and abs(r["roll_offset_deg"]) <= 1.0, r


def test_the_order_the_frames_were_stored_in_does_not_change_the_result(wm):
    n = len(YAWS)
    a, b1 = two_scans(wm, 37.0)
    order = list(range(n))
    random.Random(3).shuffle(order)
    b2 = add_scan(wm, wm.room, PANO, YAWS, meta_yaw_shift=37.0, true_jitter=(9.0, 1.0), ts=900.0, order=order)
    r1, r2 = align.align_scans(wm, a, b1), align.align_scans(wm, a, b2)
    assert abs(r1["offset_deg"] - r2["offset_deg"]) <= 0.5 and r2["quality"] == "good", (r1, r2)


def test_textureless_frames_fail_honestly_and_never_invent_an_offset(wm):
    flat = cv2.imencode(".jpg", np.full((640, 480, 3), 128, np.uint8))[1].tobytes()
    ids = []
    for ts in (1.0, 2.0):
        sid = wm.start_scan(ts=ts, room_id=wm.room)
        for i, y in enumerate(YAWS):
            wm.add_frame(sid, ts + i, y, 0.0, 480, 640, roll=0.0, screen_angle=0.0, hfov_deg=60.0, jpeg=flat)
        wm.end_scan(sid, ts + 50)
        ids.append(sid)
    r = align.align_scans(wm, *ids)
    assert r["quality"] == "failed" and r["inliers"] == 0 and r["pairs"] == 0 and r["offset_deg"] == 0.0 and r["reason"]


def test_frames_without_a_usable_picture_fail_too(wm):
    ids = []
    for ts in (1.0, 2.0):
        sid = wm.start_scan(ts=ts, room_id=wm.room)
        wm.add_frame(sid, ts, 0.0, 0.0, 480, 640, roll=0.0, screen_angle=0.0, hfov_deg=60.0, jpeg=b"jpg")        # stored, but not an image
        wm.add_frame(sid, ts + 1, 40.0, 0.0, 480, 640, roll=0.0, screen_angle=0.0, hfov_deg=60.0)                 # no picture at all
        wm.end_scan(sid, ts + 5)
        ids.append(sid)
    assert align.align_scans(wm, *ids)["quality"] == "failed"


def test_two_scans_of_different_places_are_not_called_good(wm):
    other = make_panorama(seed=99)
    a = add_scan(wm, wm.room, PANO, YAWS, ts=1.0)
    b = add_scan(wm, wm.room, other, YAWS, ts=2.0)
    r = align.align_scans(wm, a, b)
    assert r["quality"] != "good", r


def test_detected_objects_are_masked_so_moving_them_does_not_pull_the_estimate(wm):
    a = add_scan(wm, wm.room, PANO, YAWS, ts=1.0, objects=[("mug", 80.0, 0.0)])
    pano2 = PANO.copy()
    cv2.rectangle(pano2, (int(80 / 360 * 2880) - 120, 560), (int(80 / 360 * 2880) + 120, 760), (20, 200, 20), -1)   # something big changed where the mug was
    b = add_scan(wm, wm.room, pano2, YAWS, meta_yaw_shift=15.0, true_jitter=(9.0, 1.0), ts=500.0, objects=[("mug", 80.0, 0.0)])
    r = align.align_scans(wm, a, b)
    assert abs(align.wrap180(r["offset_deg"] - 15.0)) <= 1.0, r


# ------------------------------------------------------------------------------------------------ the pieces

def test_the_rotation_fit_recovers_a_known_rotation_despite_outliers():
    rng = np.random.default_rng(5)
    rb = rng.normal(size=(200, 3))
    rb[:, 2] = np.abs(rb[:, 2]) + 2.0
    rb /= np.linalg.norm(rb, axis=1, keepdims=True)
    ang = np.radians(12.0)
    r_true = np.array([[np.cos(ang), 0, np.sin(ang)], [0, 1, 0], [-np.sin(ang), 0, np.cos(ang)]])
    ra = rb @ r_true.T
    ra[:60] = rng.normal(size=(60, 3))                               # 30% wrong matches
    ra /= np.linalg.norm(ra, axis=1, keepdims=True)
    r, mask = align.rotation_ransac(rb, ra)
    assert np.linalg.norm(r - r_true) < 1e-2 and mask.sum() >= 130


def test_the_weighted_median_ignores_a_heavily_wrong_minority_and_handles_the_wrap():
    ests = [{"yaw": 179.0, "pitch": 0, "roll": 0, "w": 50}, {"yaw": -179.5, "pitch": 0, "roll": 0, "w": 60}, {"yaw": 180.0, "pitch": 0, "roll": 0, "w": 40},
            {"yaw": 90.0, "pitch": 0, "roll": 0, "w": 10}]
    c = align.combine(ests)
    assert abs(align.wrap180(c["yaw"] - 180.0)) < 1.0 and len(c["used"]) == 3


def test_guidance_says_which_way_to_turn_and_is_within_when_lined_up(wm):
    a = add_scan(wm, wm.room, PANO, [0.0], ts=1.0)
    ref = wm.list_frames(a)[0]
    probe = align.ReferenceProbe(wm.frame_jpeg(ref["id"]), 60.0)
    same = probe.probe(render_view(PANO, 0.0, 0.0))
    assert same["ok"] and same["within"] and same["words"].startswith("Lined up")
    right = probe.probe(render_view(PANO, 8.0, 0.0))                  # the camera points 8 degrees right of the reference: turn left
    assert right["ok"] and not right["within"] and 5.0 <= right["turn_deg"] * -1 <= 11.0 and "turn left" in right["words"].lower()
    blank = probe.probe(cv2.imencode(".jpg", np.full((640, 480, 3), 90, np.uint8))[1].tobytes())
    assert blank["ok"] is False and blank["within"] is False


def test_the_reference_outline_is_a_transparent_png_with_edges_only(wm):
    a = add_scan(wm, wm.room, PANO, [0.0], ts=1.0)
    png = align.edges_png(wm.frame_jpeg(wm.list_frames(a)[0]["id"]))
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert img.shape[2] == 4 and img[..., 3].max() == 255 and (img[..., 3] == 0).mean() > 0.4
    assert align.edges_png(b"not an image") is None
