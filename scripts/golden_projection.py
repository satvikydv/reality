"""Print the golden values tests/js/project.test.mjs compares against: the Python reference for the rotation-only
homography and the footprint outline (backend/app/anchoring.py). Run from the repo root, paste into the test file."""
import json
import sys

sys.path.insert(0, "backend")
from app import anchoring as A  # noqa: E402

POSE = dict(width=540, height=960, hfov=60)
CASES = [
    ("same pose", dict(yaw=100, pitch=-8, roll=4, **POSE), dict(yaw=100, pitch=-8, roll=4, **POSE)),
    ("turned 30 right, other resolution", dict(yaw=100, pitch=-8, roll=4, **POSE), dict(yaw=130, pitch=5, roll=-6, width=1080, height=1920, hfov=60)),
    ("across north, wider FOV", dict(yaw=350, pitch=10, roll=0, **POSE), dict(yaw=15, pitch=-12, roll=8, width=720, height=1280, hfov=72)),
    ("rolled screen", dict(yaw=200, pitch=0, roll=90, **POSE), dict(yaw=210, pitch=3, roll=90, **POSE)),
    ("target behind the camera", dict(yaw=100, pitch=0, roll=0, **POSE), dict(yaw=280, pitch=0, roll=0, **POSE)),
]
PTS = [(270, 480), (60, 200), (500, 900), (10, 950)]
EXT = A.box_extent((100, 300, 300, 500), 540, 960, 100.0, -8.0, 0.0, 60.0)
VIEWS = [dict(yaw=100, pitch=-8, roll=0, **POSE), dict(yaw=120, pitch=0, roll=10, width=1080, height=1920, hfov=65), dict(yaw=280, pitch=0, roll=0, **POSE)]

homography = [dict(name=n, cap=c, cur=u, pts=PTS, H=A.homography(c, u), warped=A.warp_points(A.homography(c, u), PTS)) for n, c, u in CASES]
footprint = [dict(cur=v, outline=A.footprint_outline(EXT, v, 4)) for v in VIEWS]
print("const EXT =", json.dumps(EXT) + ";")
print("const HOMOGRAPHY =", json.dumps(homography) + ";")
print("const FOOTPRINT =", json.dumps(footprint) + ";")
