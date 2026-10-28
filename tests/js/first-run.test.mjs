import test from "node:test";
import assert from "node:assert/strict";
import { classifyCameraError, requestAccess, MOTION_DENIED } from "../../web/js/permissions.js";
import { coachHint, lightVerdict, meanLuma, processing, PROCESS_STALL_MS, PROCESS_LATE_MS } from "../../web/js/coach.js";
import { StatusPoller, describe } from "../../web/js/connection.js";

/* ---------- permissions ---------- */

const err = (name, message = "") => Object.assign(new Error(message), { name });
const stream = () => { const t = { stopped: false, stop() { this.stopped = true; } }; return { t, getTracks: () => [t] }; };

test("a denied camera gets a plain explanation and a retry", () => {
  const p = classifyCameraError(err("NotAllowedError"));
  assert.equal(p.kind, "denied");
  assert.equal(p.retry, true);
  assert.match(p.body, /Try again/);
  assert.match(p.body, /site settings/);
});

test("each way the camera can fail says something different", () => {
  const kinds = Object.fromEntries(["NotAllowedError", "NotFoundError", "NotReadableError", "SecurityError", "SomethingElse"].map((n) => [n, classifyCameraError(err(n)).kind]));
  assert.deepEqual(kinds, { NotAllowedError: "denied", NotFoundError: "nocamera", NotReadableError: "busy", SecurityError: "insecure", SomethingElse: "other" });
  assert.equal(classifyCameraError(err("Error", "Insecure origin: camera needs HTTPS")).retry, false, "retrying cannot fix an insecure page");
  assert.equal(classifyCameraError(err("Error", "getUserMedia unavailable in this browser")).kind, "unsupported");
});

test("granting both: the camera is only probed (tracks stopped again), audio is never asked for", async () => {
  const s = stream();
  const asked = [];
  const nav = { mediaDevices: { getUserMedia: async (c) => { asked.push(c); return s; } } };
  const r = await requestAccess({ nav, win: { DeviceOrientationEvent: function () {} }, secure: true });
  assert.equal(r.ok, true);
  assert.equal(s.t.stopped, true);
  assert.equal(asked.length, 1);
  assert.equal(asked[0].audio, false);
  assert.equal(r.motion.state, "not-needed");
});

test("a denied camera comes back as a problem to show", async () => {
  const nav = { mediaDevices: { getUserMedia: async () => { throw err("NotAllowedError"); } } };
  const r = await requestAccess({ nav, win: { DeviceOrientationEvent: function () {} }, secure: true });
  assert.equal(r.ok, false);
  assert.equal(r.camera.state, "denied");
  assert.equal(r.problem.kind, "denied");
});

test("iOS motion permission is asked first and a refusal is reported", async () => {
  const order = [];
  const D = function () {};
  D.requestPermission = async () => { order.push("motion"); return "denied"; };
  const nav = { mediaDevices: { getUserMedia: async () => { order.push("camera"); return stream(); } } };
  const r = await requestAccess({ nav, win: { DeviceOrientationEvent: D }, secure: true });
  assert.deepEqual(order, ["motion", "camera"], "motion is requested before anything is awaited");
  assert.equal(r.ok, false);
  assert.equal(r.problem, MOTION_DENIED);
});

test("an insecure page or a browser without getUserMedia never prompts", async () => {
  let called = 0;
  const nav = { mediaDevices: { getUserMedia: async () => { called++; return stream(); } } };
  const insecure = await requestAccess({ nav, win: { DeviceOrientationEvent: function () {} }, secure: false });
  assert.equal(insecure.problem.kind, "insecure");
  const none = await requestAccess({ nav: {}, win: { DeviceOrientationEvent: function () {} }, secure: true });
  assert.equal(none.problem.kind, "unsupported");
  assert.equal(called, 0);
  assert.equal((await requestAccess({ nav, win: {}, secure: true })).motion.state, "unsupported");
});

/* ---------- scan coaching ---------- */

const base = { speed: 8, pitch: 0, roll: 0, landscape: false, fraction: 0.3, gap: null, policy: "" };

test("coaching hints, in priority order", () => {
  assert.equal(coachHint({ ...base, speed: 45 }).text, "Rotate slower");
  assert.equal(coachHint({ ...base, policy: "slow down" }).text, "Rotate slower");
  assert.equal(coachHint({ ...base, pitch: -40 }).text, "Tilt up a little");
  assert.equal(coachHint({ ...base, pitch: 50 }).text, "Tilt down a little");
  assert.equal(coachHint({ ...base, roll: 60 }).text, "Hold the phone upright");
  assert.equal(coachHint({ ...base, landscape: true }).text, "Hold the phone upright");
  assert.match(coachHint({ ...base, fraction: 0.7, gap: { dir: "left", deg: 40 } }).text, /Area not covered yet: 40° to the left/);
  assert.equal(coachHint({ ...base, fraction: 0.02 }).text, "Turn slowly to the left");
  assert.equal(coachHint({ ...base, fraction: 0.95 }).text, "Almost there");
  assert.equal(coachHint({ ...base, policy: "orientation sensor stalled" }).text, "Waiting for the motion sensors");
  assert.equal(coachHint({ ...base, policy: "link busy, wait a moment" }).tone, "info");
  assert.equal(coachHint({ ...base, speed: 50, pitch: -40 }).text, "Rotate slower", "speed outranks posture");
});

