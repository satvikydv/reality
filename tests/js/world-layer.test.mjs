import test from "node:test";
import assert from "node:assert/strict";
import { WorldLayer } from "../../web/js/world.js";

const layer = () => new WorldLayer({ now: () => 1000 });

test("the layer keeps the room's notes from the overlay and replaces them on every refresh", () => {
  const w = layer();
  w.apply({ objects: [], notes: [{ id: 1, text: "milk expires Friday", yaw: 10, pitch: 0 }] });
  assert.deepEqual(w.notes.map((n) => n.text), ["milk expires Friday"]);
  w.apply({ objects: [] });                                    // an answer without notes (another room, an error) clears them
  assert.deepEqual(w.notes, []);
});

test("an answer is a map from object id to its reason, and an empty answer shows everything again", () => {
  const w = layer();
  w.setAsk([{ object_id: 3, reason: "has a cable" }, { object_id: 5 }]);
  assert.equal(w.asked.get(3), "has a cable");
  assert.equal(w.asked.get(5), "");
  assert.equal(w.asked.has(4), false);
  w.setAsk([]);
  assert.equal(w.asked.size, 0);
  w.setAsk(null);
  assert.equal(w.asked.size, 0);
});

test("ghosts carry the time of the earlier scan only while there are ghosts", () => {
  const w = layer();
  w.setGhosts([{ label: "mug", yaw: 10, pitch: 0, kind: "gone", unsure: false }], 12345);
  assert.equal(w.ghosts.length, 1);
  assert.equal(w.ghostWhen, 12345);
  w.setGhosts([], 12345);
  assert.equal(w.ghosts.length, 0);
  assert.equal(w.ghostWhen, null);
});
