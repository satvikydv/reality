"""Parallax-aware comparison of two scans: the classes, the tolerance that grows for near objects, and no "moved" when the alignment is weak."""
import pytest

from app import changes, scale
from app.world_model import WorldModel
from synthetic_world import GOOD, add_scan, make_panorama

PANO = make_panorama()
YAWS = list(range(0, 360, 40))


@pytest.fixture()
def wm(tmp_path):
    w = WorldModel(frames_dir=tmp_path / "frames")
    w.room = w.create_room("Study", active=True)["id"]
    return w


def pair_shifted(wm, a_objects, b_objects, shift):
    """Scan B is stored in a frame rotated by `shift` degrees: the same physical direction has yaw + shift in B."""
    a = add_scan(wm, wm.room, PANO, YAWS, ts=100.0, objects=a_objects)
    b = add_scan(wm, wm.room, PANO, YAWS, meta_yaw_shift=shift, true_jitter=(9.0, 1.0), ts=900.0, objects=b_objects)
    return a, b


def labels(d, kind):
    return sorted(e["label"] for e in d[kind])


def test_a_scan_taken_with_another_start_heading_is_aligned_by_the_images_so_nothing_is_reported_moved(wm):
    objs = [("mug", 80.0, 0.0), ("laptop", 200.0, 0.0), ("plant", 300.0, 0.0)]
    a, b = pair_shifted(wm, objs, objs, shift=37.0)
    d = changes.compare_scans(wm, a, b)
    assert d["alignment"]["quality"] == "good" and abs(d["alignment"]["offset_used_deg"] - 37.0) <= 1.0 and d["alignment"]["offset_source"] == "images"
    assert d["moved"] == [] and d["possibly_moved"] == [] and d["removed"] == [] and d["added"] == [] and d["unchanged"] == 3
    assert d["speech"] == "Nothing changed."


def test_a_real_move_is_moved_and_the_other_objects_are_not(wm):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0), ("laptop", 200.0, 0.0)], [("mug", 110.0, 0.0), ("laptop", 200.0, 0.0)], shift=0.0)
    d = changes.compare_scans(wm, a, b)
    assert d["alignment"]["quality"] == "good" and labels(d, "moved") == ["mug"] and d["unchanged"] == 1
    assert "The mug moved" in d["speech"] and d["moved"][0]["sep_deg"] == pytest.approx(30, abs=3)


def test_the_tolerance_is_larger_for_near_objects_than_for_far_ones(wm):
    # the same 6 degrees of apparent shift: a mug (about 1 m away by its size) can be a viewpoint effect; a plant (about 3.6 m) cannot
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0), ("plant", 200.0, 0.0)], [("mug", 86.0, 0.0), ("plant", 206.0, 0.0)], shift=0.0)
    d = changes.compare_scans(wm, a, b)
    assert d["alignment"]["quality"] == "good"
    assert labels(d, "moved") == ["plant"] and d["unchanged"] == 1                     # the mug is within tolerance, the plant is not
    plant = d["moved"][0]
    assert plant["tolerance_deg"] < 4 and plant["distance_cm"] > 250


def test_a_shift_in_the_doubtful_range_is_possibly_moved_not_moved(wm):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0)], [("mug", 92.0, 0.0)], shift=0.0)      # 12 degrees: more than one tolerance (about 8), less than two
    d = changes.compare_scans(wm, a, b, align=GOOD)                                       # an alignment with no local evidence: only the viewpoint model
    assert labels(d, "possibly_moved") == ["mug"] and d["moved"] == [] and "may have moved" in d["speech"]
    e = d["possibly_moved"][0]
    assert e["tolerance_deg"] < e["sep_deg"] <= 2 * e["tolerance_deg"] + 0.5 and e["distance_lo_cm"] < e["distance_cm"] < e["distance_hi_cm"]
    assert e["local"] is None


def test_the_texture_around_an_object_settles_a_doubtful_shift(wm):
    # The same 12 degrees, but now the images are compared: this room has no depth, so the texture right around the mug did not shift at all
    # while the mug did. A different viewpoint would have shifted both; so it really moved.
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0)], [("mug", 92.0, 0.0)], shift=0.0)
    d = changes.compare_scans(wm, a, b)
    assert labels(d, "moved") == ["mug"] and d["possibly_moved"] == []
    lev = d["moved"][0]["local"]
    assert lev["views"] >= 1 and lev["residual_deg"] > changes.LOCAL_MOVED_K * lev["tolerance_deg"] and 9.0 < lev["residual_deg"] < 15.0


def test_a_larger_assumed_viewpoint_error_widens_the_tolerance(wm):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0)], [("mug", 92.0, 0.0)], shift=0.0)
    tight = changes.compare_scans(wm, a, b, viewpoint_cm=5.0)
    loose = changes.compare_scans(wm, a, b, viewpoint_cm=40.0)
    assert labels(tight, "moved") == ["mug"] and loose["unchanged"] == 1 and loose["moved"] == []


