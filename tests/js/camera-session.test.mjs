import test from "node:test";
import assert from "node:assert/strict";
import { CameraSession, RELEASED } from "../../web/js/camera-session.js";

// A MediaStream stand-in that counts how many of its tracks were stopped.
function fakeStream() {
  const listeners = {};
  const track = { stopped: false, stops: 0, stop() { this.stopped = true; this.stops++; }, addEventListener(t, f) { listeners[t] = f; }, end() { listeners.ended?.(); } };
  return { track, getTracks: () => [track], getVideoTracks: () => [track] };
}

function rig({ wakeLock = true, lock = true, startCamera, orientStart } = {}) {
  const doc = Object.assign(new EventTarget(), { hidden: false });
  const win = new EventTarget();
  const video = { srcObject: null };
  const streams = [];
  const sentinels = [];
  const calls = { orientStart: 0, orientStop: 0, lock: [], unlock: 0 };
  const orient = { start: orientStart || (async () => { calls.orientStart++; }), stop: () => { calls.orientStop++; } };
  const nav = wakeLock ? { wakeLock: { request: async () => { const s = Object.assign(new EventTarget(), { released: false, release() { this.released = true; } }); sentinels.push(s); return s; } } } : {};
  const scr = lock ? { orientation: { lock: async (m) => { calls.lock.push(m); }, unlock: () => { calls.unlock++; } } } : {};
  const events = { hidden: 0, ended: 0, states: [] };
  const s = new CameraSession({
    video, orient, doc, win, nav, scr,
    startCamera: startCamera || (async (v) => { const stream = fakeStream(); streams.push(stream); v.srcObject = stream; return { stream, guard: { id: streams.length } }; }),
    onState: (st) => events.states.push(st.camera), onHidden: () => events.hidden++, onEnded: () => events.ended++,
  });
  return { s, doc, win, video, streams, sentinels, calls, events };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

test("nothing runs until acquire: constructing a session touches no camera, sensor, wake lock or orientation", async () => {
  const r = rig();
  await flush();
  assert.equal(r.streams.length, 0);
  assert.equal(r.calls.orientStart, 0);
  assert.equal(r.sentinels.length, 0);
  assert.deepEqual(r.calls.lock, []);
  assert.equal(r.s.state.camera, "off");
  assert.equal(r.s.active, false);
});

test("acquire starts the camera and sensors, keeps the screen awake and asks for portrait", async () => {
  const r = rig();
  const res = await r.s.acquire();
  await flush();
  assert.equal(r.s.active, true);
  assert.equal(res.guard.id, 1);
  assert.equal(r.calls.orientStart, 1);
  assert.equal(r.sentinels.length, 1);
  assert.deepEqual(r.calls.lock, ["portrait"]);
  assert.deepEqual({ wake: r.s.state.wake, portrait: r.s.state.portrait }, { wake: true, portrait: true });
  assert.deepEqual(r.events.states.slice(0, 2), ["starting", "on"]);
});

test("acquiring twice shares one camera", async () => {
  const r = rig();
  await Promise.all([r.s.acquire(), r.s.acquire()]);
  await r.s.acquire();
  assert.equal(r.streams.length, 1);
});

test("release stops every track and sensor, drops the wake lock and unlocks the orientation", async () => {
  const r = rig();
  await r.s.acquire();
  await flush();
  r.s.release("left the screen");
  assert.equal(r.streams[0].track.stopped, true);
  assert.equal(r.streams[0].track.stops, 1, "each track is stopped exactly once");
  assert.equal(r.video.srcObject, null);
  assert.equal(r.calls.orientStop, 1);
  assert.equal(r.sentinels[0].released, true);
  assert.equal(r.calls.unlock, 1);
  assert.deepEqual({ camera: r.s.state.camera, wake: r.s.state.wake, portrait: r.s.state.portrait, reason: r.s.state.reason }, { camera: "off", wake: false, portrait: false, reason: "left the screen" });
  r.s.release(); // twice is harmless
});

test("hiding the tab stops the camera at once and tells the page; showing it again does not restart it", async () => {
  const r = rig();
  await r.s.acquire();
  r.doc.hidden = true;
  r.doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(r.streams[0].track.stopped, true);
  assert.equal(r.s.active, false);
  assert.equal(r.events.hidden, 1);
  r.doc.hidden = false;
  r.doc.dispatchEvent(new Event("visibilitychange"));
  await flush();
  assert.equal(r.streams.length, 1, "no new getUserMedia without a tap");
  assert.equal(r.s.active, false);
});

test("a visibility change while the camera is off does nothing", () => {
  const r = rig();
  r.doc.hidden = true;
  r.doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(r.events.hidden, 0);
  assert.deepEqual(r.events.states, []);
});

test("pagehide (closing the tab, bfcache) also stops the camera", async () => {
  const r = rig();
  await r.s.acquire();
  r.win.dispatchEvent(new Event("pagehide"));
  assert.equal(r.streams[0].track.stopped, true);
  assert.equal(r.s.active, false);
});

test("the browser or another app ending the track releases everything and tells the page", async () => {
  const r = rig();
  await r.s.acquire();
  r.streams[0].track.end();
  assert.equal(r.s.active, false);
  assert.equal(r.s.state.reason, "ended");
  assert.equal(r.events.ended, 1);
  assert.equal(r.calls.orientStop, 1);
});

test("releasing while the camera is still starting stops the stream that arrives late", async () => {
  let resolveStart;
  const streams = [];
  const r = rig({ startCamera: (v) => new Promise((res) => { resolveStart = () => { const stream = fakeStream(); streams.push(stream); v.srcObject = stream; res({ stream, guard: {} }); }; }) });
  const p = r.s.acquire();
  const settled = p.then(() => "resolved", (e) => e.name);
  r.s.release("left");
  resolveStart();
  assert.equal(await settled, RELEASED);
  assert.equal(streams[0].track.stopped, true);
  assert.equal(r.s.active, false);
  assert.equal(r.video.srcObject, null);
  assert.equal(r.sentinels.length, 0, "no wake lock for a camera that was cancelled");
});

test("a refused camera leaves nothing running and rethrows the browser's error", async () => {
  const err = Object.assign(new Error("denied"), { name: "NotAllowedError" });
  const r = rig({ startCamera: async () => { throw err; } });
  await assert.rejects(r.s.acquire(), (e) => e === err);
  assert.equal(r.s.active, false);
  assert.equal(r.s.state.camera, "off");
  assert.equal(r.calls.orientStop, 1, "sensors started in the tap are stopped again");
  assert.equal(r.sentinels.length, 0);
  await r.s.acquire().catch(() => {}); // can be asked again
  assert.equal(r.s.state.camera, "off");
});

test("a play() that fails after the stream was attached still stops its tracks", async () => {
  const stream = fakeStream();
  const r = rig({ startCamera: async (v) => { v.srcObject = stream; throw Object.assign(new Error("play"), { name: "NotAllowedError" }); } });
  await assert.rejects(r.s.acquire());
  assert.equal(stream.track.stopped, true);
  assert.equal(r.video.srcObject, null);
});

test("missing wake lock and orientation lock are fine", async () => {
  const r = rig({ wakeLock: false, lock: false });
  await r.s.acquire();
  await flush();
  assert.equal(r.s.active, true);
  assert.deepEqual({ wake: r.s.state.wake, portrait: r.s.state.portrait }, { wake: false, portrait: false });
  r.s.release();
});

test("a refused wake lock or orientation lock does not stop the camera", async () => {
  const r = rig();
  r.s.nav = { wakeLock: { request: async () => { throw new Error("NotAllowedError"); } } };
  r.s.scr = { orientation: { lock: async () => { throw new Error("NotSupportedError"); } } };
  await r.s.acquire();
  await flush();
  assert.equal(r.s.active, true);
  assert.equal(r.s.state.wake, false);
  assert.equal(r.s.state.portrait, false);
});

test("a wake lock the system drops (screen turned off) is reflected in the state", async () => {
  const r = rig();
  await r.s.acquire();
  await flush();
  r.sentinels[0].dispatchEvent(new Event("release"));
  assert.equal(r.s.state.wake, false);
  assert.equal(r.s.active, true);
});

test("dispose removes the listeners", async () => {
  const r = rig();
  await r.s.acquire();
  r.s.dispose();
  r.doc.hidden = true;
  r.doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(r.events.hidden, 0);
});
