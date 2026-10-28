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
  coverage: { covered_deg: 349, gaps: [[100, 110]] }, frames: { received: 42, stored: 42, failed: 0, rejected: 0 }, seconds: 31, cost_usd: 0.084, objects: OBJECTS });

const scans = () => [
  { id: 7, status: "draft", baseline: 0, started_at: t(300), ended_at: t(260), n_objects: 14, n_frames: 42, room_id: 1 },
  { id: 3, status: "saved", baseline: 0, started_at: t(7200), ended_at: t(7170), n_objects: 14, n_frames: 42, room_id: 1 },
  { id: 2, status: "saved", baseline: 1, started_at: t(90000), ended_at: t(89960), n_objects: 12, n_frames: 38, room_id: 1 },
  { id: 1, status: "saved", baseline: 0, started_at: t(200000), ended_at: t(199960), n_objects: 9, n_frames: 30, room_id: 1 },
];

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
  summary: "The mug moved. A water bottle is new. 1 object could not be checked because that part of the room was not scanned both times.",
  moved: [{ label: "mug", key: "moved:10:11", review: null, shift: { yaw_deg: 40, pitch_deg: 0, words: "40° to the right" }, from: { replay: SHOT }, to: { replay: SHOT }, speech: "The mug moved." }],
  removed: [{ label: "keys", key: "removed:12", review: "confirmed", replay: SHOT, speech: "The keys are gone; they were 150 degrees right, near the door." }],
  added: [{ label: "bottle", key: "added:13", review: "dismissed", replay: SHOT, speech: "A bottle is new, 40 degrees right, on the desk." }],
  unverified: [{ label: "phone", key: "unverified:14", reason: "that direction was not rescanned" }], unchanged_count: 10 });

const settingsRooms = () => rooms();

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
  scan: { ready: () => ({ room: R(1) }), live: () => ({ room: R(1) }) },
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
    "nothing-changed": () => ({ room: R(1), data: { ...CHANGES(), moved: [], removed: [], added: [], unverified: [], summary: "Nothing changed." } }),
  },
  settings: { default: () => ({ rooms: settingsRooms() }), loading: () => ({}), error: () => ({ message: "The server answered with an error (500)." }) },
  roomsettings: { default: () => ({ room: R(1), scans: scans() }), error: () => ({ room: R(1), scans: scans(), message: "There is already a room called Desk." }) },
  explore: { default: () => ({ room: R(1) }), "no-memory": () => ({ room: R(3) }) },
  switcher: { default: () => ({ rooms: rooms() }), empty: () => ({ rooms: [] }) },
};

export const fixture = (screen, state) => FIXTURES[screen]?.[state]?.();