def test_objects_that_vanish_or_appear_need_the_other_scan_to_have_looked_there(wm):
    a = add_scan(wm, wm.room, PANO, YAWS, ts=100.0, objects=[("mug", 80.0, 0.0), ("book", 240.0, 0.0)])
    b = add_scan(wm, wm.room, PANO, [0, 40, 80, 120], true_jitter=(9.0, 1.0), ts=900.0, objects=[("pen", 40.0, 0.0), ("lamp", 80.0, 0.0)])
    d = changes.compare_scans(wm, a, b)
    assert d["alignment"]["quality"] == "good"
    assert labels(d, "removed") == ["mug"]                                              # B looked toward 80 and the mug is not there
    assert sorted((e["label"], e["was"]) for e in d["cant_compare"]) == [("book", "removed?")] or ("book", "removed?") in [(e["label"], e["was"]) for e in d["cant_compare"]]
    assert "pen" in labels(d, "added") + [e["label"] for e in d["cant_compare"]]


@pytest.mark.parametrize("quality", ["weak", "failed"])
def test_nothing_is_called_moved_new_or_removed_when_the_alignment_is_weak_or_failed(wm, quality):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0), ("laptop", 200.0, 0.0)], [("mug", 140.0, 0.0), ("lamp", 20.0, 0.0)], shift=0.0)
    al = {**GOOD, "quality": quality, "pairs": 1, "inliers": 30, "spread_deg": 4.0}
    d = changes.compare_scans(wm, a, b, align=al)
    assert d["moved"] == [] and d["possibly_moved"] == [] and d["removed"] == [] and d["added"] == []
    assert {e["was"] for e in d["cant_compare"]} == {"moved?", "removed?", "added?"} and all("cannot be called a move" in e["reason"] for e in d["cant_compare"])
    assert "moved" not in d["speech"].replace("cannot say what moved", "") and "cannot say what moved" in d["speech"]


def test_an_object_within_tolerance_is_still_unchanged_when_the_alignment_is_weak(wm):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0)], [("mug", 82.0, 0.0)], shift=0.0)
    d = changes.compare_scans(wm, a, b, align={**GOOD, "quality": "weak"})
    assert d["unchanged"] == 1 and d["cant_compare"] == []


def test_pictures_that_cannot_be_matched_give_a_failed_alignment_and_nothing_moved(wm):
    a = wm.start_scan(ts=1.0, room_id=wm.room)
    b = wm.start_scan(ts=2.0, room_id=wm.room)
    for sid, y in ((a, 0.0), (b, 60.0)):
        fid = wm.add_frame(sid, 1.0, y, 0.0, 480, 640, roll=0.0, screen_angle=0.0, hfov_deg=60.0, jpeg=b"jpg")
        wm.observe(fid, [{"label": "mug", "box": [220.0, 300.0, 260.0, 340.0]}])
        wm.end_scan(sid, 5.0)
    d = changes.compare_scans(wm, a, b)
    assert d["alignment"]["quality"] == "failed" and d["moved"] == [] and len(d["cant_compare"]) == 1 and d["cant_compare"][0]["was"] == "moved?"


def test_the_alignment_of_a_pair_is_computed_once_and_dropped_with_either_scan(wm):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0)], [("mug", 80.0, 0.0)], shift=0.0)
    assert wm.get_alignment(a, b) is None
    changes.compare_scans(wm, a, b)
    assert wm.get_alignment(a, b)["quality"] == "good"
    wm.delete_scan(b)
    assert wm.get_alignment(a, b) is None


# ------------------------------------------------------------------------------------------------ the size and distance model

def test_sizes_come_from_spark_then_the_defaults_then_a_fallback(wm):
    assert scale.size_for(wm, "mug") == (10.0, "default") and scale.size_for(wm, "zzz") == (scale.FALLBACK_CM, "fallback")
    wm.record_label_size("mug", 12.0)
    assert scale.size_for(wm, "mug") == (12.0, "spark")
    wm.record_label_size("mug", 8.0)                                   # a running geometric mean: one answer does not dominate
    assert 9.5 < scale.size_for(wm, "mug")[0] < 10.5
    wm.record_label_size("mug", 0.0)
    wm.record_label_size("mug", 9000.0)
    assert 9.5 < scale.size_for(wm, "mug")[0] < 10.5                    # absurd values are ignored


