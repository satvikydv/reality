import test from "node:test";
import assert from "node:assert/strict";
import { Prefs, memoryStorage, KFPS } from "../../web/js/prefs.js";
import { CARDS, Onboarding, PRIMER, cameraGate, showIntroOnStart, startScreen } from "../../web/js/onboarding.js";

const shared = () => memoryStorage(); // one storage, two "page loads"

test("the intro shows on the first visit only", () => {
  const store = shared();
  assert.equal(showIntroOnStart(new Prefs(store)), true);
  const ob = new Onboarding(new Prefs(store));
  ob.markSeen();
  assert.equal(showIntroOnStart(new Prefs(store)), false, "a later visit does not show it");
  assert.equal(startScreen(), "home", "the page always lands on Home");
});

test("skipping counts as seen, so it does not come back on every visit", () => {
  const store = shared();
  new Onboarding(new Prefs(store)).markSeen(); // skip and finish call the same thing
  assert.equal(new Prefs(store).onboarded, true);
});

test("a replay from Settings changes nothing that is stored", () => {
  const store = shared();
  const p = new Prefs(store);
  const replay = new Onboarding(p, { replay: true });
  replay.markSeen();
  assert.equal(p.onboarded, false, "replaying does not mark the first run as done");
  p.set("onboarded", true);
  new Onboarding(p, { replay: true }).markSeen();
  assert.equal(new Prefs(store).onboarded, true, "and does not undo it");
});

test("three short cards; the last one carries the single permission button", () => {
  assert.equal(CARDS.length, 3);
  assert.deepEqual(CARDS.map((c) => c.id), ["what", "scan", "privacy"]);
  for (const c of CARDS) assert.ok(c.body.join(" ").length < 330, `${c.id} stays short`);
  const ob = new Onboarding(new Prefs(shared()));
  assert.deepEqual([ob.nextLabel, ob.last], ["Next", false]);
  ob.next(); ob.next();
  assert.deepEqual([ob.nextLabel, ob.last, ob.position], ["Allow camera and motion", true, "3 / 3"]);
  ob.next();
  assert.equal(ob.index, 2, "cannot run past the end");
  ob.back();
  assert.equal(ob.index, 1);
  assert.equal(new Onboarding(new Prefs(shared()), { replay: true }).next().id, "scan");
  const replay = new Onboarding(new Prefs(shared()), { replay: true });
  replay.next(); replay.next();
  assert.equal(replay.nextLabel, "Done", "a replay has nothing to allow");
});

test("the cards say what the product does, how to scan and what happens to the data", () => {
  const text = (id) => CARDS.find((c) => c.id === id).body.join(" ").toLowerCase();
  assert.match(text("scan"), /still/);
  assert.match(text("scan"), /slowly/);
  assert.match(text("scan"), /people out of frame/);
  assert.match(text("privacy"), /sent to meta.s model api/);
  assert.match(text("privacy"), /stored on the computer/);
  assert.match(text("privacy"), /delete any scan/);
});

test("the primer names both permissions and the microphone, before any browser prompt", () => {
  assert.deepEqual(PRIMER.items.map((i) => i.id), ["camera", "motion"]);
  assert.match(PRIMER.note, /microphone/i);
});

test("the camera gate sends a first-time user to the primer, and everyone after to the camera", () => {
  const store = shared();
  const p = new Prefs(store);
  assert.equal(cameraGate(p), "primer");
  p.set("primed", true);
  assert.equal(cameraGate(new Prefs(store)), "start");
});

test("settings persist and fall back to defaults", () => {
  const store = shared();
  const p = new Prefs(store);
  assert.deepEqual([p.get("kfps"), p.get("lang"), p.get("ar"), p.minIntervalMs()], [2, "", true, 500]);
  p.set("kfps", 3); p.set("lang", "hi-IN"); p.set("ar", false);
  const q = new Prefs(store);
  assert.deepEqual([q.get("kfps"), q.get("lang"), q.get("ar"), q.minIntervalMs()], [3, "hi-IN", false, 333]);
  assert.equal(q.voiceLang("en-GB"), "hi-IN");
  assert.equal(new Prefs(shared()).voiceLang("en-GB"), "en-GB", "the phone's language when none is chosen");
  assert.equal(new Prefs(shared()).voiceLang(""), "en-US");
  store.setItem("ros.kfps", "9");
  assert.equal(new Prefs(store).get("kfps"), 2, "an unknown rate is ignored");
  assert.deepEqual(KFPS, [1, 2, 3]);
});

test("the old AR switch value keeps working", () => {
  const store = shared();
  store.setItem("ros.ar", "0");
  assert.equal(new Prefs(store).get("ar"), false);
});

test("blocked or throwing storage: values still hold for the session and nothing throws", () => {
  const broken = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("QuotaExceededError"); }, removeItem() {} };
  const p = new Prefs(broken);
  assert.equal(p.onboarded, false);
  new Onboarding(p).markSeen();
  assert.equal(p.onboarded, true, "the intro does not come back on every screen change");
  p.set("primed", true);
  assert.equal(cameraGate(p), "start");
});
