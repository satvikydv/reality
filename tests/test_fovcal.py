import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from app import anchoring
from app.fovcal import CalibrationError, calibrate, capture_state, hfov_from_shot

ROOT = Path(__file__).resolve().parent.parent


def sidecar(width=1080, height=1920, zoom=1.0, locked=True, image_width=1080, **extra):
    return {"width": image_width, "camera": {"settings": {"width": width, "height": height, "zoom": zoom, "zoomLocked": locked,
                                                           "facingMode": "environment", "deviceId": "cam0", **extra}}}


def test_a_known_object_gives_the_known_angle():
    # an object spanning the whole image at distance d with width w sees a FOV of 2 atan(w / 2d)
    assert hfov_from_shot(30, 40, 1080, 1080) == pytest.approx(math.degrees(2 * math.atan(30 / 80)))
    with pytest.raises(CalibrationError):
        hfov_from_shot(0, 40, 100, 1080)


def test_two_shots_in_one_state_give_one_calibration_with_that_state():
    f = lambda d: round(1080 * 29.7 / (2 * d * math.tan(math.radians(33))))  # noqa: E731 - pixels a 66 degree camera would see
    cal = calibrate([{"distance_cm": 40, "pixels": f(40), "sidecar": sidecar()}, {"distance_cm": 70, "pixels": f(70), "sidecar": sidecar()}], 29.7)
    assert cal["hfov_deg"] == pytest.approx(66, abs=0.5) and cal["spread_deg"] < 1
    assert (cal["width"], cal["height"], cal["zoom"]) == (1080, 1920, 1.0)


@pytest.mark.parametrize("other,what", [(dict(width=720, height=1280), "width 1080 vs 720"), (dict(zoom=2.0), "zoom 1.0 vs 2.0"), (dict(deviceId="wide"), "deviceId cam0 vs wide")])
def test_shots_from_different_camera_states_are_refused(other, what):
    shots = [{"distance_cm": 40, "pixels": 600, "sidecar": sidecar()}, {"distance_cm": 70, "pixels": 350, "sidecar": sidecar(**other)}]
    with pytest.raises(CalibrationError, match="different camera state") as e:
        calibrate(shots, 29.7)
    assert what in str(e.value)


def test_a_sample_without_recorded_settings_is_refused():
    with pytest.raises(CalibrationError, match="no camera settings recorded"):
        capture_state({"width": 1080, "camera": {"video_w": 1080, "video_h": 1920}})


def test_the_cli_writes_a_calibration_the_server_then_enforces(tmp_path):
    for i, d in enumerate((40, 70)):
        (tmp_path / f"s{i}.json").write_text(json.dumps(sidecar()), encoding="utf-8")
    out = tmp_path / "cal.json"
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "calibrate_fov.py"), "--object-cm", "29.7", "--shot", "40", "612", str(tmp_path / "s0.json"),
                          "--shot", "70", "352", str(tmp_path / "s1.json"), "--out", str(out)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    cal = json.loads(out.read_text())
    assert cal["width"] == 1080 and 60 < cal["hfov_deg"] < 70
    # the server side: a camera in the calibrated state passes, any other is refused with the difference named
    assert anchoring.check_capture({"settings": {"width": 1080, "height": 1920, "zoom": 1.0}}, cal) is None
    assert "resolution 720x1280" in anchoring.check_capture({"video_w": 720, "video_h": 1280}, cal)
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "calibrate_fov.py"), "--object-cm", "29.7", "--shot", "40", "612", str(tmp_path / "s0.json"),
                          "--out", str(tmp_path / "x.json"), "--dry-run"], capture_output=True, text=True)
    assert bad.returncode == 0
    (tmp_path / "s2.json").write_text(json.dumps(sidecar(zoom=3.0)), encoding="utf-8")
    mixed = subprocess.run([sys.executable, str(ROOT / "scripts" / "calibrate_fov.py"), "--object-cm", "29.7", "--shot", "40", "612", str(tmp_path / "s0.json"),
                            "--shot", "70", "352", str(tmp_path / "s2.json"), "--out", str(tmp_path / "y.json")], capture_output=True, text=True)
    assert mixed.returncode == 1 and "different camera state" in mixed.stderr and not (tmp_path / "y.json").exists()


def test_a_saved_calibration_is_loaded_and_marks_the_fov_calibrated(tmp_path, monkeypatch):
    f = tmp_path / "cal.json"
    f.write_text(json.dumps({"hfov_deg": 66.5, "width": 1080, "height": 1920, "zoom": 1.0}), encoding="utf-8")
    monkeypatch.setattr(anchoring, "CALIBRATION_FILE", f)
    assert anchoring.load_calibration()["hfov_deg"] == 66.5
    monkeypatch.setenv("HFOV_DEG", "70")
    assert anchoring.load_calibration()["hfov_deg"] == 70 and anchoring.load_calibration()["width"] is None
    monkeypatch.delenv("HFOV_DEG")
    f.write_text("not json")
    assert anchoring.load_calibration() is None
