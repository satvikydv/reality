import random

import pytest

from app import anchoring as A
from app import memory, simworld
from app.world_model import WorldModel

W, H = simworld.W, simworld.H


@pytest.fixture
def wm():
    m = WorldModel()
    yield m
    m.close()


def run(frames, order=None, cfg=None, hfov=None):
    m = WorldModel(memory_cfg=cfg)
    s = m.start_scan()
    simworld.ingest(m, s, frames, order, hfov=hfov)
    return m, s


# ---------------------------------------------------------------- (1) order independence

def test_final_objects_do_not_depend_on_frame_arrival_order():
    truths = simworld.make_scene(seed=3, n=16)
    frames = simworld.sweep(truths, seed=3)
    n = len(frames)
    base_wm, base = run(frames)
    want = simworld.fingerprint(base_wm, base)
    assert len(want) >= 12
    orders = [list(reversed(range(n))), [i for k in range(2) for i in range(k, n, 2)]]
    rng = random.Random(7)
    for _ in range(6):
        o = list(range(n))
        rng.shuffle(o)
        orders.append(o)
    for o in orders:
        m, s = run(frames, o)
        assert simworld.fingerprint(m, s) == want, f"arrival order {o} changed the objects"


def test_order_independence_also_holds_with_clipped_fragments_and_a_wrong_fov():
    truths = simworld.make_scene(seed=11, n=14)
    frames = simworld.sweep(truths, seed=11, true_hfov=66.0, miss_p=0.15, min_visible=0.1)  # the memory assumes 60
    want = simworld.fingerprint(*run(frames))
    for seed in (1, 2, 3):
        o = list(range(len(frames)))
        random.Random(seed).shuffle(o)
        assert simworld.fingerprint(*run(frames, o)) == want


def test_arrival_ordering_is_what_made_the_result_depend_on_order():
    """The check above is only meaningful if delivery order *can* change the result: with arrival ordering it does."""
    truths = simworld.make_scene(seed=3, n=16)
    frames = simworld.sweep(truths, seed=3, true_hfov=66.0, miss_p=0.15, min_visible=0.1)
    legacy = memory.Config(order="arrival")
    base = simworld.fingerprint(*run(frames, cfg=legacy))
    changed = 0
    for k in range(8):
        o = list(range(len(frames)))
        random.Random(k).shuffle(o)
        changed += simworld.fingerprint(*run(frames, o, cfg=legacy)) != base
    assert changed >= 1


def test_object_ids_survive_later_frames(wm):
    truths = simworld.make_scene(seed=5, n=10)
    frames = simworld.sweep(truths, seed=5)
    s = wm.start_scan()
    seen = {}
    for f in frames:
        fid = wm.add_frame(s, f["ts"], f["yaw"], f["pitch"], W, H, roll=0.0, screen_angle=0.0)
        for t, e in zip(f["truth"], wm.observe(fid, [dict(d) for d in f["dets"]])):
            if e["object_id"] is not None:
                seen.setdefault(t, set()).add(e["object_id"])
    # an object may be merged into an older one, but an id is never swapped between two things
    owner = {}
    for t, ids in seen.items():
        for i in ids:
            owner.setdefault(i, set()).add(t)
    assert sum(1 for ts in owner.values() if len(ts) > 1) <= 1


# ---------------------------------------------------------------- (2) appearance

def box(cx=270, cy=480, w=60, h=60):
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


RED = [0.0] * 27
RED[0] = 1.0
BLUE = [0.0] * 27
BLUE[10] = 1.0


def two_views(wm, h1, h2):
    s = wm.start_scan()
    f1 = wm.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0)
    f2 = wm.add_frame(s, 2.0, 104.0, 0.0, W, H, roll=0.0, screen_angle=0.0)
    wm.observe(f1, [{"label": "cup", "box": box(), "hist": h1}])
    wm.observe(f2, [{"label": "cup", "box": box(), "hist": h2}])
    return s


def test_same_place_same_colours_is_one_object(wm):
    s = two_views(wm, RED, RED)
    assert len(wm.list_objects(s)) == 1


def test_same_place_clearly_different_colours_is_two_objects_and_the_veto_is_logged(wm):
    s = two_views(wm, RED, BLUE)
    assert len(wm.list_objects(s)) == 2
    vetoes = [d for d in wm.decisions(s) if d["action"] == "veto"]
    assert len(vetoes) == 1 and vetoes[0]["similarity"] == 0.0 and vetoes[0]["label"] == "cup"


def test_missing_histograms_do_not_block_a_geometric_match(wm):
    s = two_views(wm, RED, None)
    assert len(wm.list_objects(s)) == 1


def test_every_decision_is_logged_once(wm, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="memory"):
        s = two_views(wm, RED, RED)
        wm.rebuild(s)  # a rebuild repeats the same decisions: they must not be logged again
    actions = [d["action"] for d in wm.decisions(s)]
    assert actions.count("new") == 1 and actions.count("merge") == 1
    assert sum("action=merge" in r.message for r in caplog.records) == 1


def test_histogram_similarity_and_masks():
    import numpy as np
    assert memory.hist_similarity(RED, RED) == pytest.approx(1.0) and memory.hist_similarity(RED, BLUE) == 0.0
    assert memory.hist_similarity(None, RED) is None
    img = np.zeros((100, 100, 3), np.uint8)
    img[:, :50] = (0, 0, 255)   # red in BGR
    img[:, 50:] = (255, 0, 0)   # blue
    red = memory.mask_histogram(img, [[5, 5], [45, 5], [45, 95], [5, 95]], None)
    blue = memory.mask_histogram(img, None, [55, 5, 95, 95])
    again = memory.mask_histogram(img, [[10, 10], [40, 10], [40, 90], [10, 90]], None)
    assert len(red) == 27 and sum(red) == pytest.approx(1.0, abs=1e-3)
    assert memory.hist_similarity(red, again) > 0.95 and memory.hist_similarity(red, blue) < 0.1


