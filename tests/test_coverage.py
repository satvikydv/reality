import pytest

from app.coverage import BANDS, coverage, vfov_deg

P = dict(hfov_deg=60.0, width=540, height=960)


def test_a_full_turn_covers_everything_and_no_frames_cover_nothing():
    full = coverage([dict(yaw=y, pitch=0, **P) for y in range(0, 360, 30)])
    assert full["covered_deg"] == 360 and full["fraction"] == 1.0 and full["gaps"] == [] and all(full["bins"])
    none = coverage([])
    assert none["covered_deg"] == 0 and none["gaps"] == [[0, 359]] and not any(none["bins"]) and none["bands"]["mid"]["covered_deg"] == 0


def test_one_frame_covers_its_fov_minus_a_margin_and_wraps_north():
    one = coverage([dict(yaw=359.5, pitch=0, **P)])
    assert one["covered_deg"] == 49  # +-24 degrees: a conservative 80% of the 60 degree view
    assert one["gaps"] == [[25, 335]]  # a single gap, even though the covered part straddles north
    assert [i for i, b in enumerate(one["bins"]) if b] == [0, 1, 2, 34, 35]


def test_a_bin_is_covered_when_at_least_half_its_degrees_are():
    r = coverage([dict(yaw=180, pitch=0, **P)])  # covers 156..204
    assert not r["bins"][15]  # 150..159 has 4 covered degrees
    assert all(r["bins"][16:21])  # 160..209: the last one has exactly 5 (200..204)
    assert not r["bins"][21]


def test_pitch_bands_separate_floor_eye_level_and_ceiling():
    r = coverage([dict(yaw=90, pitch=-45, **P), dict(yaw=200, pitch=0, **P), dict(yaw=300, pitch=75, **P)])
    assert r["covered_deg"] == 49 * 3  # yaw coverage counts a frame at any pitch
    low, mid, high = (r["bands"][n] for n, _, _ in BANDS)
    assert low["covered_deg"] == 49 and mid["covered_deg"] == 49 and high["covered_deg"] == 49
    assert coverage([dict(yaw=90, pitch=0, **P)])["bands"]["high"]["covered_deg"] == 0


def test_portrait_frames_see_more_vertically_and_landscape_less():
    assert vfov_deg(60, 540, 960) > 85 and vfov_deg(60, 1920, 1080) < 40 and vfov_deg(60, 1, 1) == pytest.approx(60)


def test_half_degrees_round_up_as_in_javascript():
    a = coverage([dict(yaw=10.5, **P)])
    b = coverage([dict(yaw=11, **P)])
    assert a["covered_deg"] == b["covered_deg"]  # 10.5 -> 11 (a banker's round would send it to 10)
