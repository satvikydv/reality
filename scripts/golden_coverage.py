"""Print the golden values tests/js/coverage.test.mjs compares against: backend/app/coverage.py on fixed frame sets.
Run from the repo root and paste the output over `const CASES` in the test file when the algorithm changes."""
import json
import sys

sys.path.insert(0, "backend")
from app.coverage import coverage  # noqa: E402

P = dict(hfov_deg=60.0, width=540, height=960)
CASES = {
    "empty": [],
    "legacy square frames": [dict(yaw=10, hfov_deg=50.0), dict(yaw=40, hfov_deg=50.0)],
    "full turn, 30 degree steps": [dict(yaw=y, pitch=0, **P) for y in range(0, 360, 30)],
    "across north with half degrees": [dict(yaw=359.5, pitch=0, **P), dict(yaw=0.5, pitch=2, **P), dict(yaw=10.5, pitch=-3, **P)],
    "floor, eye level, ceiling": [dict(yaw=90, pitch=-45, **P), dict(yaw=90, pitch=0, **P), dict(yaw=200, pitch=40, **P), dict(yaw=300, pitch=75, **P)],
    "landscape frames": [dict(yaw=y, pitch=10, hfov_deg=70.0, width=1920, height=1080) for y in (0, 45, 90, 135)],
    "one frame": [dict(yaw=180, pitch=0, **P)],
    "wider FOV, fractional yaws": [dict(yaw=y + 0.4, pitch=-12.5, hfov_deg=72.0, width=720, height=1280) for y in (5, 100, 215)],
}
print("const CASES =", json.dumps([{"name": n, "frames": f, "want": coverage(f)} for n, f in CASES.items()]) + ";")
