"""Writes tests/js/coverage.test.mjs: the fixed test body plus golden values from the Python coverage module."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
golden = subprocess.run([sys.executable, str(root / "scripts" / "golden_coverage.py")], capture_output=True, text=True, check=True, cwd=root).stdout.strip()
body = '''import test from "node:test";
import assert from "node:assert/strict";
import { coverage, vfovDeg } from "../../web/js/coverage.js";

// GOLDEN values come from backend/app/coverage.py (scripts/golden_coverage.py): the phone and the server must agree
// on what has been covered, to the degree, for the same frames.
''' + golden + '''

for (const c of CASES) {
  test(`coverage matches the Python module: ${c.name}`, () => {
    const got = coverage(c.frames);
    assert.equal(got.covered_deg, c.want.covered_deg);
    assert.ok(Math.abs(got.fraction - c.want.fraction) < 1e-12);
    assert.deepEqual(got.gaps, c.want.gaps);
    assert.deepEqual(got.bins, c.want.bins);
    for (const b of ["low", "mid", "high"]) {
      assert.equal(got.bands[b].covered_deg, c.want.bands[b].covered_deg, `${b} band`);
      assert.deepEqual(got.bands[b].gaps, c.want.bands[b].gaps, `${b} gaps`);
      assert.deepEqual(got.bands[b].bins, c.want.bands[b].bins, `${b} bins`);
    }
  });
}

test("yaw coverage counts a frame at any pitch; the bands separate the pitches", () => {
  const f = (yaw, pitch) => ({ yaw, pitch, hfov_deg: 60, width: 540, height: 960 });
  const r = coverage([f(90, -45), f(200, 0), f(300, 75)]);
  assert.equal(r.covered_deg, 49 * 3);
  assert.ok(r.bands.low.covered_deg > 0 && r.bands.mid.covered_deg > 0 && r.bands.high.covered_deg > 0);
  assert.equal(coverage([f(90, 0)]).bands.high.covered_deg, 0, "a level frame does not cover the ceiling band");
  assert.ok(vfovDeg(60, 540, 960) > 60 && vfovDeg(60, 1920, 1080) < 60, "portrait frames see more vertically than horizontally");
});
'''
(root / "tests" / "js" / "coverage.test.mjs").write_text(body, encoding="utf-8")
print("wrote tests/js/coverage.test.mjs")
