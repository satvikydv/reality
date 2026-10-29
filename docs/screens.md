# Phone screens: inventory for a re-skin

This is the brief for restyling the phone app. The app is functional and deliberately plain: every screen, state and tap works, and nothing about the look is final. Restyle freely; the structure below is what the scripts rely on.

`web/js/screens.js` is the registry of the screens listed here, and `tests/js/rooms-ui.test.mjs` fails if this file, the registry, the markup or the demo fixtures drift apart.

## How the pieces are separated

| Part | Where | Re-skin touches it? |
|---|---|---|
| Design tokens (colours, radii, spacing, type sizes, tap sizes) | `web/css/theme.css` (`:root` variables) | yes, mostly here |
| Screen and component styles | `web/css/app.css` (uses the tokens) | yes |
| Markup: one `<section id="s-<screen>">` per screen | `web/index.html` | yes, keep the ids, `data-show` and `data-act` |
| Which screens exist, their states, nav tab, camera rule | `web/js/screens.js` | no |
| Demo data for every state of every screen | `web/js/fixtures.js` | no (add a fixture when a state is added) |
| Server calls | `web/js/api.js` | no |
| App data (rooms, active room, scan in progress) | `web/js/store.js` | no |
| Small DOM builders (room card, thumbnail, history row, change card) | `web/js/views.js` | classes only |
| Behaviour | `web/js/app.js` | no |

Rules of the markup:

- A screen is `<section class="scr" id="s-<id>">`. Only the current screen has the class `on`.
- A part of a screen that appears only in some states carries `data-show="state state ..."`. The script sets the section's `data-state` and hides the other parts. The state names are exactly the ones listed below.
- Every tap is `data-act="<action>"` (plus `data-id`, `data-room`, `data-scan`, `data-label`... for what it acts on). There are no other event hooks.
- Model-written text (object names, answers) is inserted as text, never as markup.
- The bottom bar is `<nav id="nav">`, built from `NAV` in `screens.js` (Rooms, Find, Scan, Changes, Settings; Scan is the one raised accent control); it shows on screens whose registry entry has a tab. The camera screens, the wizard, the review, the intro and the primer have no bar.
- The room switcher is a sheet (`#s-switcher`) over any screen; the pill that opens it (`.roomsw`) sits in the top bar of Home, Room, Find, Changes and Settings.

Design tokens (names only, values are in `theme.css`, the visual system): surfaces `--bg --surface --card --card-2`, fills `--fill-1 --fill-2 --fill-3`, `--panel --panel-solid --scrim`, text `--ink --dim --faint`, lines `--line --line-strong`, the one accent `--accent --accent-ink --accent-fill --accent-line` (blue on the phone, amber on the laptop via `data-theme="laptop"`), status `--green --red --amber` each with `-fill` and `-line`, type `--t-label ... --t-num-xl` (Sora for words, JetBrains Mono for numbers and labels), radii `--r-xs --r-sm --r-md --r-lg --r-xl --r-pill`, space `--s-1 ... --s-7`, sizes `--tap --tap-min --btn-h --field-h --bar-h --top-h`, motion `--ease --ease-inout --dur-1 --dur-2 --dur-3 --dur-ring`. Names from before the visual system (`--on-accent --danger-fill --r-btn --r-card --r-ctl --fs-* --nav-h --pad --gap`) are kept and map onto the new values.

Previewing a state without a server or a phone: `/?demo&screen=<id>&state=<state>`, for example `/?demo&screen=find&state=all-results`. The same render code runs as in the real app, fed from `fixtures.js`. `/?demo` alone opens Home.

## Concepts the screens express

- **Room** (a "space"): a name the user chose (Living Room, Desk, Wardrobe...). A room's memory is its latest **saved** scan; older saved scans are kept only for What changed. Rooms never share objects, overlay, guidance or comparison.
- **Active room**: the room the user explicitly chose. It is the default for Find, Explore, What changed and the Reality API. Creating a room does not make it active.
- **Scan states**: a new scan is a *draft* until the user reviews it and presses Save. A draft is not memory. Discard deletes it.
- **Directions, not distances**: every position is a compass direction (yaw) and an elevation (pitch). Nothing on any screen is a distance; changes show an angular shift only.
- **Not found is not "not there"**: a negative answer always says how much of that room the scan covered.