def test_tolerance_falls_with_distance_and_with_the_share_of_the_movement_across_the_view():
    near, far = scale.tolerance_deg(100, 15), scale.tolerance_deg(400, 15)
    assert near > 3 * far > 3 and scale.tolerance_deg(100, 30) > near
    assert scale.tolerance_deg(100, 15, across=0.0) == pytest.approx(scale.tolerance_deg(100, 15, across=scale.MIN_PERP))     # never below a third
    assert scale.tolerance_deg(100, 15, across=1.0) == pytest.approx(near)
    along = scale.across_share({"east": 0.0, "north": 1.0, "up": 0.0}, 0.0, 0.0)      # moving straight toward what you look at
    side = scale.across_share({"east": 1.0, "north": 0.0, "up": 0.0}, 0.0, 0.0)
    assert along == pytest.approx(0.0, abs=1e-6) and side == pytest.approx(1.0) and scale.across_share(None, 0, 0) is None


def test_the_distance_band_widens_when_the_size_is_a_guess(wm):
    a = add_scan(wm, wm.room, PANO, [0, 40], ts=1.0, objects=[("mug", 10.0, 0.0), ("zzz thing", 50.0, 0.0)])
    objs = {o["label"]: o for o in wm.list_objects(a)}
    known = scale.distance_estimate(wm, objs["mug"], 10.0, "default")
    guessed = scale.distance_estimate(wm, objs["zzz thing"], 30.0, "fallback")
    assert known["lo_cm"] < known["distance_cm"] < known["hi_cm"] and 90 < known["distance_cm"] < 120        # 10 cm seen as 40 px at a 415 px focal length
    assert guessed["rel_err"] > known["rel_err"]
    assert scale.distance_estimate(wm, {"id": 999999}, 10.0) is None


# ------------------------------------------------------------------------------------------------ what real scans taught

def test_a_translation_the_pairs_disagree_on_is_not_used_to_shrink_the_tolerance(wm):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0)], [("mug", 80.0, 0.0)], shift=0.0)
    obj = wm.list_objects(a)[0]
    al_trusted = {**GOOD, "translation": {"east": 0.0, "north": 1.0, "up": 0.0, "consistency": 0.95, "trusted": True}}
    al_noisy = {**GOOD, "translation": {"east": 0.0, "north": 1.0, "up": 0.0, "consistency": 0.3, "trusted": False}}
    # the mug sits at yaw 80 (east) and the trusted movement is toward north: mostly across the view, so the tolerance stays; if it were toward the mug it would shrink
    near = changes.compare_scans(wm, a, b, align={**al_trusted, "translation": {"east": 1.0, "north": 0.0, "up": 0.0, "consistency": 0.95, "trusted": True}})
    assert near["unchanged"] == 1
    assert scale.across_share(None, obj["yaw"], obj["pitch"]) is None
    d_noisy = changes.compare_scans(wm, a, b, align=al_noisy)
    assert d_noisy["alignment"]["translation"]["trusted"] is False and d_noisy["unchanged"] == 1


def test_several_objects_of_one_kind_cannot_be_called_moved_only_possibly_moved(wm):
    a, b = pair_shifted(wm, [("cable", 80.0, 0.0), ("cable", 200.0, 0.0)], [("cable", 80.0, 0.0), ("cable", 140.0, 0.0)], shift=0.0)   # one cable 'moved' 60 degrees: or a different cable
    d = changes.compare_scans(wm, a, b)
    assert d["moved"] == [] and len(d["possibly_moved"]) == 1 and d["possibly_moved"][0]["ambiguous"] is True
    assert "several" in d["possibly_moved"][0]["speech"]


def test_many_possibly_moved_objects_of_one_kind_are_one_sentence(wm):
    a, b = pair_shifted(wm, [("mug", 40.0, 0.0), ("mug", 120.0, 0.0), ("mug", 200.0, 0.0)], [("mug", 52.0, 0.0), ("mug", 132.0, 0.0), ("mug", 212.0, 0.0)], shift=0.0)
    d = changes.compare_scans(wm, a, b)
    assert len(d["possibly_moved"]) == 3 and d["speech"].count("may have moved") == 1 and "3 mugs may have moved" in d["speech"]


def test_a_stored_alignment_from_an_older_version_is_recomputed(wm):
    a, b = pair_shifted(wm, [("mug", 80.0, 0.0)], [("mug", 80.0, 0.0)], shift=0.0)
    wm.set_alignment(a, b, {"quality": "weak", "offset_deg": 0.0, "spread_deg": 3.0, "inliers": 10, "pairs": 1})        # an older result: no version
    assert changes.alignment_for(wm, a, b)["quality"] == "good"
    assert wm.get_alignment(a, b)["version"] == changes.align_mod.ALIGN_VERSION


# ------------------------------------------------------------------------------------------------ evidence for gone and new

