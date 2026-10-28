import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { SCREENS, NAV, ids, applyState, isCamera, navTab, hasRoomSwitcher, hasState } from "../../web/js/screens.js";
import { FIXTURES, fixture } from "../../web/js/fixtures.js";
import { createStore, INITIAL, freeNameChips, roomById, activeRoom, roomLabel, NAME_CHIPS } from "../../web/js/store.js";
import { makeApi, describeFailure } from "../../web/js/api.js";
import { groupObjects, summaryTiles } from "../../web/js/views.js";

const docs = readFileSync(new URL("../../docs/screens.md", import.meta.url), "utf8");
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

/* ---------- the registry ---------- */

test("the registry lists the screens of the room flow, each with states, data and api", () => {
  for (const id of ["home", "wizard", "scan", "summary", "room", "find", "result", "guide", "changes", "settings", "roomsettings", "explore", "switcher", "welcome", "primer"])
    assert.ok(SCREENS[id], `missing screen ${id}`);
  for (const [id, s] of Object.entries(SCREENS)) {
    assert.ok(s.states.length >= 1 && new Set(s.states).size === s.states.length, `${id}: states`);
    assert.ok(s.title && Array.isArray(s.data) && Array.isArray(s.api), `${id}: title, data, api`);
  }
});

test("only scan, guide and explore use the camera, and the bottom bar has the five tabs", () => {
  assert.deepEqual(ids().filter(isCamera).sort(), ["explore", "guide", "scan"]);
  assert.deepEqual(NAV.map(([id]) => id), ["home", "find", "scan", "changes", "settings"]);
  for (const id of ids()) assert.ok(navTab(id) === null || NAV.some(([t]) => t === navTab(id)), `${id}: tab`);
  assert.equal(navTab("room"), "home");
  assert.equal(navTab("scan"), null, "no bar over the camera");
  assert.ok(hasRoomSwitcher("home") && hasRoomSwitcher("find") && hasRoomSwitcher("changes") && !hasRoomSwitcher("scan"));
  assert.ok(hasState("find", "all-results") && !hasState("find", "nope"));
});

test("every state of every screen has a demo fixture", () => {
  for (const [id, s] of Object.entries(SCREENS)) {
    for (const st of s.states) assert.equal(typeof fixture(id, st), "object", `no fixture for ${id}:${st}`);
    assert.deepEqual(Object.keys(FIXTURES[id]).sort(), [...s.states].sort(), `${id}: fixtures and registry disagree`);
  }
});

test("the markup has a section per screen and uses only registered states", () => {
  for (const [id, s] of Object.entries(SCREENS)) {
    assert.ok(html.includes(`id="s-${id}"`), `no section s-${id}`);
    const from = html.indexOf(`id="s-${id}"`), sect = html.slice(from, html.indexOf("</section>", from));
    for (const m of sect.matchAll(/data-show="([^"]+)"/g)) for (const st of m[1].split(/\s+/)) assert.ok(s.states.includes(st), `${id}: markup state ${st} is not in the registry`);
  }
});

test("docs/screens.md describes every screen and every state, with its data and api", () => {
  for (const [id, s] of Object.entries(SCREENS)) {
    const at = docs.indexOf(`### \`${id}\``);
    assert.ok(at >= 0, `docs/screens.md has no section for ${id}`);
    const next = docs.indexOf("\n### ", at + 5), sect = docs.slice(at, next < 0 ? undefined : next);
    for (const st of s.states) assert.ok(sect.includes(`\`${st}\``), `${id}: state ${st} is not described in docs/screens.md`);
    for (const a of s.api) assert.ok(sect.includes(a), `${id}: api ${a} is not in docs/screens.md`);
  }
});

test("applyState shows only the parts that belong to the state", () => {
  const mk = (show) => ({ dataset: { show }, hidden: false });
  const parts = [mk("default empty"), mk("loading"), mk("error offline slow")];
  const section = { dataset: {}, querySelectorAll: () => parts };
  applyState(section, "loading");
  assert.deepEqual(parts.map((p) => p.hidden), [true, false, true]);
  assert.equal(section.dataset.state, "loading");
  applyState(section, "offline");
  assert.deepEqual(parts.map((p) => p.hidden), [true, true, false]);
});

/* ---------- views: what the review shows ---------- */

const obj = (id, label) => ({ id, label, yaw: id * 10, pitch: 0 });

test("objects group by kind, biggest group first", () => {
  const g = groupObjects([obj(1, "cable"), obj(2, "laptop"), obj(3, "cable"), obj(4, "cable"), obj(5, "laptop"), obj(6, "mug")]);
  assert.deepEqual(g.map((x) => [x.label, x.count]), [["cable", 3], ["laptop", 2], ["mug", 1]]);
});

test("up to six kinds get a tile each; more collapse into a tappable Others tile that holds the rest", () => {
  const few = summaryTiles(["a", "b", "c"].map((l, i) => obj(i, l)));
  assert.equal(few.tiles.length, 3);
  assert.equal(few.others, null);
  const kinds = "abcdefgh".split("");
  const many = summaryTiles(kinds.flatMap((l, i) => Array.from({ length: 8 - i }, (_, j) => obj(i * 10 + j, l)))); // a:8 ... h:1
  assert.deepEqual(many.tiles.map((t) => t.label), ["a", "b", "c", "d", "e"]);
  assert.equal(many.others.label, "Others");
  assert.deepEqual(many.others.groups.map((g) => g.label), ["f", "g", "h"]);
  assert.equal(many.others.count, 3 + 2 + 1);
  assert.equal(many.others.objects.length, 6);
  const total = many.tiles.reduce((n, t) => n + t.count, 0) + many.others.count;
  assert.equal(total, 36, "nothing is lost between the tiles and Others");
});

