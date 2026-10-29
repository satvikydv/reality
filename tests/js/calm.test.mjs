import test from "node:test";
import assert from "node:assert/strict";
import { OVERLAY, MODES, DEFAULT_MODE, overlayConfig, showsHud } from "../../web/js/overlay-config.js";
import { selectLabels, labelScore, hiddenReason, placeLabel, plural } from "../../web/js/calm.js";
import { simplifyPolygon, smoothPolygon, polygonArea, prepareOutline, cachedOutline } from "../../web/js/simplify.js";

const VIEW = { w: 390, h: 844, top: 120, bot: 200 };
const CALM = OVERLAY.calm;
const obj = (id, label, over = {}) => ({ id, label, confidence: 0.9, uncertainty_deg: 2, age_s: 10, ...over });
const item = (id, label, x, y, over = {}) => ({ o: obj(id, label, over), at: [x, y] });
const labelBoxes = (r) => r.labels.map((l) => ({ x: l.x, y: l.y, w: l.w, h: l.h }));
const overlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/* ---------- the config ---------- */

test("Calm is the default and every threshold lives in the config", () => {
  assert.equal(DEFAULT_MODE, "calm");
  assert.deepEqual(MODES, ["calm", "normal", "debug"]);
  assert.ok(CALM.maxLabels <= 5);
  for (const k of ["minConfidence", "maxUncertaintyDeg", "backgroundLabels", "centralityFloor", "recencyHalfLifeS", "groupRadiusPx", "chip", "reticleRadiusPx", "outline", "hud"]) assert.ok(k in CALM, k);
  assert.equal(overlayConfig("nonsense"), CALM, "an unknown mode falls back to Calm");
  assert.equal(OVERLAY.normal.maxLabels, Infinity);
  assert.equal(OVERLAY.debug.showScores, true);
  assert.equal(OVERLAY.normal.outlines, "all");
  assert.ok(CALM.backgroundLabels.includes("wall") && CALM.backgroundLabels.includes("ceiling"));
});

test("the compass tape, horizon and ladder show only on the Guide screen in Calm", () => {
  for (const s of ["scan", "explore", "guide"]) assert.equal(showsHud("calm", s), s === "guide");
  for (const s of ["scan", "explore", "guide"]) { assert.ok(showsHud("normal", s)); assert.ok(showsHud("debug", s)); }
});

test("plurals for the group label", () => {
  assert.deepEqual(["bottle", "water bottle", "cup", "mouse", "battery", "box", "glass", "shelf", "keys", "watch", "key"].map(plural),
    ["bottles", "water bottles", "cups", "mice", "batteries", "boxes", "glasses", "shelves", "keys", "watches", "keys"]);
});

/* ---------- scoring and hiding ---------- */

test("the score is confidence x centrality x recency", () => {
  const mid = [VIEW.w / 2, (VIEW.top + VIEW.h - VIEW.bot) / 2];
  const base = labelScore(obj(1, "mug"), mid, VIEW, CALM);
  assert.ok(base > labelScore(obj(1, "mug"), [20, VIEW.top + 5], VIEW, CALM), "the centre scores higher than the corner");
  assert.ok(labelScore(obj(1, "mug", { confidence: 0.6 }), mid, VIEW, CALM) < base, "less sure scores lower");
  assert.ok(labelScore(obj(1, "mug", { age_s: 7200 }), mid, VIEW, CALM) < base, "older scores lower");
  assert.ok(labelScore(obj(1, "mug", { age_s: 1e9 }), mid, VIEW, CALM) >= base * CALM.recencyFloor * 0.99 * 0.9 - 1e-9, "but never below the floor");
  assert.ok(Math.abs(labelScore(obj(1, "mug", { confidence: 0.5 }), mid, VIEW, CALM) - base * 0.5 / 0.9) < 1e-9, "confidence is a plain factor");
  const edge = labelScore(obj(1, "mug"), [0, VIEW.top], VIEW, CALM);
  assert.ok(edge >= 0.9 * CALM.centralityFloor * Math.pow(2, -10 / CALM.recencyHalfLifeS) - 1e-9, "an object at the edge keeps the centrality floor");
});

