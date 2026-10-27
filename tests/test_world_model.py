import pytest

from app import anchoring as A
from app.world_model import WorldModel, describe_offset, label_match_score, normalize_label

W, H = 480, 640


@pytest.fixture
def wm(tmp_path):
    m = WorldModel(frames_dir=tmp_path / "frames", merge_deg=8.0)
    yield m
    m.close()


def centre_box(cx=240, cy=320, w=40, h=40):
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def frame(wm, scan, ts, yaw, pitch=0.0, jpeg=b"jpg"):
    return wm.add_frame(scan, ts, yaw, pitch, W, H, roll=0.0, screen_angle=0.0, source="relative", jpeg=jpeg)


def test_normalize_and_match():
    assert normalize_label("  The Water  Bottle ") == "water bottle"
    assert label_match_score("keys", "key") == 1.0  # plural folded
    assert label_match_score("remote", "remote control") == 0.7
    assert label_match_score("key", "keyboard") == 0.0
    assert label_match_score("mug", "laptop") == 0.0


def test_anchor_matches_camera_direction_for_centre_box(wm):
    s = wm.start_scan()
    f = frame(wm, s, 1.0, 100.0, 10.0)
    r = wm.observe(f, [{"label": "mug", "box": centre_box()}])[0]
    assert r["new"] and abs(A.wrap180(r["yaw"] - 100)) < 1e-6 and abs(r["pitch"] - 10) < 1e-6


def test_same_object_in_overlapping_frames_merges(wm):
    s = wm.start_scan()
    # camera turned 10 deg between frames; the mug sits at the same world direction (~100 + ... ) in both
    yaw, pitch = 108.0, 0.0
    f1 = frame(wm, s, 1.0, 100.0)
    f2 = frame(wm, s, 2.0, 115.0)
    u1, v1 = A.direction_to_pixel(yaw, pitch, W, H, 100.0, 0.0)
    u2, v2 = A.direction_to_pixel(yaw, pitch, W, H, 115.0, 0.0)
    a = wm.observe(f1, [{"label": "mug", "box": centre_box(u1, v1)}])[0]
    b = wm.observe(f2, [{"label": "Mug", "box": centre_box(u2, v2)}])[0]
    assert a["object_id"] == b["object_id"] and not b["new"]
    o = wm.get_object(a["object_id"])
    assert o["n_obs"] == 2 and abs(A.wrap180(o["yaw"] - yaw)) < 0.01 and o["spread_deg"] < 0.01
    assert len(wm.list_objects(s)) == 1


def test_two_same_label_objects_in_one_frame_stay_separate(wm):
    s = wm.start_scan()
    f = frame(wm, s, 1.0, 0.0)
    out = wm.observe(f, [{"label": "mug", "box": centre_box(200, 320)}, {"label": "mug", "box": centre_box(210, 320)}])
    assert out[0]["object_id"] != out[1]["object_id"]  # 10 px apart is within merge_deg, but one frame cannot see one object twice
    assert len(wm.list_objects(s)) == 2


def test_far_apart_same_label_not_merged(wm):
    s = wm.start_scan()
    wm.observe(frame(wm, s, 1.0, 0.0), [{"label": "mug", "box": centre_box()}])
    wm.observe(frame(wm, s, 2.0, 120.0), [{"label": "mug", "box": centre_box()}])
    assert len(wm.list_objects(s)) == 2


def test_merge_across_wrap(wm):
    s = wm.start_scan()
    a = wm.observe(frame(wm, s, 1.0, 358.0), [{"label": "keys", "box": centre_box()}])[0]
    b = wm.observe(frame(wm, s, 2.0, 3.0), [{"label": "keys", "box": centre_box()}])[0]
    assert a["object_id"] == b["object_id"]
    o = wm.get_object(a["object_id"])
    assert A.angular_distance(o["yaw"], o["pitch"], 0.5, 0) < 3.0