test("the gap hint only appears once there is something to fill", () => {
  const gap = { dir: "right", deg: 30 };
  assert.doesNotMatch(coachHint({ ...base, fraction: 0.2, gap }).text, /not covered/);
  assert.doesNotMatch(coachHint({ ...base, fraction: 0.98, gap }).text, /not covered/);
});

test("light check: dark rooms are flagged, unknown is not an alarm", () => {
  assert.equal(lightVerdict(20).ok, false);
  assert.match(lightVerdict(20).text, /dark/);
  assert.equal(lightVerdict(120).ok, true);
  assert.equal(lightVerdict(null).ok, null);
  const px = (v, n) => new Uint8ClampedArray(Array.from({ length: n * 4 }, (_, i) => (i % 4 === 3 ? 255 : v)));
  assert.ok(Math.abs(meanLuma(px(100, 64)) - 100) < 0.01);
  assert.equal(meanLuma(new Uint8ClampedArray(0)), null);
});

test("processing progress is real, and a stall or an overlong wait is called out", () => {
  const st = { sent: 20, done: 12, rejected: 2 };
  const p = processing(st, 8000, 1000);
  assert.deepEqual([p.level, p.finished, p.total, p.fraction], ["ok", 14, 20, 0.7]);
  assert.equal(p.text, "Reading frames: 14 of 20");
  assert.equal(processing(st, 20000, 1000).level, "slow");
  assert.equal(processing(st, 40000, PROCESS_STALL_MS + 1).level, "stalled");
  assert.equal(processing(st, PROCESS_LATE_MS + 1, 1000).level, "late");
  assert.equal(processing({ sent: 0, done: 0, rejected: 0 }, 1000, 0).fraction, 0);
  assert.equal(processing({ sent: 3, done: 9, rejected: 0 }, 1000, 0).fraction, 1, "never more than all frames");
});

/* ---------- server connection ---------- */

const okStatus = (api = "ok") => ({ server: true, api: { state: api, message: `api ${api}` }, last_scan: null, scans: 0 });

test("describe: loading, offline, server error and every key state", () => {
  assert.equal(describe(null).state, "loading");
  const off = describe({ ok: false, error: "Failed to fetch" });
  assert.deepEqual([off.state, off.tone, off.can.scan, off.can.find, off.can.changes], ["offline", "bad", false, false, false]);
  assert.match(off.detail, /Reconnecting automatically/);
  const err500 = describe({ ok: false, httpStatus: 500, error: "boom" });
  assert.deepEqual([err500.state, err500.can.scan], ["error", false]);
  assert.match(err500.detail, /500/);
  const ok = describe({ ok: true, status: okStatus("ok") });
  assert.deepEqual([ok.state, ok.tone, ok.can.scan, ok.label], ["ready", "ok", true, "Connected · API key valid"]);
  assert.equal(describe({ ok: true, status: okStatus("set") }).label, "Connected · API key set");
  for (const k of ["missing", "invalid"]) {
    const d = describe({ ok: true, status: okStatus(k) });
    assert.deepEqual([d.tone, d.can.scan, d.can.find, d.can.changes], ["bad", false, false, true]);
  }
  const un = describe({ ok: true, status: okStatus("unreachable") });
  assert.deepEqual([un.tone, un.can.scan], ["warn", true]);
});

// A manual clock: timers run when the test says so.
function clock() {
  const q = [];
  return { setTimer: (f, ms) => { const t = { f, ms, live: true }; q.push(t); return t; }, clearTimer: (t) => { if (t) t.live = false; },
    pending: () => q.filter((t) => t.live), runNext: async () => { const t = q.find((x) => x.live); t.live = false; await t.f(); } };
}

test("the poller backs off while the server is away and recovers on its own", async () => {
  const c = clock();
  let up = false;
  const updates = [];
  const fetchFn = async () => { if (!up) throw new TypeError("Failed to fetch"); return { ok: true, json: async () => okStatus() }; };
  const p = new StatusPoller({ fetchFn, onUpdate: (r, d) => updates.push(d.state), setTimer: c.setTimer, clearTimer: c.clearTimer });
  p.start();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(updates, ["offline"]);
  assert.equal(c.pending()[0].ms, 2000);
  await c.runNext();
  assert.equal(c.pending()[0].ms, 4000);
  await c.runNext(); await c.runNext(); await c.runNext();
  assert.equal(c.pending()[0].ms, 10000, "capped");
  up = true;
  await c.runNext();
  assert.equal(updates.at(-1), "ready");
  assert.equal(c.pending()[0].ms, 15000, "normal interval again");
  p.stop();
  assert.equal(c.pending().length, 0);
});

