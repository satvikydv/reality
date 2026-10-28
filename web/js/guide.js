// Guided retrieval math on the phone (F12): from the live camera pose to a stored (yaw, pitch) anchor.
// Port of backend/app/guidance.py (same maths, same hint bands); tests/js/guide.test.mjs checks both agree.
// The arrow comes from the target direction in the image's own axes (right, up, forward), so it stays
// correct when the phone is rolled or pitched. Yaw is compass degrees clockwise from north.

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

export const ARRIVE_DEG = 6;
export const TURN_AROUND_DEG = 110;
export const FAR_DEG = 45;

const wrap180 = (d) => ((((d + 180) % 360) + 360) % 360) - 180;
const unit = (yaw, pitch) => [Math.cos(pitch * D2R) * Math.sin(yaw * D2R), Math.cos(pitch * D2R) * Math.cos(yaw * D2R), Math.sin(pitch * D2R)];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

// (right, up, forward) of the image in earth coordinates; roll is the image roll (sensor roll + screen angle).
export function cameraBasis(yaw, pitch, roll = 0) {
  const fwd = unit(yaw, pitch);
  const y = yaw * D2R;
  const rightRef = [Math.cos(y), -Math.sin(y), 0];
  const upRef = cross(rightRef, fwd);
  const c = Math.cos(roll * D2R), s = Math.sin(roll * D2R);
  const up = upRef.map((u, i) => c * u + s * rightRef[i]);
  const right = rightRef.map((r, i) => c * r - s * upRef[i]);
  return { right, up, fwd };
}

export function angularDistance(yaw1, pitch1, yaw2, pitch2) {
  return Math.acos(Math.max(-1, Math.min(1, dot(unit(yaw1, pitch1), unit(yaw2, pitch2))))) * R2D;
}

// Pixel where a world direction appears, or null when it is behind the camera. May lie outside the image.
export function directionToPixel(yaw, pitch, width, height, camYaw, camPitch, camRoll = 0, hfov = 60) {
  const { right, up, fwd } = cameraBasis(camYaw, camPitch, camRoll);
  const d = unit(yaw, pitch);
  const z = dot(d, fwd);
  if (z <= 1e-6) return null;
  const f = (width / 2) / Math.tan((hfov * D2R) / 2);
  return [width / 2 + (f * dot(d, right)) / z, height / 2 - (f * dot(d, up)) / z];
}

// Half-angle of the cone the arrow could really point into when the stored direction is only known to uncertaintyDeg.
// Port of guidance.cone_deg.
export function coneDeg(distanceDeg, uncertaintyDeg) {
  if (!(uncertaintyDeg > 0)) return 0;
  if (distanceDeg <= uncertaintyDeg) return 90;
  return Math.asin(uncertaintyDeg / distanceDeg) * R2D;
}

export function instruction(distanceDeg, x, y, inView, arrived) {
  if (arrived) return "That's it.";
  if (distanceDeg > TURN_AROUND_DEG) return "Turn around.";
  let word, verb;
  if (Math.abs(x) >= Math.abs(y)) { word = x > 0 ? "right" : "left"; verb = "Turn"; } else { word = y > 0 ? "up" : "down"; verb = "Tilt"; }
  if (inView) return `Almost there, a little ${word}.`;
  return distanceDeg >= FAR_DEG ? `${verb} ${word}.` : `A bit more ${word}.`;
}

// target {yaw, pitch}; cam {yaw, pitch, roll, width, height, hfov}. Returns
// {distanceDeg, dyaw, dpitch, arrowDeg (clockwise from up), coneDeg, inView, pixel, arrived, instruction}.
export function guide(target, cam, { yawOffset = 0, margin = 0.1, arriveDeg = ARRIVE_DEG, uncertaintyDeg = 0 } = {}) {
  const { yaw: camYaw, pitch: camPitch, roll = 0, width, height, hfov = 60 } = cam;
  const tYaw = (((target.yaw + yawOffset) % 360) + 360) % 360;
  const { right, up } = cameraBasis(camYaw, camPitch, roll);
  const d = unit(tYaw, target.pitch);
  const x = dot(d, right), y = dot(d, up);
  const arrowDeg = Math.hypot(x, y) > 1e-9 ? ((Math.atan2(x, y) * R2D) % 360 + 360) % 360 : 90;
  const pixel = directionToPixel(tYaw, target.pitch, width, height, camYaw, camPitch, roll, hfov);
  const inView = pixel !== null && pixel[0] >= width * margin && pixel[0] <= width * (1 - margin) && pixel[1] >= height * margin && pixel[1] <= height * (1 - margin);
  const distanceDeg = angularDistance(camYaw, camPitch, tYaw, target.pitch);
  const arrived = inView && distanceDeg <= arriveDeg;
  return { distanceDeg, dyaw: wrap180(tYaw - camYaw), dpitch: target.pitch - camPitch, arrowDeg, coneDeg: coneDeg(distanceDeg, uncertaintyDeg), inView, pixel, arrived, instruction: instruction(distanceDeg, x, y, inView, arrived) };
}