def test_find_ranking_and_alternates(wm):
    s = wm.start_scan()
    f = frame(wm, s, 1.0, 0.0)
    wm.observe(f, [{"label": "remote control", "box": centre_box(100, 300)}, {"label": "charger", "box": centre_box(380, 300)}])
    assert wm.find("remote")[0]["label"] == "remote control"
    assert wm.find("laptop") == []
    hits = wm.find("power adapter", alternates=["charger", "cable"])
    assert [h["label"] for h in hits] == ["charger"] and hits[0]["matched_on"] == "charger"


def test_neighbors_relations(wm):
    s = wm.start_scan()
    f = frame(wm, s, 1.0, 0.0)
    wm.observe(f, [{"label": "laptop", "box": centre_box(240, 320)}, {"label": "mug", "box": centre_box(100, 320)},
                   {"label": "lamp", "box": centre_box(240, 120)}, {"label": "keys", "box": centre_box(240, 520)}])
    ids = {o["label"]: o["id"] for o in wm.list_objects(s)}
    rel = {n["label"]: n["relation"] for n in wm.neighbors(ids["laptop"])}
    assert rel == {"mug": "right of", "lamp": "below", "keys": "above"}  # laptop is right of the mug, below the lamp, above the keys


def test_where_is_speech_replay_and_scan_start(wm):
    s = wm.start_scan(source="relative")
    f1 = frame(wm, s, 1.0, 90.0, jpeg=b"first")
    f2 = frame(wm, s, 2.0, 140.0, jpeg=b"second")
    wm.observe(f1, [{"label": "laptop", "box": centre_box(240, 320), "conf": 0.9}])
    wm.observe(f2, [{"label": "charger", "box": centre_box(240, 320), "conf": 0.8}, {"label": "charger", "box": centre_box(10, 10, 10, 10), "conf": 0.1}])
    r = wm.where_is("the thing that charges", alternates=["charger"])
    assert r["object"]["label"] == "charger" and r["is_latest_scan"]
    assert abs(r["from_scan_start"]["dyaw"] - 50) < 1.0
    assert "to the right of where the scan started" in r["speech"]
    assert r["replay"]["frame_id"] == f2 and wm.frame_jpeg(r["replay"]["frame_id"]) == b"second"
    assert wm.where_is("scissors") is None


def test_where_is_falls_back_to_earlier_scan(wm):
    s1 = wm.start_scan(ts=1.0)
    wm.observe(frame(wm, s1, 1.0, 0.0), [{"label": "scissors", "box": centre_box()}])
    s2 = wm.start_scan(ts=2.0)
    wm.observe(frame(wm, s2, 2.0, 0.0), [{"label": "mug", "box": centre_box()}])
    r = wm.where_is("scissors")
    assert r["scan_id"] == s1 and not r["is_latest_scan"] and "earlier scan" in r["speech"]


def test_list_objects_since_and_latest_default(wm):
    s = wm.start_scan()
    wm.observe(frame(wm, s, 1.0, 0.0), [{"label": "mug", "box": centre_box()}])
    wm.observe(frame(wm, s, 9.0, 120.0), [{"label": "keys", "box": centre_box()}])
    assert [o["label"] for o in wm.list_objects()] == ["mug", "keys"]
    assert [o["label"] for o in wm.list_objects(since=5.0)] == ["keys"]


def test_persists_to_disk(tmp_path):
    db = tmp_path / "wm.sqlite"
    a = WorldModel(db)
    s = a.start_scan()
    a.observe(a.add_frame(s, 1.0, 10.0, 0.0, W, H), [{"label": "mug", "box": centre_box()}])
    a.close()
    b = WorldModel(db)
    assert b.find("mug")[0]["label"] == "mug"
    b.close()


def test_unknown_frame_raises(wm):
    with pytest.raises(KeyError):
        wm.observe(999, [])


def test_describe_offset():
    assert describe_offset(2, 0) == "straight ahead of where the scan started, roughly level"
    assert "left" in describe_offset(-40, 0) and "above level" in describe_offset(0, 25)
    assert describe_offset(170, 0).startswith("behind")
