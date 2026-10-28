import test from "node:test";
import assert from "node:assert/strict";
import { guide, coneDeg } from "../../web/js/guide.js";

// Expected values come from backend/app/guidance.py (width 540, height 960, hfov 60) for the same inputs,
// so the phone's arrow and hint agree with the server-side maths.
// [targetYaw, targetPitch, camYaw, camPitch, roll, yawOffset] -> distance, arrow, inView, pixel, arrived, instruction, dyaw
const CASES = [
  [[0, 0, 0, 0, 0, 0], 0.0, 90.0, true, [270.0, 480.0], true, "That's it.", 0.0],
  [[70, 0, 0, 0, 0, 0], 70.0, 90.0, false, [1554.8680304487807, 480.0], false, "Turn right.", 70.0],
  [[35, 0, 0, 0, 0, 0], 35.0, 90.0, false, [597.4546586459246, 480.0], false, "A bit more right.", 35.0],
  [[15, 0, 0, 0, 0, 0], 15.0, 90.0, true, [395.30743608719376, 480.0], false, "Almost there, a little right.", 15.0],
  [[350, 0, 10, 0, 0, 0], 20.0, 270.0, true, [99.7879666882115, 480.0], false, "Almost there, a little left.", -20.0],
  [[0, 60, 0, 0, 0, 0], 60.0, 0.0, false, [270.0, -330.0], false, "Tilt up.", 0.0],
  [[0, -60, 0, 0, 0, 0], 60.0, 180.0, false, [270.0, 1290.0], false, "Tilt down.", 0.0],
  [[180, 0, 0, 0, 0, 0], 180.0, 90.0, false, null, false, "Turn around.", -180.0],
  [[40, 0, 0, 0, 90, 0], 40.0, 0.0, false, [270.0, 87.59193767093421], false, "A bit more up.", 40.0],
  [[20, 0, 120, 0, 0, 100], 0.0, 90.0, true, [270.0, 480.0], true, "That's it.", 0.0],
  [[200, -15, 170, -12, 8, 0], 29.30249970795086, 91.31773369154871, false, [532.3924449536679, 486.03577338791166], false, "A bit more right.", 30.0],
  [[300, 35, 20, -10, -20, 0], 87.67987544288142, 326.16032054332345, false, [-6157.659058409568, -9107.153166637367], false, "Tilt up.", -80.0],
  [[10, 5, 0, 0, 0, 0], 11.168952812354922, 63.25979464427289, true, [352.4599681197152, 438.45443066484836], false, "Almost there, a little right.", 10.0],
  [[0, 0, 0, -90, 0, 0], 90.0, 0.0, false, null, false, "Tilt up.", 0.0],
];

const circ = (a, b) => Math.abs(((a - b + 540) % 360) - 180);

for (const [[ty, tp, cy, cp, roll, off], dist, arrow, inView, pixel, arrived, text, dyaw] of CASES) {
  test(`guide target (${ty},${tp}) camera (${cy},${cp}) roll ${roll} offset ${off}`, () => {
    const g = guide({ yaw: ty, pitch: tp }, { yaw: cy, pitch: cp, roll, width: 540, height: 960, hfov: 60 }, { yawOffset: off });
    assert.ok(Math.abs(g.distanceDeg - dist) < 1e-4, `distance ${g.distanceDeg} vs ${dist}`);
    if (!(cp === -90)) assert.ok(circ(g.arrowDeg, arrow) < 1e-4, `arrow ${g.arrowDeg} vs ${arrow}`);
    assert.equal(g.inView, inView);
    assert.equal(g.arrived, arrived);
    assert.equal(g.instruction, text);
    assert.ok(Math.abs(g.dyaw - dyaw) < 1e-4);
    if (pixel === null) assert.equal(g.pixel, null);
    else {
      assert.ok(Math.abs(g.pixel[0] - pixel[0]) < 1e-3 * Math.max(1, Math.abs(pixel[0])));
      assert.ok(Math.abs(g.pixel[1] - pixel[1]) < 1e-3 * Math.max(1, Math.abs(pixel[1])));
    }
  });
}

test("the arrow's cone is narrow for a far target, wide for a near one, and the half-turn inside the error", () => {
  assert.equal(coneDeg(50, 0), 0);
  assert.ok(Math.abs(coneDeg(100, 10) - 5.739) < 1e-3);   // asin(10 / 100)
  assert.ok(coneDeg(20, 10) > coneDeg(60, 10));
  assert.equal(coneDeg(8, 10), 90);
  const g = guide({ yaw: 40, pitch: 0 }, { yaw: 0, pitch: 0, roll: 0, width: 540, height: 960, hfov: 60 }, { uncertaintyDeg: 8 });
  assert.ok(Math.abs(g.coneDeg - (Math.asin(8 / g.distanceDeg) * 180) / Math.PI) < 1e-9);
  assert.equal(guide({ yaw: 40, pitch: 0 }, { yaw: 0, pitch: 0, roll: 0, width: 540, height: 960, hfov: 60 }).coneDeg, 0);
});