test("low confidence, large uncertainty, background classes and off-screen objects are hidden, each with its reason", () => {
  const at = [200, 400];
  assert.equal(hiddenReason(obj(1, "mug"), at, VIEW, CALM), null);
  assert.equal(hiddenReason(obj(1, "mug", { confidence: 0.3 }), at, VIEW, CALM), "lowConfidence");
  assert.equal(hiddenReason(obj(1, "mug", { uncertainty_deg: 12 }), at, VIEW, CALM), "uncertain");
  assert.equal(hiddenReason(obj(1, "Wall"), at, VIEW, CALM), "background");
  assert.equal(hiddenReason(obj(1, "ceiling"), at, VIEW, CALM), "background");
  assert.equal(hiddenReason(obj(1, "mug"), null, VIEW, CALM), "offscreen");
  assert.equal(hiddenReason(obj(1, "mug"), [-5, 400], VIEW, CALM), "offscreen");
  assert.equal(hiddenReason(obj(1, "mug"), [200, 50], VIEW, CALM), "offscreen", "under the top bar");
  assert.equal(hiddenReason(obj(1, "mug"), [200, 700], VIEW, CALM), "offscreen", "under the bottom controls");
  assert.equal(hiddenReason(obj(1, "wall", { confidence: 0.1 }), at, VIEW, OVERLAY.normal), null, "Normal hides nothing for these");
});

/* ---------- selection ---------- */

test("at most five labels, the best scoring ones", () => {
  const labels = ["laptop", "mug", "keys", "phone", "cable", "lamp", "book", "bottle", "charger", "remote", "pen", "plant"];
  const items = labels.map((l, i) => item(i + 1, l, 60 + (i % 4) * 90, 200 + Math.floor(i / 4) * 110, { confidence: 0.95 - i * 0.03 }));
  const r = selectLabels(items, VIEW, CALM);
  assert.ok(r.labels.length <= 5 && r.labels.length >= 1);
  assert.equal(r.shown, r.labels.length);
  const scores = r.labels.map((l) => l.score);
  assert.deepEqual(scores, [...scores].sort((a, b) => b - a), "ordered by score");
  const dropped = items.filter((i) => !r.labels.some((l) => l.ids.includes(i.o.id)));
  for (const d of dropped) assert.ok(labelScore(d.o, d.at, VIEW, CALM) <= Math.min(...scores) + 1e-9, `${d.o.label} was dropped but outscores a shown label`);
  assert.equal(r.hidden.overflow + r.hidden.collision, items.length - r.labels.length);
});

test("hidden objects never become labels, and the hidden counts add up", () => {
  const items = [item(1, "wall", 200, 300), item(2, "mug", 200, 330), item(3, "cup", 100, 300, { confidence: 0.2 }), item(4, "lamp", 300, 300, { uncertainty_deg: 20 }), { o: obj(5, "phone"), at: null }, item(6, "keys", 120, 420)];
  const r = selectLabels(items, VIEW, CALM);
  assert.deepEqual(r.labels.map((l) => l.label).sort(), ["keys", "mug"]);
  assert.deepEqual([r.hidden.background, r.hidden.lowConfidence, r.hidden.uncertain, r.hidden.offscreen], [1, 1, 1, 1]);
  assert.equal(r.candidates, 6);
});

test("duplicates close together become one label with a count; far apart or different they stay separate", () => {
  const near = selectLabels([item(1, "bottle", 150, 300), item(2, "bottle", 210, 340), item(3, "bottle", 180, 380)], VIEW, CALM);
  assert.deepEqual(near.labels.map((l) => [l.text, l.count]), [["3 bottles", 3]]);
  assert.deepEqual(near.labels[0].ids.sort(), [1, 2, 3]);
  assert.equal(near.shown, 3);
  const far = selectLabels([item(1, "bottle", 40, 250), item(2, "bottle", 350, 600)], VIEW, CALM);
  assert.deepEqual(far.labels.map((l) => l.text).sort(), ["bottle", "bottle"]);
  const mixed = selectLabels([item(1, "bottle", 150, 300), item(2, "cup", 170, 320)], VIEW, CALM);
  assert.deepEqual(mixed.labels.map((l) => l.text).sort(), ["bottle", "cup"]);
  const two = selectLabels([item(1, "water bottle", 150, 300), item(2, "water bottle", 200, 300)], VIEW, CALM);
  assert.equal(two.labels[0].text, "2 water bottles");
});

