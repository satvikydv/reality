// Demo data for every screen in every state (screens.js lists them). /?demo&screen=ID&state=STATE renders a screen from here
// with the same render code the real app uses, so a design can be checked state by state without a server or a phone.
// Each entry is a function (timestamps are relative to now). Shapes match the server's answers (see docs/screens.md).

const t = (agoS) => Date.now() / 1000 - agoS;
export const SHOT = { frame_id: 1, box: [180, 190, 330, 300], width: 640, height: 480 };

const rooms = () => [
  { id: 1, name: "Living Room", active: true, n_objects: 14, n_scans: 3, n_drafts: 0, last_scanned: t(7200), coverage_pct: 97, scan_id: 3 },
  { id: 2, name: "Desk", active: false, n_objects: 6, n_scans: 1, n_drafts: 0, last_scanned: t(90000), coverage_pct: 62, scan_id: 5 },
  { id: 3, name: "Wardrobe", active: false, n_objects: 0, n_scans: 0, n_drafts: 0, last_scanned: null, coverage_pct: 0, scan_id: null },
];

const R = (id) => rooms().find((r) => r.id === id);
const ref = (r) => ({ id: r.id, name: r.name, active: r.active });

export const OBJECTS = [["laptop", -20, 5], ["laptop", 35, 0], ["laptop", 200, 8], ["cable", 60, -25], ["cable", -75, -30], ["cable", 150, -20], ["bottle", 95, -5],
  ["bottle", 250, 0], ["charger", -60, -10], ["mug", 120, 2], ["keys", 300, -15], ["phone", 10, -12], ["monitor", -5, 20], ["lamp", 330, 35]]
  .map(([label, yaw, pitch], i) => ({ id: i + 1, label, yaw: (yaw + 360) % 360, pitch, n_obs: 3, replay: SHOT }));

const recent = () => OBJECTS.slice(0, 5).map((o, i) => ({ ...o, last_seen: t(600 * (i + 1)), room: ref(R(1)) }));

const SUMMARY = () => ({ type: "scan_summary", scan_id: 7, room_id: 1, room_name: "Living Room", status: "draft", speech: "I found 14 objects: 3 laptops, 3 cables, 2 water bottles, a charger, a mug, keys and a phone.",
  coverage: { covered_deg: 349, gaps: [[100, 110]] }, frames: { received: 42, stored: 42, failed: 0, rejected: 0 }, seconds: 31, cost_usd: 0.084, objects: OBJECTS,
  scan_quality: { ok: false, words: "Saved scans can still be compared, but a later comparison will be surer if next time you:",
    tips: ["Pan slower so each thing is in at least two shots: only 9 of 14 were. Something seen once cannot be called gone later.",
      "Tilt up a little so the far wall is in view: the shots are mostly the desk and the things on it, which leaves little to line a later scan up with."],
    checks: [{ id: "frames", ok: true, value: 42, target: 5, tip: null }, { id: "seen_twice", ok: false, value: "9 of 14", target: "60%", tip: "Pan slower so each thing is in at least two shots." }] } });

const scans = () => [
  { id: 7, status: "draft", baseline: 0, started_at: t(300), ended_at: t(260), n_objects: 14, n_frames: 42, room_id: 1 },
  { id: 3, status: "saved", baseline: 0, started_at: t(7200), ended_at: t(7170), n_objects: 14, n_frames: 42, room_id: 1 },
  { id: 2, status: "saved", baseline: 1, started_at: t(90000), ended_at: t(89960), n_objects: 12, n_frames: 38, room_id: 1 },
  { id: 1, status: "saved", baseline: 0, started_at: t(200000), ended_at: t(199960), n_objects: 9, n_frames: 30, room_id: 1 },
];

const NOTES = () => [
  { id: 2, text: "Milk expires Friday", author: "Maya", yaw: 35, pitch: -5, label: "fridge", created_at: t(3600), done_at: null },
  { id: 1, text: "Call the plumber about the tap", author: "phone", yaw: 200, pitch: 0, label: null, created_at: t(90000), done_at: null }];

const FOUND = () => ({ type: "find_result", query: "my charger", concept: "charger", found: true, room_id: 1, room_name: "Living Room", active: true,
  speech: "The charger was last seen about 60 degrees to the left of where the scan started, below the laptop.",
  object: { id: 9, label: "charger", yaw: 300, pitch: -10, n_obs: 3, conf_best: 0.9, age_s: 120, uncertainty_deg: 8, fov_error_deg: 2.5 },
  from_scan_start: { dyaw: -60, dpitch: -10 }, replay: SHOT });