## Data shapes the screens receive

```text
Room        {id, name, active, n_objects, n_scans, n_drafts, last_scanned (epoch s | null), coverage_pct, scan_id (the memory scan | null)}
Scan        {id, status: "saved" | "draft", baseline (0|1), started_at, ended_at, n_objects, n_frames, room_id}
Thumb       {id, label, yaw, pitch, last_seen, room: {id, name, active}, replay: {frame_id, box, width, height} | null}   (picture = /world/frame/<frame_id>.jpg cropped to box)
FindResult  {room_id, room_name, found, speech, concept, object: {id, label, yaw, pitch, n_obs, conf_best, age_s, uncertainty_deg} | null,
             from_scan_start: {dyaw, dpitch}, replay, checked: {scans, covered_deg, scan_id} (when not found)}
             all rooms: {all_rooms: true, groups: [FindResult-like, one per room], found, speech}
Summary     {scan_id, room_id, room_name, status: "draft", speech, coverage: {covered_deg, gaps}, frames: {stored...}, seconds, cost_usd, objects: [{id, label, yaw, pitch, replay}]}
Changes     {status, room, scan_before, scan_after, scans: [{id, started_at, baseline}], summary,
             moved: [{label, key, review, shift: {yaw_deg, pitch_deg, words}, from: {replay}, to: {replay}}],
             removed / added: [{label, key, review, speech, replay}], unverified: [{label, key, reason}], unchanged_count}   review: "confirmed" | "dismissed" | null
```

Every list screen has the same set of non-happy states: `loading`, `error` (the server answered with an error), `offline` (not reachable, reconnecting by itself) and `slow` (no answer yet). Where a state would add nothing (a sheet, a form), it is left out.

## Screens

### `welcome`

First-run intro: three short cards (what Reality OS does, how to scan, what happens to your data), Back / Next / Skip, and the last button "Allow camera and motion". Shown once on the first visit; replayable from Settings. No camera.

| State | Shows |
|---|---|
| `default` | the current card, progress dots, buttons |

- Data: the three intro cards.
- API: none.

### `primer`

Explains the browser's camera and motion prompts before they appear; its Continue button is the only thing that triggers them. A refusal is explained here with a retry.

| State | Shows |
|---|---|
| `default` | what Camera and Motion sensors are for, the microphone note, Continue / Not now |
| `denied` | the same, plus a plain explanation of what was refused and Try again |

- Data: the permission explanations; the reason a permission was refused.
- API: none.

### `home`

Rooms home. Brand, room switcher, server status pill, search over all rooms' objects with room filter chips, Recent objects, a card per room (name, objects, coverage, last scanned, active badge), "Add a room". The camera is never on here.

| State | Shows |
|---|---|
| `default` | search, filter chips, Recent, room cards, add room |
| `empty` | no rooms yet, "Add your first room" |
| `loading` | skeleton cards |
| `error` | notice with the server's error and Retry |
| `offline` | notice: server not reachable, reconnecting, Retry |
| `slow` | notice: the server is slow to answer, skeleton cards |

- Data: rooms with stats, recent objects with thumbnails, search results grouped per room, server status (`Connected · API key valid` and the other key states).
- API: `GET /api/rooms`, `GET /api/search?q=&room_id=`, `GET /api/status`.
- Taps: room card opens Room details; a thumbnail opens that object (another room asks to switch first); filter chip limits search and cards to one room.

### `wizard`

Scan wizard, step 1: choose which room to scan (rescan an existing one, the active room preselected) or name a new one (with suggestion chips including Desk, Shelf, Wardrobe), and whether it becomes the active room. Continue goes to the live scan.