def test_an_object_seen_only_once_is_never_called_removed_or_new(wm):
    a = add_scan(wm, wm.room, PANO, YAWS, ts=100.0, objects=[("mug", 80.0, 0.0), ("book", 240.0, 0.0)], sightings=1)
    b = add_scan(wm, wm.room, PANO, YAWS, true_jitter=(9.0, 1.0), ts=900.0, objects=[("book", 240.0, 0.0), ("lamp", 160.0, 0.0)], sightings=1)
    d = changes.compare_scans(wm, a, b)
    assert d["alignment"]["quality"] == "good" and d["removed"] == [] and d["added"] == []
    why = {(e["label"], e["was"]): e["reason"] for e in d["cant_compare"]}
    assert "seen only once" in why[("mug", "removed?")] and "seen only once" in why[("lamp", "added?")]


def test_an_object_seen_twice_where_the_rescan_looked_is_removed(wm):
    a = add_scan(wm, wm.room, PANO, YAWS, ts=100.0, objects=[("mug", 80.0, 0.0), ("book", 240.0, 0.0)])
    b = add_scan(wm, wm.room, PANO, YAWS, true_jitter=(9.0, 1.0), ts=900.0, objects=[("book", 240.0, 0.0)])
    d = changes.compare_scans(wm, a, b)
    assert labels(d, "removed") == ["mug"] and d["cant_compare"] == []


# ------------------------------------------------------------------------------------------------ moved, low confidence (a weak alignment)

WEAK = {**GOOD, "quality": "weak", "pairs": 1, "inliers": 84, "offset_deg": 0.6, "spread_deg": 0.0, "offset_sem_deg": 1.3}


def far_move(wm, a_objs=(("plant", 80.0, 0.0), ("laptop", 200.0, 0.0)), b_objs=(("plant", 110.0, 0.0), ("laptop", 200.0, 0.0)), shift=0.0):
    return pair_shifted(wm, list(a_objs), list(b_objs), shift=shift)


def test_a_weak_alignment_calls_a_far_move_moved_low_confidence_in_its_own_list_and_says_check_it(wm):
    a, b = far_move(wm)                                            # a plant about 3.6 m away moved 30 degrees: its tolerance is about 2.4
    d = changes.compare_scans(wm, a, b, align=WEAK, low_confidence=True)
    assert d["moved"] == [] and d["possibly_moved"] == [] and labels(d, "moved_low_confidence") == ["plant"]
    e = d["moved_low_confidence"][0]
    assert e["low_confidence"] is True and e["sep_deg"] > changes.LOW_CONF_K * e["tolerance_deg"] + changes.LOW_CONF_UNC_FLOOR
    assert "seems to have moved" in d["speech"] and "check it" in d["speech"] and "lined up only roughly" in d["speech"]
    assert d["removed"] == [] and d["added"] == [] and d["unchanged"] == 1


@pytest.mark.parametrize("case", ["switched off", "failed", "repeated label", "offset disagrees with the compass", "too few points", "shift not far enough"])
def test_moved_low_confidence_never_appears_outside_its_narrow_case(wm, case):
    a_objs, b_objs, al, shift, on = [("plant", 80.0, 0.0), ("laptop", 200.0, 0.0)], [("plant", 110.0, 0.0), ("laptop", 200.0, 0.0)], dict(WEAK), 0.0, True
    if case == "switched off":
        on = False
    elif case == "failed":
        al["quality"] = "failed"
    elif case == "repeated label":
        a_objs, b_objs = a_objs + [("plant", 300.0, 0.0)], b_objs + [("plant", 300.0, 0.0)]
    elif case == "offset disagrees with the compass":
        shift, al["offset_deg"] = 25.0, 25.0
    elif case == "too few points":
        al["inliers"] = changes.LOW_CONF_MIN_INLIERS - 1
    elif case == "shift not far enough":
        b_objs = [("plant", 86.0, 0.0), ("laptop", 200.0, 0.0)]  # 6 degrees: beyond the tolerance, not three times it plus the uncertainty
    a, b = far_move(wm, a_objs, b_objs, shift)
    d = changes.compare_scans(wm, a, b, align=al, low_confidence=on)
    assert d["moved_low_confidence"] == [] and d["moved"] == [] and d["removed"] == [] and d["added"] == [], case
    assert any(e["label"] == "plant" and e["was"] == "moved?" for e in d["cant_compare"]), case


def test_the_switch_is_one_module_setting(wm, monkeypatch):
    a, b = far_move(wm)
    monkeypatch.setattr(changes, "MOVED_LOW_CONFIDENCE", False)
    off = changes.compare_scans(wm, a, b, align=WEAK)
    monkeypatch.setattr(changes, "MOVED_LOW_CONFIDENCE", True)
    on = changes.compare_scans(wm, a, b, align=WEAK)
    assert off["moved_low_confidence"] == [] and off["alignment"]["low_confidence_enabled"] is False
    assert labels(on, "moved_low_confidence") == ["plant"] and on["alignment"]["low_confidence_enabled"] is True