const NOTFOUND = () => ({ type: "find_result", query: "my passport", concept: "passport", found: false, room_id: 1, room_name: "Living Room", active: true, speech: "I have not seen that yet.",
  object: null, replay: null, checked: { scans: 3, covered_deg: 313, scan_id: 3 } });
const NOTSCANNED = () => ({ type: "find_result", query: "my keys", concept: "keys", found: false, room_id: 3, room_name: "Wardrobe", active: true, speech: "Wardrobe has not been scanned yet.",
  object: null, replay: null, checked: { scans: 0, covered_deg: 0, scan_id: null } });

const CHANGES = () => ({ status: "ok", room: ref(R(1)), scan_before: 2, scan_after: 3,
  scans: [{ id: 3, started_at: t(7200), baseline: false }, { id: 2, started_at: t(90000), baseline: true }, { id: 1, started_at: t(200000), baseline: false }],
  summary: "The mug moved. The charger may have moved. A water bottle is new. 1 object could not be compared.",
  alignment: { quality: "good", method: "pose prior", offset_deg: 37.2, offset_used_deg: 37.2, offset_source: "images", inliers: 214, pairs: 6, spread_deg: 0.4, viewpoint_cm: 15, parallax_deg: 1.1 },
  moved: [{ label: "mug", key: "moved:10:11", review: null, shift: { yaw_deg: 40, pitch_deg: 0, words: "40° to the right" }, from: { replay: SHOT }, to: { replay: SHOT }, speech: "The mug moved.", tolerance_deg: 8.2 }],
  moved_low_confidence: [],
  possibly_moved: [{ label: "charger", key: "possibly_moved:15:16", review: null, shift: { yaw_deg: 12, pitch_deg: 0, words: "12° to the right" }, from: { replay: SHOT }, to: { replay: SHOT }, speech: "The charger may have moved.", tolerance_deg: 8.2 }],
  removed: [{ label: "keys", key: "removed:12", review: "confirmed", replay: SHOT, speech: "The keys are gone; they were 150 degrees right, near the door." }],
  added: [{ label: "bottle", key: "added:13", review: "dismissed", replay: SHOT, speech: "A bottle is new, 40 degrees right, on the desk." }],
  cant_compare: [{ label: "phone", key: "cant_compare:14", was: "removed?", reason: "that direction was not rescanned" }], unchanged_count: 10 });

// a desk rescanned from 3 frames: the pictures line up only roughly, so the only claim is a move far beyond what the viewpoint explains
const WEAK_CHANGES = () => {
  const cant = [{ label: "laptop", key: "cant_compare:21", was: "moved?", reason: "the two scans only lined up roughly, so this cannot be called a move" },
    { label: "cup", key: "cant_compare:22", was: "removed?", reason: "the two scans only lined up roughly, so this cannot be called a move" }];
  return { ...CHANGES(), summary: "The two scans lined up only roughly, so I can only point out a change too big to be the viewpoint. The mouse seems to have moved: it was 40° right, now 25° right. The two scans only lined up roughly, so check it. 2 objects could not be compared.",
    alignment: { quality: "weak", method: "all pairs", offset_deg: 0.6, offset_used_deg: 0.6, offset_source: "images", inliers: 84, pairs: 1, spread_deg: 0, offset_sem_deg: 1.3, viewpoint_cm: 15, parallax_deg: 0.4, low_confidence_enabled: true },
    moved: [], possibly_moved: [], removed: [], added: [],
    moved_low_confidence: [{ label: "mouse", key: "moved_low_confidence:30:31", review: null, low_confidence: true, shift: { yaw_deg: -16, pitch_deg: 27, words: "16° to the left and 27° up" },
      from: { replay: SHOT }, to: { replay: SHOT }, speech: "The mouse seems to have moved.", tolerance_deg: 9.0, sep_deg: 30.1 }],
    cant_compare: cant, unverified: cant, unchanged_count: 3 };
};

const settingsRooms = () => rooms();