test("a server error is not 'offline', and retryNow polls immediately", async () => {
  const c = clock();
  const urls = [];
  const fetchFn = async (u) => { urls.push(u); return { ok: false, status: 500, text: async () => "Internal Server Error" }; };
  const states = [];
  const p = new StatusPoller({ fetchFn, onUpdate: (r, d) => states.push([d.state, r.httpStatus]), setTimer: c.setTimer, clearTimer: c.clearTimer });
  p.start();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(states, [["error", 500]]);
  await p.retryNow({ refresh: true });
  assert.equal(urls.at(-1), "/api/status?refresh=true");
  assert.equal(c.pending().length, 1, "only one timer is ever pending");
});

test("a slow answer that arrives after stop() is ignored", async () => {
  const c = clock();
  let release;
  const fetchFn = () => new Promise((res) => { release = () => res({ ok: true, json: async () => okStatus() }); });
  const seen = [];
  const p = new StatusPoller({ fetchFn, onUpdate: () => seen.push(1), setTimer: c.setTimer, clearTimer: c.clearTimer });
  p.start();
  p.stop();
  release();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(seen, []);
  assert.equal(c.pending().length, 0);
});

test("the key still being checked neither blocks the buttons nor sticks: the poller asks again soon", async () => {
  const d = describe({ ok: true, status: okStatus("checking") });
  assert.deepEqual([d.state, d.tone, d.can.scan, d.can.find], ["ready", "wait", true, true]);
  const c = clock();
  const p = new StatusPoller({ fetchFn: async () => ({ ok: true, json: async () => okStatus("checking") }), setTimer: c.setTimer, clearTimer: c.clearTimer });
  p.start();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(c.pending()[0].ms, 2000);
  p.stop();
});

/* ---------- requests with a time limit ---------- */
import { fetchJson } from "../../web/js/connection.js";
import { explainNotFound, ago } from "../../web/js/explain.js";

test("fetchJson folds every outcome into a result and never throws", async () => {
  const res = (status, body) => async () => ({ ok: status < 400, status, text: async () => (typeof body === "string" ? body : JSON.stringify(body)) });
  assert.deepEqual(await fetchJson("/x", { fetchFn: res(200, { a: 1 }) }), { ok: true, status: 200, data: { a: 1 } });
  const bad = await fetchJson("/x", { fetchFn: res(500, { detail: "boom" }) });
  assert.deepEqual([bad.ok, bad.status, bad.error], [false, 500, "boom"]);
  const html = await fetchJson("/x", { fetchFn: res(502, "<html>Bad Gateway</html>") });
  assert.deepEqual([html.ok, html.status, html.data], [false, 502, null]);
  const gone = await fetchJson("/x", { fetchFn: async () => { throw new TypeError("Failed to fetch"); } });
  assert.deepEqual([gone.ok, gone.status, gone.timedOut], [false, 0, false]);
});

test("fetchJson gives up after the time limit", async () => {
  const never = (url, { signal }) => new Promise((_, rej) => signal.addEventListener("abort", () => rej(new Error("aborted"))));
  const r = await fetchJson("/x", { fetchFn: never, timeoutMs: 20 });
  assert.deepEqual([r.ok, r.status, r.timedOut, r.error], [false, 0, true, "no answer in time"]);
});

/* ---------- "not found" ---------- */

test("not found says what was and was not checked", () => {
  const none = explainNotFound({ concept: "keys", found: false, checked: { scans: 0, covered_deg: 0, scan_id: null } });
  assert.equal(none.title, "Nothing has been scanned yet");
  const partial = explainNotFound({ concept: "passport", found: false, checked: { scans: 2, covered_deg: 252, scan_id: 7 } });
  assert.equal(partial.title, "Not found in your last scan");
  assert.match(partial.text, /scan #7, which covered 70% of the room/);
  assert.match(partial.text, /other 30% .* not checked/);
  assert.match(partial.text, /Nothing is checked live/);
  const full = explainNotFound({ concept: "passport", found: false, checked: { scans: 1, covered_deg: 360, scan_id: 1 } });
  assert.match(full.text, /probably moved/);
  assert.equal(explainNotFound({ concept: "", found: false }).title, "I did not catch the name");
  assert.equal(explainNotFound({ concept: "mug", found: false }).title, "Nothing has been scanned yet", "an older server without the checked field");
});

test("ago", () => {
  assert.deepEqual([ago(10), ago(600), ago(7200), ago(400000)], ["just now", "10 min ago", "2 h ago", "5 days ago"]);
});
