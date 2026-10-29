import test from "node:test";
import assert from "node:assert/strict";
import { Coverage, KeyframePolicy, angularDistance, wrap180 } from "../../web/js/keyframe.js";

// Feed a path of [t_ms, yaw, pitch?] samples and return the captures and the hints seen.
function run(policy, path) {
  const taken = [], hints = new Set();
  for (const [t, yaw, pitch = 0] of path) {
    const r = policy.update({ yaw: ((yaw % 360) + 360) % 360, pitch, t });
    if (r.capture) taken.push([t, Math.round(yaw), r.reason]);
    if (r.hint) hints.add(r.hint);
  }
  return { taken, hints };
}

const sweep = (degPerS, fromYaw, toYaw, t0 = 0, hz = 20) => {
  const dir = Math.sign(toYaw - fromYaw), out = [];
  for (let t = 0; ; t += 1000 / hz) {
    const yaw = fromYaw + dir * degPerS * (t / 1000);
    if ((yaw - toYaw) * dir > 0) break;
    out.push([t0 + t, yaw]);
  }
  return out;
};

test("distance and wrap", () => {
  assert.ok(Math.abs(angularDistance({ yaw: 350, pitch: 0 }, { yaw: 10, pitch: 0 }) - 20) < 1e-9);
  assert.ok(Math.abs(angularDistance({ yaw: 0, pitch: 0 }, { yaw: 0, pitch: 90 }) - 90) < 1e-9);
  assert.equal(wrap180(190), -170);
});

test("a slow steady pan takes one frame per step and a first frame at the start", () => {
  const { taken } = run(new KeyframePolicy({ stepDeg: 18 }), sweep(9, 0, 180)); // 9 deg/s for 20 s
  assert.equal(taken[0][2], "first");
  assert.ok(taken.length >= 9 && taken.length <= 11, `got ${taken.length}`);
  for (let i = 1; i < taken.length; i++) {
    const d = taken[i][1] - taken[i - 1][1];
    assert.ok(d >= 17 && d <= 22, `step ${d}`);
  }
});

test("a fast pan does not take blurry frames and says slow down", () => {
  const { taken, hints } = run(new KeyframePolicy(), sweep(60, 0, 180));
  assert.equal(taken.length, 0);
  assert.ok(hints.has("slow down"));
});

test("overdue frame is taken at medium speed, but not past the hard limit", () => {
  const p = new KeyframePolicy({ stepDeg: 18, maxSpeed: 12, hardSpeed: 30 });
  const { taken } = run(p, [...sweep(5, 0, 5), ...sweep(20, 5, 120, 1000)]); // start slow, then 20 deg/s
  assert.ok(taken.some((x) => x[2] === "overdue"), JSON.stringify(taken));
});

test("standing still takes nothing after the first frame", () => {
  const still = Array.from({ length: 60 }, (_, i) => [i * 50, 100, -10]);
  assert.equal(run(new KeyframePolicy(), still).taken.length, 1);
});

test("a pitch change counts as turning", () => {
  const path = [...Array.from({ length: 10 }, (_, i) => [i * 50, 0, 0]), ...Array.from({ length: 80 }, (_, i) => [500 + i * 50, 0, i * 0.5])];
  const { taken } = run(new KeyframePolicy({ stepDeg: 18 }), path); // 10 deg/s up to 40 deg
  assert.ok(taken.length >= 2, JSON.stringify(taken));
});

test("min interval holds back the next frame", () => {
  const p = new KeyframePolicy({ stepDeg: 5, minIntervalMs: 2000 });
  const { taken } = run(p, sweep(8, 0, 40)); // 5 s
  assert.ok(taken.length <= 3, JSON.stringify(taken));
});

test("undo lets the next sample retry a frame that was not sent", () => {
  const p = new KeyframePolicy();
  const a = run(p, sweep(5, 0, 3)).taken.length;
  assert.equal(a, 1);
  p.undo();
  assert.equal(p.last, null);
});

test("coverage fills around frames, wraps north, and points at the nearest gap", () => {
  const c = new Coverage(); // 60 degree FOV: each frame covers yaw +- 24
  assert.equal(c.fraction, 0);
  c.mark(355);
  assert.ok(c.bins[33] && c.bins[35] && c.bins[0] && c.bins[1] && !c.bins[2] && !c.bins[30]); // covers 331..19
  for (let y = 0; y < 360; y += 20) c.mark(y);
  assert.equal(c.fraction, 1);
  assert.equal(c.nextGap(10), null);
  const g = new Coverage();
  g.mark(0); g.mark(30); g.mark(60); // covers -24..84
  assert.deepEqual(g.nextGap(60), { dir: "right", deg: 30 }); // the first bin that is not mostly covered is 90
  g.reset();
  g.mark(180);
  assert.equal(g.nextGap(180).deg, 30);
});

test("coverage uses the frame's own field of view and reports pitch bands", () => {
  const wide = new Coverage({ hfov: 90 }), narrow = new Coverage({ hfov: 40 });
  wide.mark(0); narrow.mark(0);
  assert.ok(wide.fraction > narrow.fraction * 2);
  const c = new Coverage();
  c.mark(0, -45); c.mark(90, 0);
  assert.ok(c.bands.low.covered_deg > 0 && c.bands.mid.covered_deg > 0 && c.bands.high.covered_deg === 0);
});
