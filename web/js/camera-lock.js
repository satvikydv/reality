// The camera must stay in the state the field of view was calibrated for. Resolution, zoom and lens choice change
// the FOV; a scan taken after any of them moves would anchor objects with the wrong angle and look fine doing it.
// So the settings are locked when the camera starts, checked before and after every frame, and any change stops the
// scan with a message saying exactly what changed. Pure logic: the track and video are passed in.

// What the field of view depends on. frameRate, resizeMode and the like do not change it.
export const WATCHED = ["width", "height", "zoom", "facingMode", "deviceId"];

// A fixed request, so calibration and scanning use the same capture mode. The browser may grant something else; what it
// granted is what gets locked and recorded.
export const CAPTURE_REQUEST = { width: { ideal: 1920 }, height: { ideal: 1080 } };

export class CameraSettingsChanged extends Error {
  constructor(diffs) {
    super(`Camera settings changed (${diffs.map((d) => `${d.key} ${d.was} -> ${d.now}`).join(", ")}). The field-of-view calibration no longer applies, so the scan was stopped.`);
    this.name = "CameraSettingsChanged";
    this.diffs = diffs;
  }
}

export const pickSettings = (s = {}) => Object.fromEntries(WATCHED.filter((k) => s[k] !== undefined).map((k) => [k, s[k]]));

const same = (a, b) => (typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-3 : a === b);

// What differs between the locked settings and the current ones: [{key, was, now}]. A watched value that disappears counts.
export function diffSettings(locked, current) {
  const diffs = [];
  for (const k of WATCHED) {
    if (locked[k] === undefined && current[k] === undefined) continue;
    if (!same(locked[k], current[k])) diffs.push({ key: k, was: locked[k] ?? "unset", now: current[k] ?? "unset" });
  }
  return diffs;
}

// What differs between the camera now and the state the FOV was calibrated in (width, height, zoom), as served by
// /api/overlay: [{key, was (calibrated), now}]. No calibration state recorded: nothing to compare.
export function diffCalibration(cal, current) {
  if (!cal) return [];
  return ["width", "height", "zoom"].filter((k) => cal[k] != null && !same(cal[k], current[k])).map((k) => ({ key: k, was: cal[k], now: current[k] ?? "unset" }));
}

export class CameraGuard {
  // track: MediaStreamTrack (getSettings, getCapabilities, applyConstraints); video: the <video> showing it (optional).
  constructor(track, video = null) {
    this.track = track;
    this.video = video;
    this.locked = null;
    this.lockedVideo = null;  // the video element's size when locked: frames are cut from it, and it changes with rotation
    this.zoomLocked = false;
  }

  // Pin zoom to 1x where the camera exposes it, then remember the settings the browser actually granted.
  async lock() {
    try {
      const cap = this.track.getCapabilities?.() || {};
      if (cap.zoom) {
        await this.track.applyConstraints({ advanced: [{ zoom: Math.max(1, cap.zoom.min ?? 1) }] });
        this.zoomLocked = true;
      }
    } catch { /* zoom is not adjustable here: width, height and lens are still guarded */ }
    this.locked = pickSettings(this.track.getSettings());
    if (this.video?.videoWidth) this.lockedVideo = { w: this.video.videoWidth, h: this.video.videoHeight };
    return this.locked;
  }

  current() { return pickSettings(this.track.getSettings()); }

  // [] when the camera is as locked; otherwise what changed (the video element's size is checked against its size at lock time).
  diff() {
    if (!this.locked) return [{ key: "lock", was: "not locked", now: "camera started without a lock" }];
    const d = diffSettings(this.locked, this.current());
    const v = this.video, lv = this.lockedVideo;
    if (v && lv && v.videoWidth && (v.videoWidth !== lv.w || v.videoHeight !== lv.h)) {
      d.push({ key: "video size", was: `${lv.w}x${lv.h}`, now: `${v.videoWidth}x${v.videoHeight}` });
    }
    return d;
  }

  // Throws CameraSettingsChanged when anything the FOV depends on moved.
  assertLocked() {
    const d = this.diff();
    if (d.length) throw new CameraSettingsChanged(d);
  }
}