// the objects review: a scan's objects grouped by label
const thumbOf = (o) => ({ ...SHOT, image_url: "/world/frame/1.jpg", box: SHOT.box });
const labelsData = (empty = false) => {
  const by = new Map();
  for (const o of OBJECTS) by.set(o.label, [...(by.get(o.label) || []), o]);
  const labels = empty ? [] : [...by].map(([label, objs]) => ({ label, instances: objs.length, sightings: objs.length * 3, avg_confidence: 0.82,
    objects: objs.map((o, i) => ({ id: o.id, label, sightings: 3, avg_confidence: 0.8 + i * 0.03, best_confidence: 0.93, yaw: o.yaw, pitch: o.pitch, uncertainty_deg: 3.2, first_seen: t(900), last_seen: t(300), thumb: thumbOf(o) })) }))
    .sort((a, b) => b.instances - a.instances);
  return { scan: { id: 3, status: "saved", room_id: 1, room_name: "Living Room", started_at: t(7200), ended_at: t(7170), n_frames: 42 }, labels,
    instances: labels.reduce((n, g) => n + g.instances, 0), sightings: labels.reduce((n, g) => n + g.sightings, 0), possible_duplicates: empty ? 0 : 2,
    hidden_labels: empty ? [] : [{ label: "wall", sightings: 6 }] };
};
const detailData = () => ({ scan: { id: 3, status: "saved", room_id: 1, room_name: "Living Room" },
  object: { id: 1, label: "laptop", sightings: 3, avg_confidence: 0.85, best_confidence: 0.93, yaw: 340, pitch: 5, uncertainty_deg: 3.2 }, spread_deg: 2.1, fov_error_deg: 1.4,
  sightings: [0.93, 0.84, 0.78].map((c, i) => ({ id: 11 + i, frame_id: 1 + i, image_url: `/world/frame/${1 + i}.jpg`, box: SHOT.box, width: 640, height: 480, confidence: c, mask: null,
    anchor: { yaw: 340 + i, pitch: 5 }, fragment: i === 2, track_id: null, fov_error_deg: 1.2, camera: { yaw: 335 + i * 4, pitch: 2, roll: 0, hfov_deg: 60, ts: 1 + i } })),
  decisions: [{ action: "new", text: "Started a new object: nothing close enough to join." }], corrections: [], decisions_source: "log" });