/* ---------- the data layer ---------- */

test("the api client builds room-scoped urls and bodies", async () => {
  const calls = [];
  const api = makeApi(async (url, o) => { calls.push([url, o.method || "GET", o.body]); return { ok: true, status: 200, data: {} }; });
  await api.rooms();
  await api.createRoom("Living Room", true);
  await api.patchRoom(3, { active: true });
  await api.changes(3, undefined, 9);
  await api.changes(3);
  await api.search("water bottle", 2);
  await api.search("", null);
  await api.saveScan(1, 7);
  await api.discardScan(1, 7);
  await api.deleteScan(1, 7);
  await api.setBaseline(1, null);
  await api.review(1, 2, 3, "moved:1:2", "confirmed");
  await api.guide(5, 1);
  await api.deleteRoom(4);
  await api.deleteAll();
  await api.overlay(2, 8);
  await api.scanObjects(4);
  await api.duplicates(4);
  await api.object(9);
  await api.mergeObjects([1, 2]);
  await api.detachSighting(7);
  await api.deleteObject(3);
  await api.markNotObject(1, "wall");
  await api.unmarkNotObject(1, "ceiling light");
  assert.deepEqual(calls.slice(16).map((c) => c.slice(0, 2).join(" ")), [
    "/api/scans/4/objects GET", "/api/scans/4/duplicates GET", "/api/objects/9 GET", "/api/objects/merge POST", "/api/sightings/7/detach?source=phone POST",
    "/api/objects/3?source=phone DELETE", "/api/rooms/1/not-objects POST", "/api/rooms/1/not-objects/ceiling%20light?source=phone DELETE"]);
  assert.deepEqual(calls[19][2], { ids: [1, 2], source: "phone" }, "corrections say who made them");
  assert.deepEqual(calls[22][2], { label: "wall", source: "phone" });
  assert.deepEqual(calls.slice(0, 16).map((c) => c.slice(0, 2).join(" ")), [
    "/api/rooms GET", "/api/rooms POST", "/api/rooms/3 PATCH", "/api/rooms/3/changes?after=9 GET", "/api/rooms/3/changes GET",
    "/api/search?q=water%20bottle&room_id=2 GET", "/api/search GET", "/api/rooms/1/scans/7/save POST", "/api/rooms/1/scans/7/discard POST",
    "/api/rooms/1/scans/7 DELETE", "/api/rooms/1/baseline POST", "/api/rooms/1/changes/review POST", "/api/guide POST", "/api/rooms/4 DELETE", "/api/data DELETE",
    "/api/overlay?room_id=2&scan_id=8 GET"]);
  assert.deepEqual(calls[1][2], { name: "Living Room", active: true });
  assert.deepEqual(calls[10][2], { scan_id: null });
  assert.deepEqual(calls[11][2], { before: 2, after: 3, key: "moved:1:2", state: "confirmed" });
  assert.deepEqual(calls[12][2], { object_id: 5, room_id: 1 });
});

test("a failed call becomes an offline, slow or error state with words", () => {
  assert.equal(describeFailure({ ok: false, status: 0, timedOut: true }).kind, "slow");
  assert.equal(describeFailure({ ok: false, status: 0 }).kind, "offline");
  const e = describeFailure({ ok: false, status: 409, data: { detail: "there is already a room called Desk" } });
  assert.deepEqual([e.kind, e.text], ["error", "there is already a room called Desk"]);
  assert.match(describeFailure({ ok: false, status: 500 }).text, /500/);
});

test("the store tells subscribers what changed, and only when something did", () => {
  const s = createStore(INITIAL);
  const seen = [];
  const off = s.on((st, changed) => seen.push(changed));
  s.set({ activeId: 2 });
  s.set({ activeId: 2 }); // no change: no call
  s.set({ activeId: 3, scope: "all" });
  off();
  s.set({ activeId: 4 });
  assert.deepEqual(seen, [["activeId"], ["activeId", "scope"]]);
  assert.equal(s.get().activeId, 4);
});

test("room helpers and name suggestions", () => {
  const st = { ...INITIAL, rooms: [{ id: 1, name: "Desk", active: true }, { id: 2, name: "living room", active: false }], activeId: 1 };
  assert.equal(activeRoom(st).name, "Desk");
  assert.equal(roomById(st, 2).name, "living room");
  assert.equal(roomLabel(st), "Desk");
  assert.equal(roomLabel({ ...st, activeId: null }), "No room");
  const free = freeNameChips(st);
  assert.ok(!free.includes("Desk") && !free.includes("Living Room"), "names in use (any case) are not suggested");
  assert.equal(free.length, NAME_CHIPS.length - 2);
  for (const n of ["Desk", "Shelf", "Wardrobe"]) assert.ok(NAME_CHIPS.includes(n));
});
