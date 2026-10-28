import pytest

from app import anchoring as A
from app.guidance import guide

W, H = 480, 640


def g(t_yaw, t_pitch=0.0, cam_yaw=0.0, cam_pitch=0.0, roll=0.0, **kw):
    return guide(t_yaw, t_pitch, cam_yaw, cam_pitch, roll, W, H, **kw)


def test_target_dead_ahead_arrives():
    r = g(0.0)
    assert r["in_view"] and r["arrived"] and r["instruction"] == "That's it."
    assert r["distance_deg"] < 1e-6 and r["pixel"] == pytest.approx((W / 2, H / 2))


def test_target_right_far_then_near():
    far = g(70.0)
    assert not far["in_view"] and far["arrow_deg"] == pytest.approx(90.0, abs=1.0) and far["instruction"] == "Turn right."
    assert far["dyaw"] == pytest.approx(70.0)
    mid = g(35.0)  # outside the 60 degree FOV margin, closer than FAR_DEG
    assert not mid["in_view"] and mid["instruction"] == "A bit more right."
    near = g(15.0)
    assert near["in_view"] and not near["arrived"] and near["instruction"] == "Almost there, a little right."


def test_target_left_across_the_wrap():
    r = g(350.0, cam_yaw=10.0)
    assert r["dyaw"] == pytest.approx(-20.0) and r["arrow_deg"] == pytest.approx(270.0, abs=1.0)
    assert "left" in r["instruction"]


def test_target_above_and_below_use_tilt():
    up = g(0.0, 60.0)
    assert not up["in_view"] and up["arrow_deg"] == pytest.approx(0.0, abs=1.0) and up["instruction"] == "Tilt up."
    down = g(0.0, -60.0)
    assert down["arrow_deg"] == pytest.approx(180.0, abs=1.0) and down["instruction"] == "Tilt down."


def test_behind_the_camera():
    r = g(180.0)
    assert r["pixel"] is None and not r["in_view"] and r["instruction"] == "Turn around."


def test_roll_rotates_the_arrow_into_image_axes():
    # phone rolled 90 degrees clockwise: image-up now points to world-right (see anchoring.camera_basis)
    r = g(40.0, roll=90.0)
    assert r["arrow_deg"] == pytest.approx(0.0, abs=1.0) and r["instruction"] == "A bit more up."


def test_yaw_offset_maps_scan_frame_to_live_frame():
    # the relative sensor's zero moved by 100 degrees since the scan: anchor 20 in the scan is 120 live
    r = g(20.0, cam_yaw=120.0, yaw_offset=100.0)
    assert r["arrived"]
    assert not g(20.0, cam_yaw=120.0)["arrived"]


def test_in_view_pixel_matches_anchoring():
    r = g(10.0, 5.0)
    assert r["pixel"] == pytest.approx(A.direction_to_pixel(10.0, 5.0, W, H, 0.0, 0.0))


def test_looking_straight_down_does_not_crash():
    r = g(0.0, 0.0, cam_pitch=-90.0)
    assert 0.0 <= r["arrow_deg"] < 360.0 and r["instruction"]