| State | Shows |
|---|---|
| `new` | name field and suggestion chips (no rooms exist yet, or a name is being typed) |
| `rescan` | the existing rooms as chips, plus the name field for a new room |
| `error` | a message: the name is taken, empty, or nothing was chosen |

- Data: existing rooms, name suggestions not already used, the active-room choice.
- API: `POST /api/rooms`, `PATCH /api/rooms/{id}`.

### `scan`

Live scan, with the camera on. A checklist first (light reading from the camera, "room is empty", stand upright), then the coverage compass: a ring of yaw bins (what was looked at), the camera's view cone, three pitch bands (ceiling, eye level, floor) at the side, a progress percentage, the coaching hint (rotate slower, tilt up, area not covered yet...), frame and object counts. Cancel asks before discarding. It shows coverage, not where things are.

| State | Shows |
|---|---|
| `ready` | the pre-scan checklist, Cancel, Start scanning. When the room already has a saved scan (a rescan): that scan's first keyframe as a faded outline (30%) over the camera and a line saying how to line up ("Turn left 8 degrees, move slightly right.", "Lined up. Start scanning."); Start reads "Start anyway" until lined up and is never disabled |
| `live` | hint, compass and percentage, stats, Cancel, Done |

- Data: the room being scanned, light level, coverage bins and pitch bands, current direction and field of view, coaching hint, frame and object counts.
- API: `WS /ws scan_start{room_id} / scan_end / scan_cancel`, `GET /api/rooms/{id}/reference`, `WS /ws live frame (flag 4) answered with align_hint`.

How the lining up works: the phone sends a small live frame about twice a second; the server matches it with the first scan's keyframe (SIFT, RANSAC) and answers `align_hint {ok, within, words, turn_deg, tilt_deg, roll_deg, move, parallax_deg, inliers}`. Turn, tilt and roll come from the recovered rotation; "move slightly left / right / forward / back" from the direction of the recovered translation (its size is unknown, so only a direction is given). Within means turn, tilt and roll are each under 4 degrees and the remaining parallax is under 1.5 degrees. If the live view shares nothing with the keyframe the line says to point at the faded outline.

### `summary`

Scan summary, then review. While frames are read: real progress, and after a stall a way out. Then objects grouped by kind with thumbnails (the biggest kinds, then one tappable "Others" tile), counts, and Save / Discard / View in room.

| State | Shows |
|---|---|
| `processing` | progress bar and "Reading frames: n of m" |
| `stalled` | the same in warning colour, "Check the server" and "Go home" |
| `review` | the sentence, grouped tiles (tap one to see its objects), frames, seconds, coverage, cost, Discard / Save / View in room |
| `empty` | nothing was found: the sentence and Discard (Save is disabled), View in room |

- Data: progress (frames answered of frames sent), the summary with objects and thumbnails.
- API: `WS scan_summary`, `POST /api/rooms/{id}/scans/{scan}/save`, `POST /api/rooms/{id}/scans/{scan}/discard`, `GET /api/status`.

### `room`

Room details. Name and active badge; Find in this room, Rescan, View changes, Explore (AR), Make active, Room settings; info (objects, coverage, last scan, saved scans); recent objects; scan history with a mark for the memory scan, the baseline and an unsaved draft, and per-scan Save / Discard / Set baseline / Delete.

| State | Shows |
|---|---|
| `default` | everything above |
| `empty` | the room has no saved scan: "Not scanned yet" with Scan this room (history shows any draft) |
| `loading` | skeleton |
| `error` | what failed and Try again |

- Data: the room with stats, recent objects with thumbnails, the scan history.
- API: `GET /api/rooms/{id}`, `GET /api/rooms/{id}/scans`, `PATCH /api/rooms/{id}`, `POST /api/rooms/{id}/baseline`, `DELETE /api/rooms/{id}/scans/{scan}`, `POST /api/rooms/{id}/scans/{scan}/save`, `POST /api/rooms/{id}/scans/{scan}/discard`.

