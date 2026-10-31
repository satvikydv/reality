"""Scanning a desk twice from 40 to 60 cm: the depth-rendered world reproduces the real near-field failure, and the fixes are checked on it.

The world (tests/synthetic_world.DeskScene) renders real parallax, so a few centimetres of hand drift shift near objects by several degrees
while the wall behind barely moves, as on the phone. Everything here is synthetic: it checks the code, not a camera.
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

from app import align, changes
from synthetic_world import DeskScene

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_alignment  # noqa: E402

warnings.filterwarnings("ignore", category=RuntimeWarning)

NAMES = eval_alignment.NEAR_NAMES


@pytest.fixture(scope="module")
def desk():
    """The near-field world of scripts/eval_alignment.py, seed 1: a reference scan and the five rescans, aligned once."""
    wm, ref, scenarios, truth = eval_alignment.near_field_world(1)
    by = {s["name"]: s for s in scenarios}
    aligned = {n: align.align_scans(wm, ref, s["scan"]) for n, s in by.items()}
    return wm, ref, by, truth, aligned


def compare(desk, name, **kw):
    wm, ref, by, _, aligned = desk
    return changes.compare_scans(wm, ref, by[name]["scan"], align=kw.pop("align", aligned[name]), **kw)


def labels(d, kind):
    return sorted(e["label"] for e in d[kind])


# ------------------------------------------------------------------------------------------------ the renderer

def test_the_desk_renderer_has_real_parallax_near_objects_shift_more_than_the_far_wall():
    scene = DeskScene(seed=5)
    _, at = scene.render((0.0, 0.0, 0.0), 0.0, -40.0, with_owner=True)
    _, moved = scene.render((10.0, 0.0, 0.0), 0.0, -40.0, with_owner=True)     # the phone 10 cm to the right, pointing the same way
    cx = lambda m: float(np.nonzero(m)[1].mean())
    shift_near = cx(at["notebook"]) - cx(moved["notebook"])                    # about 30 cm away
    f = 270.0 / np.tan(np.radians(30.0))
    assert np.degrees(np.arctan(shift_near / f)) > 8.0                         # several degrees for a 10 cm step, as on the real desk
    y, p = scene.direction("mouse"), scene.direction("mouse", (10.0, 0.0, 0.0))
    assert y[0] - p[0] > 5.0                                                   # the same object seen from 10 cm aside is in another direction


# ------------------------------------------------------------------------------------------------ reproduction, then the fix

def test_masking_the_objects_reproduces_the_weak_near_field_alignment_and_keeping_them_fixes_it(desk):
    wm, ref, by, _, aligned = desk
    old = align.align_scans(wm, ref, by[NAMES[0]]["scan"], mask_objects=True)      # what the code did before: no features on detected objects
    assert old["quality"] != "good" and old["pairs"] <= 2, old                       # the desk is mostly objects: little left to match
    new = aligned[NAMES[0]]
    assert new["pairs"] >= 3 and new["inliers"] >= 3 * max(1, old["inliers"]) and abs(new["offset_deg"]) <= 3.0, new
    assert aligned[NAMES[1]]["quality"] == "good" and aligned[NAMES[3]]["quality"] == "good"


def test_the_images_recover_each_frames_pose_and_the_scans_tilt_bias(desk):
    wm, _, by, truth, aligned = desk
    al = aligned[NAMES[0]]                                                          # stored pitch reads 4 degrees high in this rescan
    assert 2.0 <= al["pitch_bias_deg"] <= 6.0, al["pitch_bias_deg"]
    errs_stored, errs_images = [], []
    for f in wm.list_frames(by[NAMES[0]]["scan"]):
        pose = al["frame_poses"].get(str(f["id"]))
        if pose:
            errs_stored.append(abs(f["pitch"] - truth[f["id"]]["pitch"]))
            errs_images.append(abs(pose["pitch"] - truth[f["id"]]["pitch"]))
    assert errs_images and np.mean(errs_images) < np.mean(errs_stored) - 1.5          # the images undo the bias (what is left: the reference's own noise)


def test_the_moved_mouse_is_found_by_the_texture_around_it_and_nothing_else_is_claimed(desk):
    d = compare(desk, NAMES[1])
    assert labels(d, "moved") == ["mouse"] and d["removed"] == [] and d["added"] == []
    lev = d["moved"][0]["local"]
    assert lev and lev["residual_deg"] > changes.LOCAL_MOVED_K * lev["tolerance_deg"]


def test_two_moved_objects_are_never_called_unchanged_or_gone_and_nothing_else_is_claimed(desk):
    d = compare(desk, NAMES[4])                    # this rescan lines up only weakly (four frames, the standard error is just over the limit)
    reported = labels(d, "moved") + labels(d, "moved_low_confidence") + labels(d, "possibly_moved") + [e["label"] for e in d["cant_compare"] if e["was"] == "moved?"]
    assert {"mouse", "phone"} <= set(reported) and set(labels(d, "moved") + labels(d, "moved_low_confidence")) <= {"mouse", "phone"}
    assert d["removed"] == [] and d["added"] == []
    if d["alignment"]["quality"] == "weak":
        assert d["moved"] == [] and "phone" in labels(d, "moved_low_confidence")    # the phone moved far beyond anything the viewpoint explains


@pytest.mark.parametrize("name", [NAMES[0], NAMES[2], NAMES[3]])
def test_rescans_where_nothing_moved_claim_no_move_no_new_and_nothing_gone(desk, name):
    d = compare(desk, name)
    assert d["moved"] == [] and d["moved_low_confidence"] == [] and d["removed"] == [] and d["added"] == [], (name, d["speech"])


def test_a_20_cm_step_is_measured_from_the_objects_that_did_not_move(desk):
    d = compare(desk, NAMES[2])
    al = d["alignment"]
    assert al["viewpoint_cm_measured"] is not None and 15.0 <= al["viewpoint_cm_used"] <= 35.0, al
    assert al["translation"]["trusted"] and abs(al["translation"]["yaw_deg"] - 90.0) < 20.0          # the step was to the east


def test_without_local_evidence_the_assumed_viewpoint_error_still_holds(desk):
    _, _, _, _, aligned = desk
    al = {**aligned[NAMES[1]], "local": []}                                          # no texture around anything: only the viewpoint model
    d = compare(desk, NAMES[1], align=al)
    assert d["moved"] == [] and "mouse" in labels(d, "possibly_moved")              # 25 to 30 degrees at 45 cm is within twice what 15 cm explains


# ------------------------------------------------------------------------------------------------ the pieces

def test_one_object_cannot_carry_a_pair_when_it_moved():
    rng = np.random.default_rng(3)
    k = align.intrinsics(480, 640, 60.0)

    def project(rays, r):
        p = (k @ (rays @ r.T).T).T
        return (p[:, :2] / p[:, 2:3]).astype(np.float32)

    def rot_y(deg):
        a = np.radians(deg)
        return np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])

    bg = rng.normal(size=(30, 3)) * [0.6, 0.6, 0.1] + [0, 0, 1]
    obj = rng.normal(size=(70, 3)) * [0.05, 0.05, 0.01] + [0.1, 0.1, 1]
    rays = np.vstack([bg, obj])
    rays /= np.linalg.norm(rays, axis=1, keepdims=True)
    pa = project(rays, np.eye(3))
    pb = np.vstack([project(rays[:30], rot_y(5.0)), project(rays[30:], rot_y(12.0))])   # the camera turned 5 degrees; the object also moved
    desc = rng.normal(size=(100, 128)).astype(np.float32)
    box = [float(pa[30:, 0].min() - 2), float(pa[30:, 1].min() - 2), float(pa[30:, 0].max() + 2), float(pa[30:, 1].max() + 2)]
    fa = align.Feat(pa, desc, 480, 640, align.regions(pa, [box]))
    fb = align.Feat(pb, desc, 480, 640)
    est = align.estimate_pair(fb, fa, 60.0, 60.0)
    assert est is not None and est.dropped_region == 0
    assert align.rotation_angle_deg(est.r, rot_y(5.0).T) < 0.5                        # the background's rotation, not the object's
    fa_static = align.Feat(pa, desc, 480, 640, align.regions(pa, [box]))
    fb_static = align.Feat(project(rays, rot_y(5.0)), desc, 480, 640)
    still = align.estimate_pair(fb_static, fa_static, 60.0, 60.0)
    assert still.dropped_region is None and still.inliers == 100                        # an object that agrees with the scene is kept


def test_local_predictions_use_the_ring_around_a_box_not_the_box_and_skip_clipped_boxes():
    rng = np.random.default_rng(1)
    src = rng.uniform(100, 380, size=(200, 2)).astype(np.float32)
    dst = src + np.float32([12.0, -5.0])                                               # the surroundings shifted by (12, -5)
    inside = (src[:, 0] > 200) & (src[:, 0] < 260) & (src[:, 1] > 200) & (src[:, 1] < 260)
    dst[inside] += 80.0                                                                # the object's own features moved a lot more
    out = align.local_predictions(src, dst, [(7, [210.0, 210.0, 250.0, 250.0]), (8, [0.0, 10.0, 40.0, 50.0])], 480, 640)
    assert [p["det"] for p in out] == [7]                                              # the box at the frame edge is skipped
    assert abs(out[0]["px"][0] - 242.0) < 1.0 and abs(out[0]["px"][1] - 225.0) < 1.0 and out[0]["n"] >= align.LOCAL_MIN_POINTS


def test_the_standard_error_counts_frames_not_pairs_and_never_trusts_two_agreeing_pairs_too_much():
    same_b = [{"a": i, "b": 9, "yaw": 1.0 + 0.01 * i, "pitch": 0, "roll": 0, "w": 100} for i in range(6)]   # six pairs, all through one frame of B
    c = align.combine(same_b)
    assert c["n_eff"] == 1 and c["sem"] >= 1.2533 * align.PAIR_NOISE_FLOOR_DEG
    two = align.combine([{"a": 1, "b": 2, "yaw": 3.0, "pitch": 0, "roll": 0, "w": 50}, {"a": 3, "b": 4, "yaw": 3.0, "pitch": 0, "roll": 0, "w": 60}])
    assert two["spread"] == 0.0 and two["sem"] == pytest.approx(1.2533 * align.PAIR_NOISE_FLOOR_DEG / np.sqrt(2))


def test_pose_of_inverts_the_camera_basis():
    for yaw, pitch, roll in ((10.0, -40.0, 3.0), (250.0, 20.0, -7.0), (359.0, -80.0, 0.0)):
        b = align.basis({"yaw": yaw, "pitch": pitch, "roll": roll, "screen_angle": 0.0})
        y, p, r = align.pose_of(b)
        assert abs(align.wrap180(y - yaw)) < 1e-6 and abs(p - pitch) < 1e-6 and abs(r - roll) < 1e-6


def test_objects_split_by_hand_drift_count_once_but_objects_seen_together_count_twice():
    objs = [{"id": 1, "label": "mouse", "yaw": 40.0, "pitch": -40.0}, {"id": 2, "label": "mouse", "yaw": 48.0, "pitch": -36.0},
            {"id": 3, "label": "mouse", "yaw": 200.0, "pitch": -40.0}]
    dets = {1: [{"frame_id": 10}], 2: [{"frame_id": 11}], 3: [{"frame_id": 12}]}
    kinds, groups = changes._kinds(objs, dets, lambda o: 15.0)
    assert kinds["mouse"] == 2 and groups[1] == groups[2] != groups[3]               # 1 and 2 may be one mouse; 3 is far away
    dets[2] = [{"frame_id": 10}]                                                      # seen in the same frame as 1: two mice
    assert changes._kinds(objs, dets, lambda o: 15.0)[0]["mouse"] == 3