test("labels never overlap and stay on screen, however crowded", () => {
  let seed = 7;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let round = 0; round < 40; round++) {
    const n = 3 + Math.floor(rnd() * 25), names = ["laptop", "mug", "keys", "phone", "cable", "lamp", "book", "bottle", "charger"];
    const items = Array.from({ length: n }, (_, i) => item(i + 1, names[Math.floor(rnd() * names.length)], 20 + rnd() * 350, VIEW.top + 10 + rnd() * (VIEW.h - VIEW.bot - VIEW.top - 20), { confidence: 0.5 + rnd() * 0.5 }));
    const r = selectLabels(items, VIEW, { ...CALM, maxLabels: 8 });
    const boxes = labelBoxes(r);
    for (let i = 0; i < boxes.length; i++) {
      assert.ok(boxes[i].x >= 0 && boxes[i].x + boxes[i].w <= VIEW.w && boxes[i].y >= VIEW.top && boxes[i].y + boxes[i].h <= VIEW.h - VIEW.bot, `round ${round}: label off screen`);
      for (let j = i + 1; j < boxes.length; j++) assert.ok(!overlap(boxes[i], boxes[j]), `round ${round}: labels ${i} and ${j} overlap`);
    }
  }
});

test("a label that would sit on another moves out of the way, and is dropped when there is no room", () => {
  const w = 80, spot = [195, 300], placed = [];
  const first = placeLabel(w, spot, placed, VIEW, CALM);
  placed.push(first);
  const second = placeLabel(w, spot, placed, VIEW, CALM);
  assert.ok(second && !overlap(first, second) && second.y !== first.y, "shifted");
  const crowded = Array.from({ length: 30 }, (_, i) => ({ x: 0, y: VIEW.top + i * 20, w: VIEW.w, h: 20 }));
  assert.equal(placeLabel(w, spot, crowded, VIEW, CALM), null, "no room: left out, not drawn over another label");
});

test("one outline only: the object under the reticle, else none", () => {
  const cx = VIEW.w / 2, cy = (VIEW.top + VIEW.h - VIEW.bot) / 2;
  const r = selectLabels([item(1, "mug", cx + 30, cy + 10), item(2, "lamp", cx + 80, cy), item(3, "keys", 40, 300)], VIEW, CALM);
  assert.equal(r.outlineId, 1, "the nearest to the centre");
  const none = selectLabels([item(1, "mug", cx + 200, cy + 200), item(2, "lamp", 30, 250)], VIEW, CALM);
  assert.equal(none.outlineId, null, "nothing under the reticle: no outline");
  const hiddenOne = selectLabels([item(1, "wall", cx, cy), item(2, "mug", cx + 60, cy)], VIEW, CALM);
  assert.equal(hiddenOne.outlineId, 2, "a hidden class is not outlined either");
  assert.equal(selectLabels([item(1, "mug", cx, cy)], VIEW, OVERLAY.normal).outlineId, null, "Normal outlines everything itself: the choice is Calm's");
});

test("the Find or Guide target is always labelled and outlined, whatever its score or the label limit", () => {
  const items = Array.from({ length: 9 }, (_, i) => item(i + 1, ["a", "b", "c", "d", "e", "f", "g", "h", "i"][i], 60 + (i % 3) * 120, 200 + Math.floor(i / 3) * 120));
  items.push(item(99, "charger", 350, 640, { confidence: 0.1, uncertainty_deg: 30, hl: true }));
  const r = selectLabels(items, VIEW, CALM);
  assert.equal(r.outlineId, 99);
  assert.ok(r.labels.some((l) => l.ids.includes(99) && l.target), "the target has a label");
  assert.equal(r.labels[0].target, true, "and it comes first");
  assert.ok(r.labels.length <= CALM.maxLabels + 1);
});