# ---------------------------------------------------------------- (3) edge-clipped detections

def test_a_clipped_detection_seen_in_two_frames_becomes_an_object(wm):
    s = wm.start_scan()
    clip = [0, 300, 20, 340]  # small and touching the border: a fragment
    f1 = wm.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0)
    f2 = wm.add_frame(s, 2.0, 100.5, 0.0, W, H, roll=0.0, screen_angle=0.0)
    first = wm.observe(f1, [{"label": "phone", "box": clip}])[0]
    assert first["object_id"] is None and first["skipped"] == "fragment" and wm.list_objects(s) == []
    second = wm.observe(f2, [{"label": "phone", "box": clip}])[0]
    assert second["object_id"] is not None and len(wm.list_objects(s)) == 1
    assert wm.get_object(second["object_id"])["n_obs"] == 2
    assert any(d["action"] == "promote" for d in wm.decisions(s))


def test_two_fragments_of_different_things_or_in_one_frame_do_not_promote(wm):
    s = wm.start_scan()
    f = wm.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0)
    wm.observe(f, [{"label": "phone", "box": [0, 300, 20, 340]}, {"label": "phone", "box": [0, 305, 20, 345]}])
    assert wm.list_objects(s) == []  # two sightings, but in one frame
    g = wm.add_frame(s, 2.0, 180.0, 0.0, W, H, roll=0.0, screen_angle=0.0)
    wm.observe(g, [{"label": "phone", "box": [0, 300, 20, 340]}])
    assert wm.list_objects(s) == []  # a different direction entirely


def test_a_fragment_joins_a_matching_object_at_once(wm):
    s = wm.start_scan()
    whole = wm.observe(wm.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0), [{"label": "laptop", "box": [40, 260, 440, 380]}])[0]
    cut = wm.observe(wm.add_frame(s, 2.0, 60.0, 0.0, W, H, roll=0.0, screen_angle=0.0), [{"label": "laptop", "box": [460, 280, 540, 380]}])[0]
    assert cut["object_id"] == whole["object_id"]


def test_promotion_can_be_switched_off():
    m = WorldModel(memory_cfg=memory.Config(promote_fragments=False))
    s = m.start_scan()
    for ts in (1.0, 2.0, 3.0):
        m.observe(m.add_frame(s, ts, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0), [{"label": "phone", "box": [0, 300, 20, 340]}])
    assert m.list_objects(s) == []


# ---------------------------------------------------------------- (4) what is stored per object

def test_objects_carry_confidence_last_seen_frames_and_uncertainty(wm):
    s = wm.start_scan()
    near = wm.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0)
    later = wm.add_frame(s, 9.0, 104.0, 0.0, W, H, roll=0.0, screen_angle=0.0)
    wm.observe(near, [{"label": "cup", "box": box(), "conf": 0.7}])
    wm.observe(later, [{"label": "cup", "box": box(285), "conf": 0.9}])
    o = wm.list_objects(s)[0]
    assert o["n_obs"] == 2 and o["conf_best"] == 0.9 and o["last_seen"] == 9.0 and o["first_seen"] == 1.0
    assert o["fov_err_deg"] > 0 and o["uncertainty_deg"] == pytest.approx((o["spread_deg"] ** 2 + o["fov_err_deg"] ** 2) ** 0.5)


def test_fov_error_grows_away_from_the_image_centre_and_with_the_fov_uncertainty():
    def err(cx, rel):
        m = WorldModel(fov_rel_error=rel)
        s = m.start_scan()
        m.observe(m.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0), [{"label": "cup", "box": box(cx)}])
        return m.list_objects(s)[0]["fov_err_deg"]
    assert err(270, 0.1) == pytest.approx(0.0, abs=0.01)
    assert 0.5 < err(60, 0.1) and err(60, 0.1) == pytest.approx(5 * err(60, 0.02), rel=0.02)
    assert memory.uncertainty_deg(3.0, 4.0) == pytest.approx(5.0)


def test_uncertainty_reflects_disagreeing_sightings(wm):
    s = wm.start_scan()
    wm.observe(wm.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0), [{"label": "cup", "box": box()}])
    wm.observe(wm.add_frame(s, 2.0, 106.0, 0.0, W, H, roll=0.0, screen_angle=0.0), [{"label": "cup", "box": box()}])  # same pixel, camera moved 6 deg
    (o,) = wm.list_objects(s)
    assert 2.5 < o["spread_deg"] < 3.5 and o["uncertainty_deg"] >= o["spread_deg"]


def test_a_database_without_detections_can_be_rebuilt(tmp_path):
    import sqlite3
    path = tmp_path / "w.sqlite"
    m = WorldModel(path)
    s = m.start_scan()
    m.observe(m.add_frame(s, 1.0, 100.0, 0.0, W, H, roll=0.0, screen_angle=0.0), [{"label": "cup", "box": box()}])
    m.db.execute("DELETE FROM detections")  # what a scan stored before detections were kept looks like
    m.db.commit()
    m.close()
    old = WorldModel(path)
    assert old.rebuild(s) == 1 and old.list_objects(s)[0]["label"] == "cup"
    old.close()
