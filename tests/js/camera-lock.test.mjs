import test from "node:test";
import assert from "node:assert/strict";
import { CameraGuard, CameraSettingsChanged, diffCalibration, diffSettings, pickSettings, WATCHED } from "../../web/js/camera-lock.js";

// A MediaStreamTrack stand-in whose settings the test can change, like the browser does when zoom or resolution moves.
function fakeTrack(settings, { zoom = null, refuseZoom = false } = {}) {
  const t = {
    settings: { ...settings },
    applied: [],
    getSettings() { return { ...this.settings, frameRate: 30 }; },
    getCapabilities() { return zoom ? { zoom } : {}; },
    async applyConstraints(c) {
      if (refuseZoom) throw new Error("OverconstrainedError");
      this.applied.push(c);
      const z = c.advanced?.[0]?.zoom;
      if (z !== undefined) this.settings.zoom = z;
    },
  };
  return t;
}
const GRANTED = { width: 1080, height: 1920, facingMode: "environment", deviceId: "cam0", zoom: 1 };

test("only what the field of view depends on is watched", () => {
  assert.deepEqual(pickSettings({ ...GRANTED, frameRate: 30, resizeMode: "none" }), GRANTED);
  assert.deepEqual(WATCHED, ["width", "height", "zoom", "facingMode", "deviceId"]);
});

test("locking pins zoom to 1x and remembers what was granted", async () => {
  const t = fakeTrack({ ...GRANTED, zoom: 2.4 }, { zoom: { min: 1, max: 8 } });
  const g = new CameraGuard(t);
  const locked = await g.lock();
  assert.equal(locked.zoom, 1);
  assert.deepEqual(t.applied, [{ advanced: [{ zoom: 1 }] }]);
  assert.ok(g.zoomLocked);
  assert.deepEqual(g.diff(), []);
  g.assertLocked(); // does not throw
});

test("a camera that cannot zoom is still guarded on resolution and lens", async () => {
  const noZoom = { width: 1080, height: 1920, facingMode: "environment", deviceId: "cam0" };
  const g = new CameraGuard(fakeTrack(noZoom));
  await g.lock();
  assert.ok(!g.zoomLocked);
  const refusing = new CameraGuard(fakeTrack(GRANTED, { zoom: { min: 1, max: 4 }, refuseZoom: true }));
  await refusing.lock();
  assert.ok(!refusing.zoomLocked && refusing.locked.width === 1080);
});

test("a zoom change is reported with what it was and what it is now", async () => {
  const t = fakeTrack(GRANTED, { zoom: { min: 1, max: 8 } });
  const g = new CameraGuard(t);
  await g.lock();
  t.settings.zoom = 2;
  assert.deepEqual(g.diff(), [{ key: "zoom", was: 1, now: 2 }]);
  assert.throws(() => g.assertLocked(), (e) => e instanceof CameraSettingsChanged && /zoom 1 -> 2/.test(e.message) && /calibration no longer applies/.test(e.message));
});

test("a resolution change, a lens switch and several changes at once are all reported", async () => {
  const t = fakeTrack(GRANTED);
  const g = new CameraGuard(t);
  await g.lock();
  t.settings.width = 720; t.settings.height = 1280;
  assert.deepEqual(g.diff().map((d) => d.key), ["width", "height"]);
  t.settings.deviceId = "ultrawide";
  assert.deepEqual(g.diff().map((d) => d.key), ["width", "height", "deviceId"]);
  assert.match(new CameraSettingsChanged(g.diff()).message, /width 1080 -> 720, height 1920 -> 1280, deviceId cam0 -> ultrawide/);
});

test("tiny zoom jitter is not a change; a real one is", () => {
  assert.deepEqual(diffSettings({ zoom: 1 }, { zoom: 1.0004 }), []);
  assert.equal(diffSettings({ zoom: 1 }, { zoom: 1.01 }).length, 1);
});

test("a setting that disappears counts as a change", () => {
  assert.deepEqual(diffSettings({ zoom: 1, width: 1080 }, { width: 1080 }), [{ key: "zoom", was: 1, now: "unset" }]);
});

test("rotating the phone changes the video size even if the track settings do not", async () => {
  const video = { videoWidth: 1080, videoHeight: 1920 };
  const g = new CameraGuard(fakeTrack(GRANTED), video);
  await g.lock();
  assert.deepEqual(g.diff(), []);
  video.videoWidth = 1920; video.videoHeight = 1080;
  assert.deepEqual(g.diff(), [{ key: "video size", was: "1080x1920", now: "1920x1080" }]);
});

test("a guard that was never locked refuses to vouch for the camera", () => {
  const g = new CameraGuard(fakeTrack(GRANTED));
  assert.throws(() => g.assertLocked(), CameraSettingsChanged);
});

test("the camera is compared with the state the FOV was calibrated in", () => {
  const cal = { width: 1080, height: 1920, zoom: 1 };
  assert.deepEqual(diffCalibration(cal, { width: 1080, height: 1920, zoom: 1 }), []);
  assert.deepEqual(diffCalibration(cal, { width: 720, height: 1280, zoom: 1 }).map((d) => d.key), ["width", "height"]);
  assert.deepEqual(diffCalibration(cal, { width: 1080, height: 1920 }), [{ key: "zoom", was: 1, now: "unset" }]);
  assert.deepEqual(diffCalibration(null, { width: 1 }), [], "no calibration recorded: nothing to compare");
  assert.deepEqual(diffCalibration({ width: null, height: null, zoom: null }, { width: 1 }), []);
});