test("the choice does not depend on the order of the objects", () => {
  const items = Array.from({ length: 14 }, (_, i) => item(i + 1, ["laptop", "mug", "keys", "cable"][i % 4], 30 + ((i * 53) % 330), 140 + ((i * 97) % 450), { confidence: 0.55 + (i % 5) * 0.09, age_s: i * 40 }));
  const shape = (r) => JSON.stringify([r.outlineId, r.labels.map((l) => [l.text, Math.round(l.x), Math.round(l.y)])]);
  const a = shape(selectLabels(items, VIEW, CALM));
  for (const order of [[...items].reverse(), [...items.slice(5), ...items.slice(0, 5)]]) assert.equal(shape(selectLabels(order, VIEW, CALM)), a);
});

test("Normal and Debug keep every visible object labelled", () => {
  const items = Array.from({ length: 12 }, (_, i) => item(i + 1, `thing${i}`, 40 + (i % 4) * 100, 160 + Math.floor(i / 4) * 150, { confidence: 0.2 }));
  const calm = selectLabels(items, VIEW, CALM), normal = selectLabels(items, VIEW, OVERLAY.normal);
  assert.equal(calm.labels.length, 0, "Calm hides low confidence");
  assert.ok(normal.labels.length >= 10, "Normal does not");
});

/* ---------- mask outlines ---------- */

const circle = (n, r = 100) => Array.from({ length: n }, (_, i) => [200 + r * Math.cos((i / n) * 2 * Math.PI), 200 + r * Math.sin((i / n) * 2 * Math.PI)]);

test("simplifying keeps the corners and drops the points in between", () => {
  const noisy = [];
  for (const [a, b] of [[[0, 0], [100, 0]], [[100, 0], [100, 60]], [[100, 60], [0, 60]], [[0, 60], [0, 0]]])
    for (let t = 0; t < 10; t++) noisy.push([a[0] + ((b[0] - a[0]) * t) / 10 + (t % 2 ? 0.4 : -0.4), a[1] + ((b[1] - a[1]) * t) / 10 + (t % 2 ? 0.3 : -0.3)]);
  const s = simplifyPolygon(noisy, 3);
  assert.ok(s.length >= 4 && s.length <= 6, `a rectangle has about four corners, got ${s.length}`);
  for (const [x, y] of [[0, 0], [100, 0], [100, 60], [0, 60]]) assert.ok(s.some((p) => Math.hypot(p[0] - x, p[1] - y) < 2), `corner ${x},${y} was lost`);
});

test("a round mask loses most of its points but not its shape", () => {
  const poly = circle(240), s = simplifyPolygon(poly, 3);
  assert.ok(s.length < poly.length / 3);
  assert.ok(Math.abs(polygonArea(s) - polygonArea(poly)) / polygonArea(poly) < 0.05);
  for (const p of s) assert.ok(Math.abs(Math.hypot(p[0] - 200, p[1] - 200) - 100) < 1e-6, "kept points are original points");
});

test("smoothing rounds corners and stays inside the original outline", () => {
  const sq = [[0, 0], [100, 0], [100, 100], [0, 100]];
  const s = smoothPolygon(sq, 1);
  assert.equal(s.length, 8);
  assert.ok(polygonArea(s) < polygonArea(sq) && polygonArea(s) > polygonArea(sq) * 0.8);
  assert.ok(s.every(([x, y]) => x >= 0 && x <= 100 && y >= 0 && y <= 100));
  assert.equal(smoothPolygon(sq, 0), sq);
  assert.equal(smoothPolygon(smoothPolygon(sq, 1), 1).length, 16);
});

test("degenerate masks pass through, and an outline is prepared once", () => {
  assert.deepEqual(simplifyPolygon([[1, 1], [2, 2]]), [[1, 1], [2, 2]]);
  assert.deepEqual(simplifyPolygon(null), []);
  assert.equal(prepareOutline(null, CALM.outline), null);
  assert.equal(prepareOutline([[0, 0], [1, 1]], CALM.outline).length, 2);
  const poly = circle(80);
  assert.equal(cachedOutline(5, poly, CALM.outline), cachedOutline(5, poly, CALM.outline), "the same object is returned from the cache");
  assert.ok(prepareOutline(poly, CALM.outline).length < poly.length * 2);
});
