// Keyframe selection and coverage tracking for the room scan (F10). Pure logic: no DOM, no sensors.
//
// The user pans the phone; we send a frame when the camera has turned far enough from the last sent
// frame and is steady enough not to blur. Coverage is a ring of 10-degree compass bins filled around
// each sent frame, used for the progress ring and the "turn left to fill the gap" hint.

import { coverage } from "./coverage.js";

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

export const wrap180 = (deg) => ((((deg + 180) % 360) + 360) % 360) - 180;

// Great-circle separation of two (yaw, pitch) directions, degrees.
export function angularDistance(a, b) {
  const v = (p) => [Math.cos(p.pitch * D2R) * Math.sin(p.yaw * D2R), Math.cos(p.pitch * D2R) * Math.cos(p.yaw * D2R), Math.sin(p.pitch * D2R)];
  const [x, y] = [v(a), v(b)];
  const c = x[0] * y[0] + x[1] * y[1] + x[2] * y[2];
  return Math.acos(Math.max(-1, Math.min(1, c))) * R2D;
}

export class KeyframePolicy {
  // stepDeg: turn between frames. maxSpeed: deg/s below which a frame is sharp enough. hardSpeed: above this
  // we only tell the user to slow down, even if overdue. farFactor: how overdue before we accept maxSpeed < speed <= hardSpeed.
  constructor({ stepDeg = 18, maxSpeed = 12, hardSpeed = 30, minIntervalMs = 500, farFactor = 1.6, windowMs = 400 } = {}) {
    Object.assign(this, { stepDeg, maxSpeed, hardSpeed, minIntervalMs, farFactor, windowMs });
    this.reset();
  }

  reset() {
    this.last = null; // last sent pose
    this.lastT = -Infinity;
    this.history = [];
  }

  speed() {
    const h = this.history;
    if (h.length < 2) return null;
    const a = h[0], b = h[h.length - 1];
    const dt = (b.t - a.t) / 1000;
    return dt > 0.05 ? angularDistance(a, b) / dt : null;
  }

  // pose: {yaw, pitch, t (ms)}. Returns {capture, reason, speed, hint}.
  update(pose) {
    this.history.push(pose);
    while (this.history.length > 1 && pose.t - this.history[0].t > this.windowMs) this.history.shift();
    const speed = this.speed();
    const out = { capture: false, reason: null, speed, hint: null };
    if (speed == null) return out; // not enough history yet
    if (speed > this.hardSpeed) out.hint = "slow down";
    const steady = speed <= this.maxSpeed;
    if (this.last === null) {
      if (steady) return this._take(pose, out, "first");
      out.hint = out.hint || "hold still a moment to start";
      return out;
    }
    if (pose.t - this.lastT < this.minIntervalMs) return out;
    const dist = angularDistance(pose, this.last);
    if (dist >= this.stepDeg && steady) return this._take(pose, out, "step");
    if (dist >= this.stepDeg * this.farFactor && speed <= this.hardSpeed) return this._take(pose, out, "overdue");
    if (dist >= this.stepDeg && !steady && !out.hint) out.hint = "slower, then hold steady";
    return out;
  }

  _take(pose, out, reason) {
    this.prev = { last: this.last, lastT: this.lastT };
    this.last = { yaw: pose.yaw, pitch: pose.pitch };
    this.lastT = pose.t;
    out.capture = true;
    out.reason = reason;
    return out;
  }

  // The frame was not actually sent (link busy): let the next sample try again.
  undo() {
    if (this.prev) {
      this.last = this.prev.last;
      this.lastT = this.prev.lastT;
    }
  }
}

// The ring of covered compass directions. What counts as covered is defined once in coverage.js (and, identically,
// in backend/app/coverage.py), so the ring and the server's scan summary cannot disagree.
export class Coverage {
  constructor({ hfov = 60, width = 540, height = 960 } = {}) {
    Object.assign(this, { hfov, width, height });
    this.reset();
  }

  reset() { this.frames = []; this.result = coverage([]); }

  // One frame sent at this pose. hfov/width/height default to the values given at construction.
  mark(yaw, pitch = 0, { hfov = this.hfov, width = this.width, height = this.height } = {}) {
    this.frames.push({ yaw, pitch, hfov_deg: hfov, width, height });
    this.result = coverage(this.frames);
  }

  get bins() { return this.result.bins; }
  get n() { return this.result.bins.length; }
  get binDeg() { return 360 / this.n; }
  get fraction() { return this.result.fraction; }
  get bands() { return this.result.bands; }

  // The nearest uncovered bin from `yaw`: {dir: "left"|"right", deg} or null when everything is covered.
  nextGap(yaw) {
    if (this.fraction >= 1) return null;
    const here = Math.floor((((yaw % 360) + 360) % 360) / this.binDeg) % this.n, bins = this.bins;
    for (let k = 1; k <= this.n / 2; k++) {
      const r = (here + k) % this.n, l = (here - k + this.n) % this.n;
      if (!bins[r]) return { dir: "right", deg: k * this.binDeg };
      if (!bins[l]) return { dir: "left", deg: k * this.binDeg };
    }
    return null;
  }
}
