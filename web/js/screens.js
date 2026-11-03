// The one registry of screens. Navigation, the bottom bar, the camera rules, demo mode (/?demo&screen=ID&state=STATE), the docs
// (docs/screens.md) and the tests all read this table, so a new look can be built from it without reading the app code.
//
//   el      the section in index.html (id "s-<id>"); inside it, elements with data-show="<state> ..." appear only in those states
//   nav     which bottom-bar tab is lit (null: no bar on this screen)
//   top     "rooms": the top bar shows the room switcher
//   camera  true: the camera is on while this screen shows (and only then)
//   states  every state the screen can be in; each has a demo fixture in fixtures.js and a section in docs/screens.md
//   data    what the screen shows;  api  where that comes from

export const NAV = [["home", "Rooms"], ["find", "Find"], ["scan", "Scan"], ["changes", "Changes"], ["settings", "Settings"]];

export const SCREENS = {
  welcome: { title: "Intro", nav: null, top: null, camera: false, states: ["default"],
    data: ["three intro cards (what it does, how to scan, your data)"], api: [] },
  primer: { title: "Permission primer", nav: null, top: null, camera: false, states: ["default", "denied"],
    data: ["what the camera and motion prompts are for", "the reason a permission was refused"], api: [] },
  home: { title: "Rooms", nav: "home", top: "rooms", camera: false, states: ["default", "empty", "loading", "error", "offline", "slow"],
    data: ["rooms with name, object count, coverage, last scanned, active flag", "recent objects", "search results grouped per room"],
    api: ["GET /api/rooms", "GET /api/search?q=&room_id=", "GET /api/status"] },
  wizard: { title: "Scan a room", nav: null, top: null, camera: false, states: ["new", "rescan", "error"],
    data: ["existing rooms (to rescan)", "name suggestions", "whether to make the room active"], api: ["POST /api/rooms", "PATCH /api/rooms/{id}"] },
  scan: { title: "Live scan", nav: null, top: null, camera: true, states: ["ready", "live"],
    data: ["room being scanned", "light reading", "coverage bins, pitch bands, view direction and cone", "frames, objects, coaching hint"],
    api: ["WS /ws scan_start{room_id} / scan_end / scan_cancel", "GET /api/rooms/{id}/reference", "WS /ws live frame (flag 4) answered with align_hint"] },
  summary: { title: "Scan summary", nav: null, top: null, camera: false, states: ["processing", "stalled", "review", "empty"],
    data: ["progress while frames are read", "objects grouped by kind with thumbnails", "frames, seconds, coverage, cost"],
    api: ["WS scan_summary", "POST /api/rooms/{id}/scans/{scan}/save", "POST /api/rooms/{id}/scans/{scan}/discard", "GET /api/status"] },
  room: { title: "Room details", nav: "home", top: "rooms", camera: false, states: ["default", "empty", "loading", "error"],
    data: ["room name, active flag", "objects, coverage, last scanned, saved scans", "recent objects with thumbnails", "scan history with memory / baseline / unsaved marks"],
    api: ["GET /api/rooms/{id}", "GET /api/rooms/{id}/scans", "PATCH /api/rooms/{id}", "POST /api/rooms/{id}/baseline", "DELETE /api/rooms/{id}/scans/{scan}",
          "POST /api/rooms/{id}/scans/{scan}/save", "POST /api/rooms/{id}/scans/{scan}/discard"] },
  find: { title: "Find", nav: "find", top: "rooms", camera: false, states: ["idle", "busy", "slow", "error", "offline", "no-room", "room-empty", "all-results", "all-none"],
    data: ["the active room and its object names", "scope: this room or all rooms", "per-room results when searching all rooms"],
    api: ["WS find{query, room_id | all_rooms}", "PATCH /api/rooms/{id} (switch room)"] },
  result: { title: "Find result", nav: null, top: null, camera: false, states: ["found", "notfound", "notscanned"],
    data: ["answer text", "stored photo with the object's box", "direction (yaw, pitch), confidence, last seen", "what was and was not checked"],
    api: ["WS find_result", "GET /world/frame/{id}.jpg", "POST /api/guide"] },
  guide: { title: "Guided search", nav: null, top: null, camera: true, states: ["default", "found"],
    data: ["target direction and live phone direction", "arrow, angle and direction in words", "confidence and last seen"], api: ["POST /api/guide"] },
  changes: { title: "What changed", nav: "changes", top: "rooms", camera: false, states: ["default", "loading", "need-two", "error", "offline", "slow", "no-room", "nothing-changed", "weak-alignment"],
    data: ["the room's saved scans (before / after pickers)", "how well the scans lined up", "moved, moved (low confidence), possibly moved, new, removed with an angular shift",
      "can't compare list with a reason each", "confirm / dismiss per change"],
    api: ["GET /api/rooms/{id}/changes?before=&after=", "POST /api/rooms/{id}/changes/review"] },
  settings: { title: "Settings", nav: "settings", top: "rooms", camera: false, states: ["default", "loading", "error"],
    data: ["voice language, keyframe rate, overlay detail", "rooms (to rename or delete)", "debug HUD (?debug)"],
    api: ["GET /api/rooms", "DELETE /api/data"] },
  roomsettings: { title: "Room settings", nav: "settings", top: null, camera: false, states: ["default", "error"],
    data: ["room name", "active flag", "what deleting would erase (scans, objects)"], api: ["PATCH /api/rooms/{id}", "DELETE /api/rooms/{id}"] },
  explore: { title: "Explore (AR)", nav: null, top: null, camera: true, states: ["default", "no-memory"],
    data: ["the room's remembered objects drawn over the camera", "overlay detail setting"], api: ["GET /api/overlay?room_id="] },
  labels: { title: "Objects: labels", nav: "home", top: null, camera: false, states: ["default", "empty", "loading", "error", "offline"],
    data: ["a scan's objects grouped by label: instances, sightings, mean confidence", "labels hidden as not an object in this room", "how many possible duplicates there are"],
    api: ["GET /api/scans/{id}/objects", "GET /api/scans/{id}/duplicates", "POST /api/rooms/{id}/not-objects", "DELETE /api/rooms/{id}/not-objects/{label}"] },
  instances: { title: "Objects: instances", nav: "home", top: null, camera: false, states: ["default", "empty", "loading", "error"],
    data: ["the instances of one label with a thumbnail, sightings and confidence each", "which two are picked to merge"],
    api: ["POST /api/objects/merge", "POST /api/rooms/{id}/not-objects", "DELETE /api/objects/{id}"] },
  sightings: { title: "Objects: sightings", nav: "home", top: null, camera: false, states: ["default", "loading", "error"],
    data: ["one instance: direction, uncertainty, mean confidence", "its sightings: picture with the box, SAM confidence, camera direction"],
    api: ["GET /api/objects/{id}", "POST /api/sightings/{id}/detach", "DELETE /api/objects/{id}"] },
  quick: { title: "Quick ask", nav: null, top: null, camera: false, sheet: true, states: ["default", "empty"],
    data: ["what the room contains, as tap-to-ask names"], api: ["WS find{query, room_id | all_rooms}"] },
  note: { title: "Notes", nav: null, top: null, camera: false, sheet: true, states: ["default", "empty"],
    data: ["where the note will be left (the object under the reticle, or the direction faced)", "the room's open notes: text, author, when, the object it is on"],
    api: ["GET /api/rooms/{id}/notes", "POST /api/rooms/{id}/notes", "PATCH /api/notes/{id}", "DELETE /api/notes/{id}"] },
  ask: { title: "Ask the room", nav: null, top: null, camera: false, sheet: true, states: ["idle", "busy", "error"],
    data: ["a question typed or spoken", "example questions", "the answer and the objects it names (shown over the camera)"], api: ["POST /api/rooms/{id}/ask"] },
  history: { title: "Rewind", nav: null, top: null, camera: false, sheet: true, states: ["default", "empty", "loading", "error"],
    data: ["the room's earlier saved scans", "what was there then and is gone or moved now (ghosts over the camera)"], api: ["GET /api/rooms/{id}/scans", "GET /api/rooms/{id}/rewind?scan="] },
  switcher: { title: "Room switcher", nav: null, top: null, camera: false, sheet: true, states: ["default", "empty"],
    data: ["rooms with the active one marked"], api: ["GET /api/rooms", "PATCH /api/rooms/{id}"] },
};

export const ids = () => Object.keys(SCREENS);
export const sectionId = (id) => `s-${id}`;
export const isCamera = (id) => !!SCREENS[id]?.camera;
export const navTab = (id) => SCREENS[id]?.nav ?? null;
export const hasRoomSwitcher = (id) => SCREENS[id]?.top === "rooms";
export const hasState = (id, state) => !!SCREENS[id]?.states.includes(state);
export const firstState = (id) => SCREENS[id].states[0];

// Show only the parts of a screen that belong to `state` (elements carry data-show="state state ...").
export function applyState(section, state) {
  section.dataset.state = state;
  for (const e of section.querySelectorAll("[data-show]")) e.hidden = !e.dataset.show.split(/\s+/).includes(state);
}