export const FIXTURES = {
  welcome: { default: () => ({}) },
  primer: { default: () => ({}), denied: () => ({ problem: { kind: "denied", retry: true, title: "Camera access was blocked", body: "Reality OS needs the camera to see the room. Tap Try again and choose Allow." } }) },
  home: {
    default: () => ({ rooms: rooms(), recent: recent(), activeId: 1 }),
    empty: () => ({ rooms: [], recent: [], activeId: null }),
    loading: () => ({}),
    error: () => ({ message: "The server answered with an error (500)." }),
    offline: () => ({ message: "The server is not reachable. Reality OS keeps trying to reconnect by itself." }),
    slow: () => ({ message: "The server is slow to answer. Still trying…" }),
  },
  wizard: {
    new: () => ({ rooms: [], activeId: null, selectedId: null, name: "" }),
    rescan: () => ({ rooms: rooms(), activeId: 1, selectedId: 1, name: "" }),
    error: () => ({ rooms: rooms(), activeId: 1, selectedId: null, name: "Desk", message: "There is already a room called Desk." }),
  },
  scan: { ready: () => ({ room: R(1), align: { ok: true, within: false, inliers: 84, words: "Turn left 8 degrees, move slightly right.", turn_deg: -8, tilt_deg: 1.2, roll_deg: 0.5, move: "right", parallax_deg: 2.4 } }), live: () => ({ room: R(1) }) },
  summary: {
    processing: () => ({ room: ref(R(1)), progress: { fraction: 0.6, text: "Reading frames: 18 of 30", level: "ok" } }),
    stalled: () => ({ room: ref(R(1)), progress: { fraction: 0.7, text: "No answer from the server for a while.", level: "stalled" } }),
    review: () => ({ summary: SUMMARY() }),
    empty: () => ({ summary: { ...SUMMARY(), objects: [], speech: "I did not find any objects." } }),
  },
  room: {
    default: () => ({ room: R(1), recent_objects: recent(), labels: { laptop: 3, cable: 3, bottle: 2 }, scans: scans() }),
    empty: () => ({ room: R(3), recent_objects: [], labels: {}, scans: [] }),
    loading: () => ({ room: R(1) }),
    error: () => ({ room: R(1), message: "The server answered with an error (500)." }),
  },
  find: {
    idle: () => ({ room: R(1), labels: ["charger", "laptop", "keys", "mug"], scope: "room" }),
    busy: () => ({ room: R(1), labels: ["charger"], scope: "room", message: "Looking for “charger”…" }),
    slow: () => ({ room: R(1), labels: ["charger"], scope: "room", message: "Still looking. The answer is slower than usual…" }),
    error: () => ({ room: R(1), labels: ["charger"], scope: "room", message: "The server could not answer: no response. Try again." }),
    offline: () => ({ room: R(1), labels: [], scope: "room", message: "The server is not reachable. Reality OS keeps trying to reconnect by itself." }),
    "no-room": () => ({ room: null, labels: [], scope: "room", rooms: rooms() }),
    "room-empty": () => ({ room: R(3), labels: [], scope: "room" }),
    "all-results": () => ({ room: R(1), labels: [], scope: "all", query: "mug", groups: [
      { room_id: 2, room_name: "Desk", active: false, found: true, speech: "The mug was last seen about 40 degrees to the right.", object: { id: 4, label: "mug" }, replay: SHOT },
      { room_id: 1, room_name: "Living Room", active: true, found: false, checked: { scans: 3, covered_deg: 313, scan_id: 3 } },
      { room_id: 3, room_name: "Wardrobe", active: false, found: false, checked: { scans: 0, covered_deg: 0, scan_id: null } }] }),
    "all-none": () => ({ room: R(1), labels: [], scope: "all", query: "passport", groups: [
      { room_id: 1, room_name: "Living Room", active: true, found: false, checked: { scans: 3, covered_deg: 313, scan_id: 3 } }] }),
  },
  result: { found: FOUND, notfound: NOTFOUND, notscanned: NOTSCANNED },
  guide: { default: () => ({ result: FOUND() }), found: () => ({ result: FOUND(), found: true }) },
  changes: {
    default: () => ({ room: R(1), data: CHANGES() }),
    loading: () => ({ room: R(1) }),
    "need-two": () => ({ room: R(2), message: "Comparing needs two saved scans of Desk; scan it again after changing something." }),
    error: () => ({ room: R(1), message: "The server answered with an error (500)." }),
    offline: () => ({ room: R(1), message: "The server is not reachable. Reality OS keeps trying to reconnect by itself." }),
    slow: () => ({ room: R(1), message: "The server is slower than usual. You can wait, or go back and try later." }),
    "no-room": () => ({ room: null, rooms: rooms() }),
    "nothing-changed": () => ({ room: R(1), data: { ...CHANGES(), moved: [], moved_low_confidence: [], possibly_moved: [], removed: [], added: [], cant_compare: [], unverified: [], summary: "Nothing changed." } }),
    "weak-alignment": () => ({ room: R(1), data: WEAK_CHANGES() }),
  },
  settings: { default: () => ({ rooms: settingsRooms() }), loading: () => ({}), error: () => ({ message: "The server answered with an error (500)." }) },
  roomsettings: { default: () => ({ room: R(1), scans: scans() }), error: () => ({ room: R(1), scans: scans(), message: "There is already a room called Desk." }) },
  explore: { default: () => ({ room: R(1) }), "no-memory": () => ({ room: R(3) }) },
  switcher: { default: () => ({ rooms: rooms() }), empty: () => ({ rooms: [] }) },
  labels: {
    default: () => ({ data: labelsData() }), empty: () => ({ data: labelsData(true) }), loading: () => ({}),
    error: () => ({ message: "The server answered with an error (500)." }), offline: () => ({ message: "The server is not reachable. Reality OS keeps trying to reconnect by itself." }),
  },
  instances: {
    default: () => ({ data: labelsData(), label: "laptop" }), empty: () => ({ data: labelsData(), label: "nothing" }), loading: () => ({}),
    error: () => ({ message: "The server answered with an error (500)." }),
  },
  sightings: { default: () => ({ detail: detailData() }), loading: () => ({}), error: () => ({ message: "The server answered with an error (500)." }) },
  note: { default: () => ({ notes: NOTES(), at: { label: "laptop", yaw: 35, pitch: 0 } }), empty: () => ({ notes: [], at: { label: null, yaw: 0, pitch: 0 } }) },
  ask: { idle: () => ({}), busy: () => ({}), error: () => ({ message: "The model could not answer. Try again in a moment." }) },
  history: {
    default: () => ({ scans: scans().filter((s) => s.status === "saved").slice(1) }), empty: () => ({ scans: [] }), loading: () => ({}),
    error: () => ({ message: "The server answered with an error (500)." }),
  },
  quick: { default: () => ({ labels: ["charger", "laptop", "keys", "mug", "water bottle"] }), empty: () => ({ labels: [] }) },
};

export const fixture = (screen, state) => FIXTURES[screen]?.[state]?.();
