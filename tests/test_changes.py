import pytest

from app import anchoring as A
from app.changes import diff_scans, estimate_yaw_offset
from app.world_model import WorldModel

W, H = 480, 640


@pytest.fixture
def wm():
    m = WorldModel(merge_deg=8.0)
    yield m
    m.close()


def build(wm, objs, shift=0.0, frame_yaws=range(0, 360, 40), ts=1.0):
    """One scan: frames every 40 degrees (in this scan's yaw frame = world + shift); every object whose
    direction lands inside a frame is detected there. objs: (label, yaw, pitch) in the world frame."""
    s = wm.start_scan(ts=ts)
    for i, fy in enumerate(frame_yaws):
        cam = (fy + shift) % 360.0
        f = wm.add_frame(s, ts + i * 0.1, cam, 0.0, W, H, roll=0.0, screen_angle=0.0)
        dets = []
        for label, yaw, pitch in objs:
            px = A.direction_to_pixel((yaw + shift) % 360.0, pitch, W, H, cam, 0.0)
            if px and 30 <= px[0] <= W - 30 and 30 <= px[1] <= H - 30:
                dets.append({"label": label, "box": [px[0] - 20, px[1] - 20, px[0] + 20, px[1] + 20]})
        wm.observe(f, dets)
    return s


ROOM = [("mug", 20, 0), ("keys", 80, 0), ("laptop", 140, 0), ("charger", 200, 0), ("water bottle", 260, 0), ("scissors", 320, 0)]


def labels(items, key="label"):
    return sorted(i[key] for i in items)


def test_nothing_changed(wm):
    a, b = build(wm, ROOM), build(wm, ROOM, ts=100.0)
    r = diff_scans(wm, a, b)
    assert r["added"] == r["removed"] == r["moved"] == r["unverified"] == [] and r["unchanged"] == 6
    assert r["speech"] == "Nothing changed." and r["alignment"]["method"] == "landmarks" and abs(r["alignment"]["offset_deg"]) < 1.0


def test_added_removed_moved_with_rotated_yaw_frame(wm):
    a = build(wm, ROOM)
    after = [o for o in ROOM if o[0] != "scissors"]  # scissors removed
    after = [("keys", 170, 10) if o[0] == "keys" else o for o in after]  # keys moved 90 degrees and up
    after.append(("notebook", 110, 0))  # notebook added
    b = build(wm, after, shift=100.0, ts=100.0)  # second session: sensor yaw zero moved by 100 degrees
    r = diff_scans(wm, a, b)
    assert r["alignment"]["method"] == "landmarks" and r["alignment"]["offset_deg"] == pytest.approx(100.0, abs=1.0)
    assert labels(r["removed"]) == ["scissors"] and labels(r["added"]) == ["notebook"] and labels(r["moved"]) == ["keys"]
    assert r["unchanged"] == 4 and r["unverified"] == []
    m = r["moved"][0]
    assert m["sep_deg"] > 60 and m["from"]["replay"]["frame_id"] and m["to"]["replay"]["frame_id"]
    assert "The keys moved" in r["speech"] and "The scissors is gone" in r["speech"] and "A notebook is new" in r["speech"]


def test_offset_wraps_to_negative(wm):
    a, b = build(wm, ROOM), build(wm, ROOM, shift=350.0, ts=100.0)
    assert diff_scans(wm, a, b)["alignment"]["offset_deg"] == pytest.approx(-10.0, abs=1.0)


def test_unscanned_area_is_unverified_not_removed(wm):
    a = build(wm, ROOM)
    b = build(wm, ROOM[:3], frame_yaws=range(0, 200, 40), ts=100.0)  # second scan only looked at yaw 0..160
    r = diff_scans(wm, a, b)
    assert r["removed"] == [] and labels(r["unverified"]) == ["charger", "scissors", "water bottle"]
    assert "3 objects could not be checked" in r["speech"]


def test_added_in_area_first_scan_never_saw_is_unverified(wm):
    a = build(wm, ROOM[:3], frame_yaws=range(0, 200, 40))
    b = build(wm, [*ROOM[:3], ("notebook", 230, 0)], ts=100.0)
    r = diff_scans(wm, a, b)
    assert r["added"] == [] and labels(r["unverified"]) == ["notebook"] and r["unchanged"] == 3


def test_two_identical_objects_pair_up_nearest(wm):
    a = build(wm, [("mug", 20, 0), ("mug", 200, 0)])
    b = build(wm, [("mug", 24, 0), ("mug", 200, 0)], ts=100.0)
    r = diff_scans(wm, a, b, yaw_offset=0.0)
    assert r["unchanged"] == 2 and r["moved"] == r["added"] == r["removed"] == []


def test_explicit_offset_is_used(wm):
    a, b = build(wm, ROOM), build(wm, ROOM, shift=50.0, ts=100.0)
    assert diff_scans(wm, a, b, yaw_offset=50.0)["alignment"]["method"] == "given"
    wrong = diff_scans(wm, a, b, yaw_offset=0.0)  # ignoring the rotation makes everything look moved
    assert len(wrong["moved"]) == 6


def test_estimate_robust_to_moved_minority_and_flags_weak_evidence(wm):
    a = build(wm, ROOM)
    moved = [("keys", 170, 0), ("mug", 90, 0)] + ROOM[2:]
    b = build(wm, moved, shift=30.0, ts=100.0)
    est = estimate_yaw_offset(wm.list_objects(a), wm.list_objects(b))
    assert est["confident"] and est["offset_deg"] == pytest.approx(30.0, abs=1.0) and est["inliers"] == 4
    one = build(wm, [("mug", 20, 0)], shift=77.0, ts=200.0)
    weak = estimate_yaw_offset(wm.list_objects(a), wm.list_objects(one))
    assert not weak["confident"] and weak["inliers"] == 1
    assert estimate_yaw_offset([], wm.list_objects(a))["candidates"] == 0