### `find`

Find in the active room (or any room named from Room details), or in all rooms (read-only, results grouped per room, each opened only after switching to that room). Text field and microphone, and a Quick ask button that opens a sheet of what the room contains. The camera is off.

| State | Shows |
|---|---|
| `idle` | scope toggle (This room / All rooms), Quick ask, the input |
| `busy` | "Looking for …" with Cancel |
| `slow` | "Still looking, slower than usual" with Cancel |
| `error` | what went wrong with Try again |
| `offline` | the server is not reachable, Try again |
| `no-room` | no active room: "Choose a room first" |
| `room-empty` | the room has not been scanned: Scan this room |
| `all-results` | one card per room: the object with its picture, or "Not seen, the last scan covered N%", or "Not scanned yet" |
| `all-none` | not seen in any room |

- Data: the active room and what it contains, the scope, per-room results.
- API: `WS find{query, room_id | all_rooms}`, `PATCH /api/rooms/{id} (switch room)`.

### `result`

Find result: the stored photo with the object's box, the answer in words, direction (yaw, pitch), confidence and last seen, and Guide me. A negative answer explains what was and was not checked. Never a distance.

| State | Shows |
|---|---|
| `found` | photo, answer, direction dial, yaw / pitch / confidence, Guide me |
| `notfound` | the answer and how much of this room the last scan covered; Search all rooms, Scan this room, Find something else |
| `notscanned` | this room has no saved scan; Scan this room, Find something else |

- Data: the find result and its stored photo.
- API: `WS find_result`, `GET /world/frame/{id}.jpg`, `POST /api/guide`.
- Guide me for an object in another room asks to switch room first; the server refuses guidance across rooms (HTTP 400).

### `guide`

Guided search, camera on. An arrow toward the stored direction with a cone for how uncertain that direction is, the angle left to go, a hint ("Turn left"), the direction in words ("About 60° to your left, 10° down"), confidence and last seen. Never a distance.

| State | Shows |
|---|---|
| `default` | arrow, degrees to go, hint, words, confidence line |
| `found` | "Found it", the arrow hidden, Done |

- Data: the target direction, the live phone direction, the confidence and age.
- API: `POST /api/guide`.

### `changes`

What changed in one room: a Before and an After picker over the room's saved scans (default: the latest against its baseline, else the one before), an **alignment** card (how well the two scans lined up from their pictures: good, weak or failed, with the matches and the start-heading difference), then moved / possibly moved / new / removed with before and after pictures and an angular shift ("moved 40° to the right"), a "Not checked" list for directions only one scan looked at, and Confirm / Dismiss per change. Scans of different rooms are never compared.

| State | Shows |
|---|---|
| `default` | pickers, the summary sentence, the alignment card, grouped change cards (Moved, Possibly moved, New, Removed), Can't compare |
| `loading` | comparing |
| `need-two` | the room has fewer than two saved scans; Scan this room |
| `error` | what failed, Try again |
| `offline` | the server is not reachable, Try again |
| `slow` | still comparing, slower than usual |
| `no-room` | no active room: Choose a room |
| `nothing-changed` | the pickers and "Nothing changed." |

- Data: the room's saved scans, the comparison, review marks.
- API: `GET /api/rooms/{id}/changes?before=&after=`, `POST /api/rooms/{id}/changes/review`.

### `settings`

Global settings: voice language, keyframes per second (1, 2, 3), overlay detail (Calm, Normal or Debug, see below), caregiver sharing (off by default; when on, the caregiver view on the laptop dashboard shows where watched things were last seen, `GET /api/care`, `POST /api/care/consent`), Your rooms (Edit opens Room settings), Replay intro, Delete all data (confirms), and the debug HUD switch (only with `?debug` in the URL).

| State | Shows |
|---|---|
| `default` | all groups, the rooms list |
| `loading` | skeleton in Your rooms |
| `error` | the rooms could not be loaded, Try again |

