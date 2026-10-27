// Device orientation -> camera pointing direction (yaw, pitch, roll), from two sources:
//   absolute: AbsoluteOrientationSensor, else `deviceorientationabsolute` events (magnetometer, north-referenced)
//   relative: RelativeOrientationSensor, else `deviceorientation` events (gyro only, arbitrary zero per session)
//
// Conventions (degrees). Earth frame is east-north-up. The rear camera looks along device -Z.
//   yaw   = compass azimuth of the camera direction, clockwise from north, [0, 360)
//   pitch = camera elevation, +90 = straight up, -90 = straight down
//   roll  = rotation about the camera axis, positive = clockwise as seen from behind the phone;
//           null when the camera points (almost) straight up or down
// Screen rotation does not matter: device axes are fixed to the phone body.

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

// W3C DeviceOrientation: R = Rz(alpha) * Rx(beta) * Ry(gamma), device -> earth. Row-major 3x3.
export function rotationFromEuler(alphaDeg, betaDeg, gammaDeg) {
  const a = alphaDeg * D2R, b = betaDeg * D2R, g = gammaDeg * D2R;
  const ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(b), sb = Math.sin(b), cg = Math.cos(g), sg = Math.sin(g);
  return [
    ca * cg - sa * sb * sg, -sa * cb, ca * sg + sa * sb * cg,
    sa * cg + ca * sb * sg, ca * cb, sa * sg - ca * sb * cg,
    -cb * sg, sb, cb * cg,
  ];
}

// Generic Sensor quaternion [x, y, z, w], device -> earth.
export function rotationFromQuaternion([x, y, z, w]) {
  return [
    1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
    2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
    2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
  ];
}

const mulv = (R, v) => [R[0] * v[0] + R[1] * v[1] + R[2] * v[2], R[3] * v[0] + R[4] * v[1] + R[5] * v[2], R[6] * v[0] + R[7] * v[1] + R[8] * v[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => Math.hypot(a[0], a[1], a[2]);

export function cameraPose(R) {
  const d = mulv(R, [0, 0, -1]); // camera direction in earth frame
  const yaw = (Math.atan2(d[0], d[1]) * R2D + 360) % 360;
  const pitch = Math.asin(Math.max(-1, Math.min(1, d[2]))) * R2D;
  let roll = null;
  const right = cross(d, [0, 0, 1]);
  if (norm(right) > 1e-3) {
    const r = right.map((v) => v / norm(right));
    const up = cross(r, d);
    const u = mulv(R, [0, 1, 0]); // device top edge
    roll = Math.atan2(dot(u, r), dot(u, up)) * R2D;
  }
  return { yaw, pitch, roll };
}

export const wrap180 = (deg) => ((((deg + 180) % 360) + 360) % 360) - 180;

// Mean of a list of poses (circular mean for yaw/roll).
export function meanPose(samples) {
  if (!samples.length) return null;
  const circ = (key) => {
    const v = samples.map((s) => s[key]).filter((x) => x != null);
    if (!v.length) return null;
    const sx = v.reduce((a, x) => a + Math.cos(x * D2R), 0), sy = v.reduce((a, x) => a + Math.sin(x * D2R), 0);
    return (Math.atan2(sy, sx) * R2D + 360) % 360;
  };
  return {
    yaw: circ("yaw"),
    pitch: samples.reduce((a, s) => a + s.pitch, 0) / samples.length,
    roll: (() => { const r = circ("roll"); return r == null ? null : wrap180(r); })(),
    n: samples.length,
  };
}

const nowMs = () => performance.timeOrigin + performance.now();

export class OrientationTracker {
  constructor() {
    this.latest = { absolute: null, relative: null };
    this.status = { absolute: "idle", relative: "idle" };
    this.counts = { absolute: 0, relative: 0 };
    this.history = { absolute: [], relative: [] }; // last ~2 s
    this.listeners = new Set();
    this.started = false;
    this.cleanup = { absolute: null, relative: null };
  }

  onSample(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  async start() {
    if (this.started) return;
    this.started = true;
    try { // iOS-style gate; Chrome on Android may also expose it
      if (typeof DeviceOrientationEvent !== "undefined" && typeof DeviceOrientationEvent.requestPermission === "function")
        await DeviceOrientationEvent.requestPermission();
    } catch { /* fall through; events may still work */ }
    this._startSource("absolute", "AbsoluteOrientationSensor", "deviceorientationabsolute");
    this._startSource("relative", "RelativeOrientationSensor", "deviceorientation");
  }

  stop() { for (const k of Object.keys(this.cleanup)) { this.cleanup[k]?.(); this.cleanup[k] = null; } this.started = false; }

  _push(source, pose, api, raw) {
    const s = { ...pose, t: nowMs(), api, raw };
    this.latest[source] = s;
    this.counts[source]++;
    const h = this.history[source];
    h.push(s);
    while (h.length && s.t - h[0].t > 2000) h.shift();
    for (const fn of this.listeners) fn(source, s);
  }

  _startSource(source, sensorName, eventName) {
    const viaEvents = () => {
      const handler = (e) => {
        if (e.alpha == null || e.beta == null || e.gamma == null) return;
        this._push(source, cameraPose(rotationFromEuler(e.alpha, e.beta, e.gamma)),
          `event:${eventName}${e.absolute ? "(absolute=true)" : ""}`, { alpha: e.alpha, beta: e.beta, gamma: e.gamma });
      };
      addEventListener(eventName, handler);
      this.cleanup[source] = () => removeEventListener(eventName, handler);
      this.status[source] = `event:${eventName}`;
    };
    const Sensor = globalThis[sensorName];
    if (!Sensor) return viaEvents();
    try {
      const sensor = new Sensor({ frequency: 30, referenceFrame: "device" });
      sensor.addEventListener("reading", () => {
        if (!sensor.quaternion) return;
        this._push(source, cameraPose(rotationFromQuaternion(sensor.quaternion)), `sensor:${sensorName}`, { quaternion: [...sensor.quaternion] });
      });
      sensor.addEventListener("error", (e) => {
        this.status[source] = `sensor error (${e.error?.name}), falling back to events`;
        try { sensor.stop(); } catch { /* ignore */ }
        viaEvents();
      });
      sensor.start();
      this.cleanup[source] = () => { try { sensor.stop(); } catch { /* ignore */ } };
      this.status[source] = `sensor:${sensorName}`;
    } catch (e) {
      this.status[source] = `sensor failed (${e.name}), using events`;
      viaEvents();
    }
  }

  // Mean pose over the last `windowMs` of samples for one source.
  averaged(source, windowMs = 500) {
    const h = this.history[source];
    const t = nowMs();
    return meanPose(h.filter((s) => t - s.t <= windowMs));
  }

  // JSON-safe snapshot for storing next to a frame.
  snapshot() {
    const t = nowMs();
    const one = (s) => s && { yaw: s.yaw, pitch: s.pitch, roll: s.roll, t: s.t, age_ms: Math.round(t - s.t), api: s.api, raw: s.raw };
    return { captured_at: t, absolute: one(this.latest.absolute), relative: one(this.latest.relative), status: { ...this.status } };
  }
}
