import math

import pytest

from app import anchoring as A

W, H = 480, 640  # portrait phone frame


def close(a, b, tol=1e-6):
    return abs(A.wrap180(a - b)) < tol


def test_centre_pixel_is_camera_direction():
    for yaw, pitch, roll in [(0, 0, 0), (123, 25, 10), (300, -40, -30), (45, 85, 5)]:
        y, p = A.pixel_to_direction(W / 2, H / 2, W, H, yaw, pitch, roll)
        assert close(y, yaw, 1e-6) and abs(p - pitch) < 1e-6


def test_image_edge_matches_hfov():
    y, p = A.pixel_to_direction(0, H / 2, W, H, 90, 0, 0, hfov_deg=60)
    assert close(y, 90 - 30, 1e-6) and abs(p) < 1e-6  # left edge is half the FOV to the left
    y, _ = A.pixel_to_direction(W, H / 2, W, H, 90, 0, 0, hfov_deg=60)
    assert close(y, 90 + 30, 1e-6)


def test_right_is_clockwise_and_up_is_up():
    y, _ = A.pixel_to_direction(W * 0.75, H / 2, W, H, 10, 0)
    assert A.wrap180(y - 10) > 0
    _, p = A.pixel_to_direction(W / 2, H * 0.25, W, H, 10, 0)
    assert p > 0


def test_wraps_across_north():
    y, _ = A.pixel_to_direction(W * 0.9, H / 2, W, H, 355, 0)
    assert 0 <= y < 360 and A.wrap180(y - 355) > 0


def test_roll_90_turns_image_up_to_the_right():
    # phone rolled clockwise 90: the top of the image points to the right of the heading
    y, p = A.pixel_to_direction(W / 2, H * 0.25, W, H, 0, 0, roll_deg=90)
    assert A.wrap180(y) > 5 and abs(p) < 1e-6


def test_screen_angle_adds_to_roll():
    assert A.image_roll(-10, 90) == 80
    assert A.image_roll(None, None) == 0


@pytest.mark.parametrize("pose", [(0, 0, 0), (200, 30, 15), (80, -45, -20), (10, 80, 0)])
def test_round_trip(pose):
    for u, v in [(50, 60), (400, 100), (240, 320), (100, 600)]:
        yaw, pitch = A.pixel_to_direction(u, v, W, H, *pose)
        back = A.direction_to_pixel(yaw, pitch, W, H, *pose)
        assert back is not None and abs(back[0] - u) < 1e-6 and abs(back[1] - v) < 1e-6


def test_behind_camera_is_none():
    assert A.direction_to_pixel(180, 0, W, H, 0, 0) is None


def test_straight_up_is_defined():
    y, p = A.pixel_to_direction(W / 2, H / 2, W, H, 0, 90)
    assert abs(p - 90) < 1e-6
    y2, p2 = A.pixel_to_direction(W / 2, H * 0.2, W, H, 0, 90)
    assert p2 < 90


def test_angular_distance_and_mean_across_wrap():
    assert abs(A.angular_distance(350, 0, 10, 0) - 20) < 1e-9
    assert abs(A.angular_distance(0, 90, 123, 90)) < 1e-9
    y, p = A.mean_direction([(350, 0, 1), (10, 0, 1)])
    assert close(y, 0, 1e-6) and abs(p) < 1e-6
    y, _ = A.mean_direction([(350, 0, 3), (10, 0, 1)])
    assert A.wrap180(y) < 0  # weighted toward 350


def test_box_anchor_uses_centre():
    y1, p1 = A.box_anchor((200, 300, 280, 340), W, H, 40, 5)
    y2, p2 = A.pixel_to_direction(240, 320, W, H, 40, 5)
    assert close(y1, y2) and abs(p1 - p2) < 1e-9