- Data: the preferences (kept on the phone) and the rooms.
- API: `GET /api/rooms`, `DELETE /api/data`.

### `roomsettings`

One room's settings: rename (a taken name is an error), make it the active room, delete it. The delete warning says what is erased: scans, remembered objects, photos.

| State | Shows |
|---|---|
| `default` | name field, active room, delete |
| `error` | a message under the name field |

- Data: the room and what deleting it would erase.
- API: `PATCH /api/rooms/{id}`, `DELETE /api/rooms/{id}`.

### `explore`

Explore (AR), opened from Room details: the room's remembered objects drawn over the live camera, locked to where they were, as much as Settings, Overlay detail allows (see below). Camera on.

| State | Shows |
|---|---|
| `default` | the room name, the AR toggle, Note / Ask / Rewind, a hint; bars for the answer being shown and for the earlier scan being shown, each with Clear |
| `no-memory` | the room has not been scanned: nothing to draw; Scan this room |

Over the camera, besides the room's objects: the room's **notes** (an amber dot with the text, where they were left), the objects an **answer** names (outlined and labelled with a reason; everything else is hidden while an answer is shown; an arrow points to one that is off screen), and the **ghosts** of a **rewind** (dashed rings with "was here" for what an earlier scan had and the room's memory does not).

- Data: the room's overlay objects (direction, outline, age, uncertainty) and its open notes.
- API: `GET /api/overlay?room_id=`.

### `labels`

Objects review, step 1 (opened with **Review objects** on the scan review, or **Objects** on a scan in a room's history): a scan's objects grouped by label with instances, sightings and mean confidence, the labels hidden as "not an object" in this room (with Restore), and how many possible duplicates there are. This is the simple phone version of the laptop dashboard's inspector.

| State | Shows |
|---|---|
| `default` | one row per label: "3 × · 9 sightings · 82%", the hidden labels, the duplicates note |
| `empty` | the scan has no objects (nothing recognised, or every kind hidden) |
| `loading` | skeleton rows |
| `error` | what failed, Try again |
| `offline` | the server is not reachable, Try again |

- Data: the scan's grouped objects, hidden labels, duplicate count.
- API: `GET /api/scans/{id}/objects`, `GET /api/scans/{id}/duplicates`, `POST /api/rooms/{id}/not-objects`, `DELETE /api/rooms/{id}/not-objects/{label}`.

### `instances`

Objects review, step 2: the instances of one label, each with a thumbnail, its sightings and mean confidence. Tick two or more and Merge selected; "not an object here" hides the whole label in this room (in every scan, now and in future scans). Tapping an instance opens its sightings. Every correction is recorded by the server.

| State | Shows |
|---|---|
| `default` | the instance rows with a pick box each, Merge selected (n), Not an object here |
| `empty` | the label has no instances any more |
| `loading` | skeleton rows |
| `error` | what failed, Try again |

- Data: the instances of one label (id, sightings, confidence, direction, thumbnail), the picked ones.
- API: `POST /api/objects/merge`, `POST /api/rooms/{id}/not-objects`, `DELETE /api/objects/{id}`.

### `sightings`

Objects review, step 3: one instance and every sighting that made it: the stored picture with the object's box, the SAM confidence, the camera's direction. Detach turns a sighting into an object of its own; Delete object ignores all its sightings from now on.

| State | Shows |
|---|---|
| `default` | direction, uncertainty, sightings, confidence, a card per sighting, Delete this object |
| `loading` | skeleton |
| `error` | what failed, Try again |

- Data: the instance and its sightings (picture, box, confidence, camera direction).
- API: `GET /api/objects/{id}`, `POST /api/sightings/{id}/detach`, `DELETE /api/objects/{id}`.

### `quick`

Quick ask: a bottom sheet over Find listing what the room contains as tap-to-ask questions ("Where is my keys?" rows with a chevron; it replaces a row of chips on the Find screen). Choosing one runs the search.

| State | Shows |
|---|---|
| `default` | the names as chips, Close |
| `empty` | "This room has no objects to ask about yet" |

- Data: what the room contains.
- API: `WS find{query, room_id | all_rooms}`.

### `note`

Notes: a bottom sheet over Explore. You leave a message in the room at the place you are looking: the object under the reticle (the note then stays on it) or the direction you face. Typed or spoken. The sheet also lists the room's open notes with Done and Delete. Notes belong to one room; they are shown over the camera, in that room, to everyone who uses it.

| State | Shows |
|---|---|
| `default` | where the note will be left, the text field with the microphone and a save button, the room's notes |
| `empty` | the same, with "No notes in this room yet" |

- Data: the target of the note, the room's open notes.
- API: `GET /api/rooms/{id}/notes`, `POST /api/rooms/{id}/notes`, `PATCH /api/notes/{id}`, `DELETE /api/notes/{id}`.

### `ask`

Ask the room: a bottom sheet over Explore. A question in plain words ("What is charging?", "When did something last move?"), typed or spoken. The answer is read aloud, shown in a bar, and the objects it names are drawn over the camera with a reason each. It reasons over the names and directions the room remembers and what changed between its saved scans; photos are never sent.

| State | Shows |
|---|---|
| `idle` | the field with the microphone, example questions |
| `busy` | "Thinking about this room…" |
| `error` | why it failed, and the examples to try again |

- Data: the question, the answer, the matching objects with reasons.
- API: `POST /api/rooms/{id}/ask`.

### `history`

Rewind: a bottom sheet over Explore listing the room's earlier saved scans. Choosing one shows, over the camera, what was there then and is gone or moved now (ghosts), aligned into today's directions. Directions the current scan never looked at are shown as "was here?", not as gone.

| State | Shows |
|---|---|
| `default` | the earlier saved scans, newest first, with objects and frames |
| `empty` | "Nothing earlier to rewind to" (only one saved scan) |
| `loading` | skeleton rows |
| `error` | why the scans could not be loaded, and Try again |

- Data: the room's saved scans; the ghosts of the chosen one.
- API: `GET /api/rooms/{id}/scans`, `GET /api/rooms/{id}/rewind?scan=`.

### `switcher`

Room switcher: a sheet listing the rooms with the active one marked. Choosing one makes it the active room.

| State | Shows |
|---|---|
| `default` | the room list, Add a room, Close |
| `empty` | "No rooms yet" and Add a room |

- Data: the rooms.
- API: `GET /api/rooms`, `PATCH /api/rooms/{id}`.

## Overlay detail (what the camera screens draw)

One setting, three modes, all thresholds in `web/js/overlay-config.js`; the choices are pure functions in `web/js/calm.js` and `web/js/simplify.js`.

- **Calm (default).** At most five labels, picked by confidence × centrality × recency, kept apart so none overlaps another, with duplicates grouped ("3 bottles"). One outline only: the Find / Guide target, else the object under the reticle (its mask simplified and rounded). Objects the detector is unsure of, whose direction is uncertain, or that are background classes (wall, floor, ceiling, door, window...) are not shown. The compass tape, horizon and pitch ladder appear only on the Guide screen.
- **Normal.** Every reliable object with its label and outline, and the compass tape, as before.
- **Debug.** Normal plus confidence and uncertainty on each label.

## Dialogs, toasts and captions (not screens)

- Confirm dialog (`#dlg`): used before discarding a scan, deleting a scan, a room or all data, and before switching room to open a result in another room. Title, one or two sentences, for the destructive ones a list (`#dlgitems`) of what is erased with counts (scans, objects, photos), a safe button and the confirming button (the only solid red).
- Toast (`#toast`): short confirmations and errors.
- Caption bar (`#cap`): every spoken reply is also shown as text (screens that already print the reply do not need it).
- Camera indicator: on every camera screen a "Camera on · STOP" pill is always visible; Stop turns the camera off.

## Touch rules

Every button, chip, field and tab is at least 44 px in both directions (`--tap` is 48). A test opens every screen and fails on a smaller target.
