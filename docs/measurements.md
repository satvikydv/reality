# Measurements

Every number here comes from a script in `scripts/` run on real data. Raw JSON lands in `data/results/` (gitignored); paste the printed tables below with the date and setup.

Status legend: **TODO** not run yet · **DONE** measured.

## M0 checklist

| Item | Script | Status |
|---|---|---|
| Phone camera over HTTPS + WS echo | `backend/run.py` | server side tested (localhost + LAN IP); **phone not yet tested** |
| SAM keyframe latency / matches | `scripts/sam_latency.py` | **DONE (first pass)** 2026-10-28, 4 frames, 960 px only |
| Orientation drift, absolute vs relative | `/orientation` page | **DONE** 2026-10-28, 3 trials: both sources identical, <0.5 deg |
| Scan pipeline (Spark names -> SAM -> World Model) | `scripts/scan_frames.py` | **DONE (first pass)** 2026-10-28, 4 frames |
| Spark box grounding vs SAM | `scripts/spark_grounding.py` | TODO |
| Spark structured plan | `scripts/spark_plan.py` | TODO |
| Voice realtime from mic | `scripts/voice_ws.py` | TODO |

## [VERIFY] items to close in M0

- [x] SAM stream event shape works with `meta-sam-parser` + `openai` SDK `AsyncStream` (2026-10-28: 37 rows, 0 errors)
- [x] `metadata` (`mask_encoding`, `include_confidence`) accepted on `/v1/responses` for `sam-3.1` (no errors; confidences returned)
- [ ] Spark native box coordinate convention (script scores pixels / 0-1000 / 0-1)
- [ ] Spark strict `json_schema` accepts the recursive tagged-union plan schema (else fall back to JSON mode)
- [ ] Voice handshake JSON shape (flat, as assumed by `scripts/voice_ws.py`) and event field names (`text` vs `transcript`)
- [x] `reasoning.effort: "low"` accepted on `muse-spark-1.3` (2026-10-28, `resolve_queries.py`); other values untested
- [x] Spark strict `json_schema` via the Responses API works for a flat schema (concept/alternates/confidence); the recursive plan schema is still untested

## Phone first run, rooms and navigation: manual checklist

Status: **not run on a phone yet.** The page logic is covered by `tests/test_entry_flow.py` (headless Chrome over the DevTools protocol with a canvas as the camera: the entry rule, the camera lifecycle, every screen in every state, the room flows against a real server) and `tests/js/`, which cannot show how a real Android or iOS browser behaves. Work through this on the phone, tick what holds, and write what does not under the item. Setup: `PLAIN_HTTP=1`, `adb reverse tcp:8000 tcp:8000`, inspect the page from `chrome://inspect` to read `__ui.session().state` (the camera, wake lock and portrait lock as the page sees them). To repeat a first run: Chrome, site settings, clear data for localhost (or use a private tab). Use two rooms that really look different (for example your desk and a wardrobe) for the room items.

Record: device ______ · browser and version ______ · date ______

**Nothing starts by itself**
- [ ] First visit (cleared data): the intro card appears. No permission prompt, no camera indicator in the status bar, no camera picture behind it.
- [ ] Returning visit: lands on Home (the rooms list) on a plain dark background. No prompt, no camera indicator. In the inspector `__ui.session().state.camera` is `"off"`.
- [ ] Wait 30 s on Home, Room details, Find and Changes: still no camera indicator, no prompt.
- [ ] Scan in the bottom bar opens the wizard (name or pick a room) with the camera still off; the camera starts only after Continue to scan.

**Intro and permissions**
- [ ] Three cards: what it does, how to scan, your data. Back, Next and Skip work. The last button says "Allow camera and motion".
- [ ] Tapping it shows the primer. Still no browser prompt. Text names Camera and Motion sensors and says the microphone is only for the mic button.
- [ ] Continue: the browser asks for the camera (and, on iPhone, motion first). Allow: lands on Home with "Allowed".
- [ ] Reload: no intro, no primer. Settings, Replay intro: the cards show, Skip or Done returns to Settings, a reload still shows no intro.
- [ ] Block the camera (tap Block, or site settings), then Scan, name a room, Continue: the primer shows "Camera access was blocked" with Try again. Unblock, Try again: the scan screen opens.
- [ ] iPhone only: deny motion: "Motion access was blocked" with the steps; allow on retry.
- [ ] Android: does motion need any prompt? Note what happened (and whether the compass tape moves).

**Rooms**
- [ ] No rooms yet: Home says "No rooms yet" with Add your first room. The room switcher pill says "No room".
- [ ] Scan, type a name or tap a suggestion (Desk, Shelf, Wardrobe), keep "Use this as my active room" on, Continue to scan: the room is created and the switcher pill shows its name.
- [ ] A second room with the same name (any capitalisation) is refused with a message, and the camera does not start.
- [ ] Scan the first room (below). Home shows its card: object count, coverage, "just now" or minutes ago, ACTIVE badge. A room never scanned says "Not scanned yet".
- [ ] Add and scan a second room. Switcher: pick it; the pill changes and the Find placeholder names it. The first room is no longer ACTIVE.
- [ ] Isolation: in the second room, Find something that only the first room has: "not seen", with the coverage of the second room, never the first room's object. Find in a room you have not scanned: "has not been scanned yet".
- [ ] Find with All rooms: the object shows under the first room only; the second room says "Not seen" with its coverage. Open the result: it asks to switch room; Not now stays, Switch room makes it active and opens the result.
- [ ] Guide me on a result in the active room works. (Cross-room guidance cannot be started from the phone: the switch comes first.)
- [ ] Room details: Find in this room, Rescan, View changes, Explore (AR), Room settings all open the right screen for that room, including a room that is not active (Make active appears only for those).
- [ ] Room settings: rename (a taken name is refused), Delete room shows the warning with how many scans and objects, Keep it cancels, Delete room removes the room; the other room is untouched and Home's card is gone. Check the laptop: `data/world_frames/<scan id>/` folders of the deleted room are gone.
- [ ] Home search: type an object name; results appear under their rooms; the room chips limit them; a name no room has says "No object called ...".

**Scanning and the review**
- [ ] Scan, pick a room, Continue to scan: the camera opens, the red "Camera on · STOP" pill shows, the checklist shows. The light line reads fine in a lit room. Cover the lens: it reads dark within about a second.
- [ ] Start scanning. The compass fills green where you have looked, the view cone follows the phone and is about as wide as the camera's view, and the three bars at the side (ceiling, eye level, floor) fill as you tilt. The percentage rises; the frame count matches the server log.
- [ ] Hints: turn faster than a lap in 10 s: "Rotate slower". Point at the floor: "Tilt up a little". Point at the ceiling: "Tilt down a little". Skip a part of the room: once past half, "Area not covered yet: N° to the left (right)". Hold the phone sideways: "Hold the phone upright".
- [ ] Cancel during a scan: "Discard this scan?" Keep scanning continues; Discard goes Home and the scan is not in the room's history.
- [ ] Done: the processing screen shows "Reading frames: n of m" and a bar that moves, then the review: the sentence, tiles grouped by kind with picture crops, an "Others" tile when there are more than six kinds (tap it, and a normal tile: their objects show below), frames, seconds, coverage, cost.
- [ ] Save: the room page opens, the scan is MEMORY, and Find in that room now finds its objects. Scan the same room again and Discard: the room's memory is unchanged.
- [ ] Scan again and choose View in room instead: the scan sits in the history marked UNSAVED and Find does not use it. Save it from there, then confirm Find sees it.
- [ ] Done, then break the link (stop `adb reverse` or the server): after about 30 s "No answer from the server for a while" with Check the server and Go home. Restore the link, Check the server: the review appears from the stored scan.

**Changes**
- [ ] With only one saved scan in the room: Changes says "Nothing to compare yet" with Scan this room.
- [ ] Move one thing (a mug, a book) and rescan, Save. Changes: the two scans are in the Before and After lists; the mug shows as moved with an angle ("40° to the right"), before and after pictures, no distance anywhere. Directions only one scan covered are under Not checked.
- [ ] Confirm and Dismiss on a change survive leaving and reopening the screen. Pick other scans in the lists; Set baseline on an old scan in Room details makes it the default Before.
- [ ] Two scans in different rooms can never be compared (there is no way to pick one from another room).

**Camera lifecycle**
- [ ] STOP (or Cancel on the checklist): back where you were, the status-bar camera indicator is gone within a second.
- [ ] Explore (AR) from Room details: camera on, the remembered objects drawn over the camera, Back turns it off. For a room never scanned it says there is nothing to show and offers a scan. Settings, Overlay detail, Labels only: the outlines disappear and the labels stay.
- [ ] Guide me: camera on; the screen shows the arrow, the angle to go, the direction in words ("About 60° to your left, 10° down"), confidence and last seen, no distance. END or Done turns the camera off.
- [ ] Switch to another app, or to another tab, while on a camera screen: the indicator goes out at once. Coming back shows Home with "The camera was turned off because the page was hidden". The camera does not restart by itself.
- [ ] During a scan, press the power button: the scan stops, the objects it found are kept as an unsaved scan in the room, and a new scan can be started afterwards.
- [ ] Wake lock: set the phone's screen timeout to 15 s. On a camera screen the screen stays on past 15 s. After STOP it dims again. Record `state.wake`.
- [ ] Portrait lock: record `state.portrait`. If it is `false` (expected on Chrome outside fullscreen), rotate to landscape during a scan and record what happens ("Hold the phone upright", or the camera guard stopping the scan).
- [ ] Open the phone's own camera app during a scan: the scan stops with "The camera stopped".

**Home, errors and captions**
- [ ] The pill reads "Connected · API key valid". Record what it reads with the real Meta key (the key check lists models; if the API refuses that call the pill says "API key set").
- [ ] Stop the server: within about 15 s the pill turns red "Server offline"; the rooms already loaded stay. Start the server: it recovers within 10 s without a reload. With a fresh page load while the server is down, Home shows the offline notice with Retry.
- [ ] Slow answer (block the model API on the laptop): Find shows "Still looking" after 8 s, then "No answer from the server" with Try again after 30 s. Cancel in between works.
- [ ] Every spoken reply is also on screen (result, review, changes, guide hint, and the caption bar elsewhere). Mute the phone: everything still works by reading.
- [ ] Typing instead of speaking works everywhere. Mic button: deny the microphone: "Microphone access is blocked. Type your question instead" and the text box is focused. Allow it: speak "where is my keys". Change the voice language in Settings and speak in it.

**Settings**
- [ ] Keyframes per second 1 against 3: scan 30 s each and record the frame counts: 1/s ____ , 3/s ____ .
- [ ] Voice language and overlay detail survive a reload.
- [ ] Delete all data: the dialog says how many rooms; Keep my data cancels; Delete everything empties Home. Check `data/world_frames/` on the laptop is empty.
- [ ] No dots menu and no Debug HUD row on a normal URL. With `/?debug` both exist and the toggle works.

**Objects review and calm overlay**
- [ ] Review a real scan: Review objects lists the kinds with sensible counts. Open a kind with many instances (bottles, cables): do the thumbnails look like the same thing? Merge two that are one object; the count drops and a rescan of the room does not undo it (corrections are kept, but a new scan is a new scan).
- [ ] Detach one sighting that is clearly a different object; Delete an object that is not real; mark a background kind (for example "wall") as not an object: it leaves the room's overlay, and Restore brings it back. Check the laptop dashboard, Objects, Corrections log lists each action with "by phone".
- [ ] Dashboard Objects: sighting pictures are large enough to judge the mask and box; the decision log explains why two sightings were merged or kept apart.
- [ ] Calm is the default: in Explore at least three labels at a time and never more than five, no label on top of another, duplicates read "3 bottles", one outline only, no compass tape. Guide shows the tape. Settings, Overlay detail, Normal and Debug show the old, busier overlay.
- [ ] Walk around a cluttered shelf with Calm on: does the one outline follow the object you point at? Does the label choice feel right? Note any label you expected and did not get, and any that jumped between frames.
- [ ] Frame rate: dev menu (`?debug`) fps and draw ms in Calm, Normal and Debug on the same view: Calm ____ , Normal ____ , Debug ____ .

**Notes, Ask the room, Rewind, caregiver view**
- [ ] Explore a scanned room. Tap Note with the reticle on an object and leave a note by typing, then by voice. The amber dot and text appear on that object; turn away and back: it stays. Open the same room on the laptop browser (`/` with `?demo` is not enough: use the real page) and check the note shows there too. Mark it done: it disappears on both.
- [ ] Ask: "What is charging?", "What here is fragile?", "What should I take with me when I leave?". Do the highlighted objects match what you would say? Note how many of 10 questions you judge right ____ / 10, and the time to answer ____ s. Ask something that matches nothing: the answer says so and nothing is drawn.
- [ ] Rewind: scan a room, move or remove one object, scan again and save. In Explore, Rewind to the earlier scan: a dashed "was here" ring appears where the object was. A direction the second scan did not cover shows "was here?".
- [ ] The caregiver demo, in order: Care on the laptop shows the pills, the room and the photo; on the phone Explore the kitchen, Rewind to the earlier scan: a ring where the pills were; Ask "What here might be unsafe?": the stove and anything sharp or hot are drawn, and the answer ends "it is not a safety check"; Note on the stove: "Turn the stove off after cooking"; it shows on the laptop's page too. Time the whole run ____ s. Without the server, `/?demo&screen=explore` and `/dashboard?demo` (Care) play the same story with made-up data.
- [ ] Caregiver: Settings, Caregiver sharing is off. On the dashboard, Care shows the list but no places or photos. Turn sharing on from the phone: places, photos and ages appear within about 8 seconds. Add "pills" with a usual room; scan it in another room and check the "usually lives in" alert.

**Viewpoint-robust alignment and the rescan assist**
- [ ] Rescan assist: scan a desk (this is the reference scan), save it. Tap Scan again, pick the room: the first scan's keyframe appears as a faded outline over the camera and a line says how to line up. Move the phone away: the words change ("Turn left 8 degrees, move slightly right."); line the outline up: "Lined up. Start scanning." and the button reads Start scanning, otherwise Start anyway. Start anyway must always work.
- [ ] Time between the phone showing a view and the words reacting: ____ s (it asks about twice a second).
- [ ] Changes: the alignment card says good, weak or failed with the matching points; nothing is under Moved when it is weak or failed. When it is weak and something moved far (a hand's width at a desk), it may show under "Moved, low confidence" with "check it"; with `MOVED_LOW_CONFIDENCE=0` it never does.
- [ ] Scan quality: after a 3-shot desk scan the review shows "Take a few more shots"; after a slow full pan with the wall in view it shows "A good scan to compare against later". Save works in both.
- [ ] Rescan the desk without moving anything and with only one shot of some object: that object is under Can't compare "seen only once", never under Removed or New.
- [ ] Run the protocol below and fill in the table.

**Upgrade**
- [ ] Start the server on a database from before rooms: Home shows one room, "Unassigned", with the old scans, not active; Find says to choose a room until you pick one.

**Demo**
- [ ] `/?demo` and `/?demo&screen=<id>&state=<state>` for any pair in `docs/screens.md` open with no prompt of any kind.

## Calm overlay and objects inspector: measurements

Measured 2026-10-29. Label counts come from `scripts/export_overlay.py` (a copy of `data/world.sqlite`) and `node scripts/measure_overlay.mjs`, which run the phone's own projection and selection code over 108 viewpoints (yaw every 10 degrees at pitch -15, 0 and 15, FOV 60 degrees, a 390x844 screen with the HUD bands kept free). Before is Normal (the old overlay: every reliable object on screen gets a label and an outline); after is Calm. Object ages are taken relative to the end of the scan, as if viewed right after scanning.

| Scan (objects) | Labels per view, mean / p95 / max, before | after | Outlines per view, before | after | Overlapping label pairs per view, before | after | Views with more than 5 labels |
|---|---|---|---|---|---|---|---|
| 6 (35 objects, 10 kinds) | 5.4 / 18 / 22 | 1.8 / 5 / 5 | 5.4 | 0.3 | 2.9 (max 19) | 0.0 | 43 of 108, then 0 |
| 5 (36 objects, 7 kinds) | 3.8 / 14 / 18 | 1.6 / 5 / 5 | 3.8 | 0.2 | 1.7 (max 10) | 0.0 | 34 of 108, then 0 |
| 4 (64 objects, 10 kinds; stored before masks were kept, so no outlines either way) | 5.9 / 30 / 38 | 1.8 / 5 / 5 | 0.0 | 0.2 | 5.3 (max 49) | 0.0 | 29 of 108, then 0 |

Calm's per-view labels stand for 2.8 to 5.0 objects on average because duplicates are grouped. On these three scans nothing was hidden for low confidence, background class or uncertainty except 12 object-views on scan 5 (uncertainty): the stored SAM confidences are all at least 0.5 and none of the kinds is a background class (the biggest are laptop, cable, bottle, clothing), so on this data the reduction comes from the five-label limit, off-screen objects, grouping and collision avoidance. The confidence and background filters matter when a scan contains wall, floor or low-confidence detections, which these did not.

Frame rate and draw time in headless Chrome (`python scripts/measure_overlay_fps.py`), demo overlay with 16 and 60 objects and a 100 degree FOV so many are in view, median of 3 s after a 1.5 s warm-up. This machine's headless Chrome is capped at 60 fps and is not a phone; the 6x rows throttle the CPU as a rough stand-in for a slower one.

| CPU throttle | Objects | Mode | fps | Draw ms | Labels shown |
|---|---|---|---|---|---|
| 1x | 16 | calm | 60 | 0.36 | 3 |
| 1x | 16 | normal | 60 | 0.64 | 5 |
| 1x | 16 | debug | 60 | 0.84 | 5 |
| 1x | 60 | calm | 60 | 0.57 | 5 |
| 1x | 60 | normal | 60 | 1.36 | 19 |
| 1x | 60 | debug | 60 | 1.68 | 19 |
| 6x | 16 | calm | 59 | 2.49 | 3 |
| 6x | 16 | normal | 29 | 4.54 | 5 |
| 6x | 16 | debug | 31 | 5.24 | 5 |
| 6x | 60 | calm | 60 | 2.76 | 5 |
| 6x | 60 | normal | 19 | 10.05 | 19 |
| 6x | 60 | debug | 20 | 10.23 | 19 |

Calm draws less than half the time of Normal at 1x and about a quarter at 6x with 60 objects, because only the chosen object's mask is projected and the rest are positions only. Not measured on the phone.

## Alignment protocol (docs for `scripts/eval_alignment.py`)

Scan the same desk with a few objects on it (at least four: a mug, a charger, a book, a bottle) five times, each from the middle of a turn, each saved to the same room:

| Scan | How | Truth to record |
|---|---|---|
| Reference | the usual scan, standing in your spot, phone upright | none |
| (a) same spot | stand exactly where you stood, scan again | nothing moved; offset 0 if the compass was used |
| (b) 20 cm sideways | step 20 cm to the side (measure it with a ruler on the floor), scan again | nothing moved; offset 0 |
| (c) start heading rotated 90 degrees | start the scan facing 90 degrees away from where you started the reference (a relative sensor makes the frame differ by 90) | nothing moved; offset 90 (or minus 90) |
| (d) two objects moved | back at your spot, really move two objects (say the mug and the charger by a hand's width or more) | `moved`: their labels; offset 0 |

Find the scan ids with `python scripts/export_overlay.py --db data/world.sqlite --list`, write a manifest (see the top of the script), then:

    python scripts/eval_alignment.py --db data/world.sqlite --manifest manifest.json --json alignment_eval.json

It works on a copy of the database. Report, per scenario: alignment quality, matches, offset and its error against the truth you wrote down; over all scenarios: the yaw-offset error, the false-move rate (objects called moved that you did not move), the true-move recall (objects you moved that are called moved), and how many objects are in "possibly moved" and "can't compare". Targets: yaw-offset error within 1 degree for (a), (c), (d); no false moves in (a) to (c); both moves found in (d); (b) should be mostly unchanged and possibly moved, never moved. If (b) shows moved objects, try `--viewpoint-cm 25` and note which value the nearby objects need: it tells you how far you really stand off.

| Scenario | Alignment | Matches | Offset error | Moved | Possibly moved | Can't compare |
|---|---|---|---|---|---|---|
| (a) same spot | | | | | | |
| (b) 20 cm sideways | | | | | | |
| (c) heading rotated 90 | | | | | | |
| (d) two moved | | | | | | |

Yaw-offset error ____ ; false-move rate ____ ; true-move recall ____ ; possibly moved ____ ; can't compare ____ . Not yet measured on the phone.

What to expect, from the synthetic desk (not a promise for the phone): (a) good alignment, nothing moved, new or removed; a 3-shot rescan may come out weak (then everything that is not clearly unchanged is "can't compare", which is correct). (b) good, no moved; near objects may be "possibly moved", and the alignment card's viewpoint should read about 20 cm (`viewpoint_cm_used`; measured from the objects that stayed put). (c) offset within a few degrees of 90 (synthetic: 0.3 to 2.4 degrees off; the per-shot compass noise shows here), nothing moved. (d) both objects moved, or "moved, low confidence" when the alignment is weak; nothing else moved. Take at least 5 shots per scan, keep the wall behind the desk in view, and check the review's scan quality card before saving. Anything under Moved in (a) to (c) is a false move: note the object, its "local" numbers in `alignment_eval.json`, and whether its box was cut by the frame edge or hidden behind something in one scan.

## Alignment: synthetic check (not the phone)

`python scripts/eval_alignment.py --synthetic` runs two synthetic worlds from `tests/synthetic_world.py`. It checks the code, not the camera: the poses are exact, the objects are rendered boxes, and the boxes are their visible pixels, which is kinder than SAM.

**Room panorama, no depth** (re-run 2026-10-31; the "20 cm sideways (simulated parallax)" scenario of 2026-10-30 is gone: it shifted the objects but not the texture around them, which the local evidence now correctly reads as a move. A real sideways step is in the desk world below):

| Scenario | Alignment | Matches | Pairs | Offset | Offset error | Moved | Possibly moved | Can't compare | Unchanged |
|---|---|---|---|---|---|---|---|---|---|
| same spot | good | 29596 | 51 | 0.0° | 0.0° | – | – | 0 | 4 |
| start heading +90 | good | 29821 | 53 | 90.0° | 0.0° | – | – | 0 | 4 |
| two objects moved | good | 26891 | 51 | -0.0° | 0.0° | book, mug | – | 0 | 2 |

- Yaw-offset error: mean 0.0°, max 0.0° over 3 scenarios with a known offset.
- False-move rate: 0 of 12 objects compared were called moved without being moved (0.0).
- True-move recall: 1.0 (2 of 2 really moved objects were called moved; 0 more only as possibly moved).
- Possibly moved (all scenarios): 0. Can't compare (all scenarios): 0.

The live guidance takes about 70 ms per camera frame on this laptop's CPU (measured 2026-10-30: a 480x640 frame against a stored 720x960 keyframe; the keyframe's features, 43 ms, are computed once per room), so 2 frames a second leaves most of the time free.

**Near-field desk, rendered with depth** (2026-10-31, 5 seeds, desk texture 0.2). A desk 40 to 60 cm below and ahead of the phone, a wall behind, five objects and a laptop whose screen changes every shot; the phone 7 to 20 cm from where the reference was taken plus about 3 cm of hand drift per axis per shot, 3 to 4 shots of 540x960 at 60 degrees, tilted down 34 to 42 degrees, a different exposure, a little blur, a stored tilt bias of +4 and -3 degrees in two rescans, and 1.5 degrees of compass noise per shot. The first two scenarios mirror the real desk scans below; the other three are the protocol on that desk. "Offset error" is against the true compass and includes the simulated per-shot compass noise, which images cannot remove; the comparison is less exposed to it, because every shot is re-anchored through its own image-derived pose and the local evidence uses no pose at all.

| Seed | Scenario | Alignment | Matches | Pairs | Offset error | Moved | Moved (low confidence) | Possibly moved | New | Removed | Can't compare | Unchanged |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | desk, rescan, nothing moved | weak | 646 | 9 | 0.56° | – | – | – | – | – | 2 | 6 |
| 1 | desk, rescan, mouse moved | good | 467 | 8 | 0.71° | mouse | – | – | – | – | 2 | 5 |
| 1 | desk, 20 cm sideways | good | 347 | 9 | 2.97° | – | – | phone, mouse | – | – | 1 | 5 |
| 1 | desk, start heading +90 | good | 701 | 8 | 0.63° | – | – | – | – | – | 1 | 7 |
| 1 | desk, two objects moved | weak | 600 | 8 | 0.79° | – | phone | – | – | – | 3 | 4 |
| 2 | desk, rescan, nothing moved | good | 742 | 9 | 0.09° | – | – | – | – | – | 0 | 6 |
| 2 | desk, rescan, mouse moved | good | 322 | 4 | 3.91° | mouse | – | – | – | – | 1 | 5 |
| 2 | desk, 20 cm sideways | good | 460 | 10 | 0.58° | – | – | – | – | – | 0 | 6 |
| 2 | desk, start heading +90 | good | 733 | 9 | 1.25° | – | – | – | – | – | 0 | 6 |
| 2 | desk, two objects moved | good | 815 | 11 | 0.79° | phone, mouse | – | – | – | – | 0 | 4 |
| 3 | desk, rescan, nothing moved | weak | 831 | 11 | 1.03° | – | – | – | – | – | 0 | 6 |
| 3 | desk, rescan, mouse moved | good | 461 | 8 | 0.06° | – | – | mouse | – | – | 1 | 5 |
| 3 | desk, 20 cm sideways | good | 500 | 10 | 0.61° | – | – | mouse | – | – | 0 | 5 |
| 3 | desk, start heading +90 | good | 802 | 10 | 0.34° | – | – | – | – | – | 0 | 6 |
| 3 | desk, two objects moved | good | 1097 | 12 | 0.41° | phone, mouse | – | – | – | – | 0 | 4 |
| 4 | desk, rescan, nothing moved | good | 582 | 7 | 1.85° | – | – | – | – | – | 0 | 6 |
| 4 | desk, rescan, mouse moved | good | 401 | 6 | 0.76° | – | – | mouse | – | – | 0 | 5 |
| 4 | desk, 20 cm sideways | good | 455 | 10 | 0.87° | – | – | phone | – | – | 1 | 5 |
| 4 | desk, start heading +90 | good | 782 | 9 | 0.98° | – | – | – | – | – | 0 | 6 |
| 4 | desk, two objects moved | good | 607 | 9 | 1.84° | phone, mouse | – | – | – | – | 0 | 4 |
| 5 | desk, rescan, nothing moved | good | 461 | 6 | 4.71° | – | – | – | – | – | 1 | 6 |
| 5 | desk, rescan, mouse moved | good | 580 | 8 | 0.74° | mouse | – | – | – | – | 1 | 5 |
| 5 | desk, 20 cm sideways | good | 443 | 9 | 2.71° | – | – | mouse | – | – | 1 | 6 |
| 5 | desk, start heading +90 | good | 683 | 9 | 2.38° | – | – | – | – | – | 1 | 6 |
| 5 | desk, two objects moved | good | 811 | 10 | 0.39° | phone, mouse | – | – | – | – | 1 | 4 |

- Alignment over 25 scenario runs: good 22, weak 3, failed 0.
- Yaw-offset error: mean 1.28°, max 4.71°.
- False moves: 0 of 152 objects compared. False new: 0. False removed: 0.
- True moves: 11 of 15 called moved, 1 more as moved (low confidence), 2 only as possibly moved.
- Possibly moved: 7. Can't compare: 17.

**Before and after on the same worlds** (10 seeds x 5 scenarios = 50 scenario runs and 30 true moves per texture level; "before" is the code at commit 124c694 run on the same rendered scans; the plainer desk, 0.1, is harder than the real one, the busier, 0.5, easier):

| Desk texture | Code | Alignment good / weak / failed | True moves: moved | moved, low confidence | possibly moved | False moves | False new | False removed |
|---|---|---|---|---|---|---|---|---|
| 0.1 | before | 1 / 43 / 6 | 0 | – | 0 | 0 | 0 | 0 |
| 0.1 | after | 36 / 14 / 0 | 15 | 3 | 4 | 0 | 0 | 0 |
| 0.2 | before | 10 / 39 / 1 | 3 | – | 5 | 1 | 3 | 0 |
| 0.2 | after | 39 / 11 / 0 | 19 | 5 | 3 | 0 | 0 | 0 |
| 0.5 | before | 27 / 23 / 0 | 6 | – | 7 | 0 | 4 | 4 |
| 0.5 | after | 39 / 11 / 0 | 21 | 2 | 1 | 0 | 0 | 0 |

The mirror of the real case (desk 0.2, "mouse moved", 10 seeds): before, 9 weak and 1 failed alignment and the mouse found 0 times; after, 8 good and 2 weak, the mouse called moved 5 times, moved (low confidence) once, possibly moved 3 times and missed once (can't compare). Remaining weak results are mostly 3-shot rescans whose standard error (counted over distinct shots) is just over 1.5 degrees; that is thin evidence, and the scan quality card now asks for at least 5 shots.

What changed and why: `docs/decisions.md`, 2026-10-31. How the static and moved objects separate in the local evidence (80 static object comparisons: at most 0.62 of the object's own size; moved: 2.05 or more) is in the same entry.

## Near-field and room scans on the phone (reported by the owner, not re-measured here)

The scans are in the owner's `data/` (a person's room), not in the repo, so these numbers are as reported on 2026-10-30/31 and could not be reproduced here; the synthetic desk above was built to mirror them.

- **Desk, three scans** (phone, water bottle, paper cup, laptop, mouse, cables at about 40 to 60 cm; 3 to 4 frames of 540x960 each at an assumed 60 degree FOV; tilted down 35 to 45 degrees; mean pitch -42, -37, -34 per scan). Reference against an unmoved rescan: 1 of 12 candidate frame pairs matched, 84 inliers, offset -0.49 degrees (truth 0), parallax 0.35 degrees, quality weak. Reference against a rescan with the mouse moved (yaw/pitch 39/-43 to 23/-16): 1 of 12 pairs, 85 inliers, offset -0.69, weak; the mouse was not reported (true-move recall 0 of 1), no false moves.
- **Office room, two scans** (27 and 34 objects): good alignment, 555 inliers from 11 pairs, offset -4 degrees, median parallax about 9.4 degrees; 11 possibly moved, 0 moved, 10 removed and 3 new that are very probably detector variance (one scan listed three paper cups, the other one).

Not yet re-run with the 2026-10-31 code. To do on the laptop with the real database: `python scripts/eval_alignment.py --db data/world.sqlite --manifest manifest.json` for the two desk pairs (it works on a copy).

## Results

### Phone camera / HTTPS
_not yet measured on a phone_

### Android Chrome over USB (`adb reverse`, `http://localhost:8000`)

Measured 2026-10-27 on a Realme RMX2001 (Android 11, Chrome 155), USB cable to the laptop, server started with `PLAIN_HTTP=1`, port forwarded with `scripts/usb_connect.ps1`, page `http://localhost:8000/probe`. Report text was read from the phone over Chrome DevTools (`adb forward`), after the sensor and camera buttons were tapped on the phone.

| Check | Result |
|---|---|
| `isSecureContext` on `http://localhost:8000` via `adb reverse` | **true** (origin `http://localhost:8000`) |
| `navigator.mediaDevices.getUserMedia` present | **true** |
| `getUserMedia` rear camera | **works**: 480x640, `facingMode: environment`, 9.8 s from tap to stream. The probe asks for no resolution, so 480x640 is Chrome's default, not the phone's limit. Permission state before the tap was `prompt`, so a Chrome camera prompt appeared and was accepted; I did not capture its wording or whether the OS-level prompt came first |
| `deviceorientation` | **works, no extra prompt needed**: 92 events in 3 s (~30 Hz), alpha/beta/gamma populated |
| `devicemotion` | **works**: 171 events in 3 s (~57 Hz), `accelerationIncludingGravity` populated |
| Permissions API: accelerometer, gyroscope, magnetometer | all `granted` before use |
| Camera / microphone Permissions API state before first use | `prompt` / `prompt` |
| `DeviceOrientationEvent.requestPermission` | **exists** in this Chrome (so my earlier "iOS Safari only" expectation was wrong); calling it returned `granted` without a visible choice being recorded |
| `AbsoluteOrientationSensor` (Generic Sensor API) | present |

Not measured: whether the camera prompt reappears after a reload, and the exact prompt wording.

USB reliability: during the test `adb devices` repeatedly dropped to empty for a few seconds and came back (first seen as "Unknown USB Device (Port Reset Failed)" in Windows). Each drop clears `adb reverse`, so a flaky cable or port will make `http://localhost:8000` fail on the phone until `scripts/usb_connect.ps1` is re-run. Try a different cable or port before blaming the app.

Note: the app itself does not use orientation sensors yet; `/probe` only checks availability.

### Orientation drift (absolute vs relative source)

2026-10-28, Realme RMX2001 over USB (`http://localhost:8000/orientation`), 3 full turns on the spot, phone upright in portrait, roughly level, 18 to 21 s each. Results read from the page over DevTools (`window.__orientation.trials`). Both sources reported `sensor:AbsoluteOrientationSensor` / `sensor:RelativeOrientationSensor` (Generic Sensor API, not the event fallback), 5625 samples each.

Procedure: tap **Start sensors**, point the camera at a fixed target, **Mark START**, turn a full 360 degrees back to the same target, hold still 2 s, **Mark END**. The page averages 0.5 s around each mark and reports the signed yaw/pitch/roll difference per source, and flags trials whose net turn is not within 45 degrees of 360.

Angles are the rear camera's pointing direction (yaw clockwise from north, pitch up positive).

| Trial | Source | dYaw (deg) | dPitch (deg) | dRoll (deg) | Net turn (deg) | Duration (s) |
|---|---|---|---|---|---|---|
| 1 | absolute | 0.42 | -1.54 | 0.20 | -359 | 21 |
| 1 | relative | 0.43 | -1.54 | 0.19 | -359 | 21 |
| 2 | absolute | 0.39 | -0.53 | -1.09 | -360 | 18 |
| 2 | relative | 0.40 | -0.54 | -1.09 | -360 | 18 |
| 3 | absolute | -0.07 | 1.81 | 0.31 | -360 | 21 |
| 3 | relative | -0.08 | 1.80 | 0.32 | -360 | 21 |

Mean |dYaw|: absolute 0.29 deg, relative 0.30 deg.

**Findings.**
- **The two sources are the same signal on this phone.** Start yaws match to 0.01 degrees in every trial (16.9, 16.0, 24.5) and the drift differs by at most 0.01 degrees. The "relative" sensor is not gyro-only here: it is north-referenced like the absolute one. The "lower drift" pick is therefore a tie (page says ABSOLUTE by 0.01 degrees, which means nothing). One earlier Save sample frame (2026-10-28 11:26:44) showed relative yaw 25 vs absolute 360, so they can disagree briefly at sensor start; the other four saved frames agreed. Not explained.
- **Decision: use the absolute source.** It gives a shared yaw frame between scans (what-changed, guidance) and costs nothing in drift. The relative source stays as a fallback for phones where it really is gyro-only; check on other devices.
- **What this does not show.** Returning to the start only shows the net error over a loop in one place; magnetic distortion that varies with heading (steel desk, speakers, the laptop) can still bend yaw in the middle of the turn and cancel at the end. Pitch moved up to 1.8 degrees between marks and roll up to 1.1, which is mostly my hand. Measure heading error against known bearings (for example door, window, wall corner at 90 degree steps) before trusting angles below about 10 degrees; `MOVE_DEG` 15 and `merge_deg` 8 stay as placeholders for that reason.
- Yaw drift over a 20 s turn is far below the anchor error expected from FOV error, so FOV calibration matters more than the sensor choice.

### Semantic resolution (F11), Spark `muse-spark-1.3`, effort low

2026-10-28, `scripts/resolve_queries.py`, 14 queries x 2 conditions, 1 run each, 0 API errors. Queries are functional descriptions ("the thing I charge my laptop with"), Hindi/English mixes ("mera charger kahan hai", "kuch kaatne ke liye kainchi") and one unanswerable query. A hit means the concept or an alternate is in a hand-written acceptable set (or empty for the unanswerable one).

| known room labels given in the prompt | hits | latency ms p50 / p95 |
|---|---|---|
| no | 14/14 | 2347 / 4061 |
| yes | 14/14 | 2081 / 3035 |

- Every returned concept passed the SAM-friendliness filter (1-3 words, no spatial words or commas); the unanswerable query returned an empty concept.
- With labels given, Spark picked a label from the list when one fit ("something to drink water from" -> "water bottle", not "glass"; "the thing I type on" -> "laptop").
- Caveats: small, easy set and a lenient acceptance list; one run each, so no variance estimate; the labels were the nine default desk objects. Treat it as "the path works", not as an accuracy figure.
- Latency is the weak point: about 2 to 5 s per query at effort low, before speech recognition and TTS. Next to try: `minimal` effort and a shorter prompt.
- Cost: $0.063 for 28 calls (about $0.002 each; reasoning tokens count as output).

### Scan pipeline (F10), Spark names -> SAM concept prompts -> World Model

2026-10-28, `scripts/scan_frames.py` on the same four clean frames as the SAM run (yaw 3.6, 14.7, 25.7, 25.9 degrees, pitch -11 to -14, absolute source, frames resized to 540x960, `min_conf` 0.5, default FOV 60 degrees). Spark `effort=low` lists the object types, SAM runs one concept prompt per name concurrently (up to 4).

| per frame | value |
|---|---|
| Spark listing latency | 3.4 to 4.9 s (p50 4.4 s) |
| SAM stage wall time (3 to 4 concurrent calls) | 2.7 to 3.4 s (p50 2.7 s) |
| names listed | 3, 3, 4, 4 (laptop, phone, water bottle, cable, candy) |
| cost | $0.012 per frame ($0.052 for the run) |

- **End to end it works**: every frame stored, 0 SAM errors, people and screens were not listed, and "the thing I drink water from" resolved to "water bottle" and located it. "Something I can charge my phone with" resolved to "cable", which is a fair answer but not a charger.
- **~7.5 s per keyframe, sequential.** At that rate a 30 s scan yields about 4 keyframes if frames are processed one after another. Needs processing frames concurrently (the pipeline allows it) and/or a lighter first stage. Spark `effort=minimal` was only 10 to 25% faster (3.3 to 3.8 s vs 3.6 to 4.6 s on one frame, 4 calls); output tokens stayed at 290 to 360, so reasoning is not the main cost.
- **Identity is fragmented**: 14 objects were stored for a scene with roughly 2 laptops, 2 or 3 bottles, 1 phone and a few cables. Cables in particular are long and thin and SAM returns several boxes at confidence 0.53 to 0.8. Same-label anchors from different frames differ by up to about 10 degrees (the black bottle was anchored at yaw 37 and 28.7 from two frames 11 degrees apart), so either the FOV is wrong, the sensor yaw is off, or both. **FOV calibration is the next measurement**: the 60 degree default is probably too wide for the 9:16 video (my estimate from the phone's sensor geometry is about 40 to 45 degrees; unverified). I could not fit it from these frames because the objects are cut by the image edge.
- **Not measured**: recall (objects Spark forgot to list), precision (no labels file), and behaviour over a full 360 degree scan.
- Raw results in `data/results/scan_frames-*.json`.

**Room scan attempt, 2026-10-28 13:01** (35 frames, one pass of about 346 degrees at about 8 degree steps, tapped about once a second, 280 to 530 KB each; yaw 158 to 179 degrees left uncovered by a 63 degree jump between two shots). Only 11 frames were sent to the APIs: the other 24 show people (faces in frames 4 to 7, coworkers behind the desk in 15 to 29) or the laptop screen, and the user has not cleared them. Of the 11: 6 were windows or a blank wall and Spark listed no objects (no false positives, no SAM calls, $0.004 each, still 3.2 to 5.5 s per Spark call); 2 had the desk objects and produced 8 objects (mouse, phone, 4 "water bottle" boxes that are 2 to 3 real bottles, cable, laptop). Coverage and merging across a full scan are therefore still unmeasured. Cost $0.076.

### SAM latency

2026-10-28, `scripts/sam_latency.py --max-sides 960 --repeat 1 --concurrency 1`, `sam-3.1`, 4 phone frames (1080x1920 resized to 540x960) of a desk with a metal bottle, black bottle, laptops, a phone, cables. I sent only four of the eight new frames, the ones with no person in view; laptop screens with small text are still visible in them. 7 concepts, 1 run each, then a box-prompt pass seeded from the concept boxes. 0 errors.

| mode | n | p50 ms | p95 ms | first event p50 ms |
|---|---|---|---|---|
| concept prompt | 28 | 2040 | 2852 | 1697 |
| box prompt | 8 | 1993 | 2252 | 1597 |

| concept | mean matches | frames with 0 matches | mean conf |
|---|---|---|---|
| water bottle | 1.25 | 1/4 | 0.83 |
| laptop | 1.50 | 0/4 | 0.95 |
| phone | 0.25 | 3/4 | 0.97 |
| paper cup, charger, keys, scissors | 0 | 4/4 | n/a |

- **Parsing works live**: stream events, `meta-sam-parser`, boxes, masks and confidences all came through. Repeat stability not tested (repeat 1; the script's "0/28 changed" line compares nothing).
- **One request per concept**: about 2 s each, so a 9-concept scan frame costs about 18 s sequentially. Per-frame concept prompting for a 30 s scan will not work at 2 s per call unless run concurrently or limited to Spark-listed objects with batched box prompts (already the plan). Box prompt latency is the same as concept prompt (2.0 s vs 2.0 s), so batching up to 16 boxes per call is the saving, not the prompt type [VERIFY with a 16-box call].
- **Accuracy is not measured**: no labels file with true counts exists. The zero-match rows are probably right (paper cup and charger are only in the three frames I excluded; keys and scissors are not in the room), but I have not checked the 0.25 phone hit or the 1.25 bottle count by eye.
- Cost: $0.092 for this run ($0.155 logged in total of the $150 credit).
- Fixed in `scripts/common.py`: `sam_call` now closes the stream (`raw.close()`), which removes the noisy `aclose(): asynchronous generator is already running` messages on teardown.


### Spark grounding
_TODO_

### Spark plan
_TODO_

### Voice
_TODO_

## Cost log

`data/cost_log.jsonl` is appended by each script (estimates from published prices). Meta credit: $150.

| Date | Script | Est. USD | Notes |
|---|---|---|---|

<!-- eval_memory:start -->
## Spatial memory evaluation

Run 2026-10-28 with `python scripts/eval_memory.py --real`: 5 synthetic rooms of 16 known objects per condition, each swept in 15 degree steps; the memory assumes a 60 degree FOV; frames reach the memory in a random order (the scored run) and in 6 further random orders (order stable). Match tolerance 12 degrees. Detections are simulated (jitter, misses, clipping, colour noise), so this measures the memory, not SAM.

| Condition | System | Objects (seen) | Duplicates | Wrong merges | Missed | ID switches | Find success | Recall | Anchor error (median) | Order stable |
|---|---|---|---|---|---|---|---|---|---|---|
| clean (FOV as assumed) | full | 16.0 (16.0) | 0% | 0% | 0% | 0.0 | 100% | 100% | 0.6° | 100% |
|  | no appearance check | 16.0 (16.0) | 0% | 0% | 0% | 0.0 | 100% | 100% | 0.6° | 100% |
|  | no fragment promotion | 16.0 (16.0) | 0% | 0% | 0% | 0.0 | 100% | 100% | 0.6° | 100% |
|  | arrival order, no appearance, no promotion (old behaviour) | 16.0 (16.0) | 0% | 0% | 0% | 0.0 | 100% | 100% | 0.6° | 100% |
| FOV 10% wider than assumed | full | 16.6 (16.0) | 4% | 0% | 0% | 1.2 | 100% | 100% | 1.2° | 100% |
|  | no appearance check | 16.6 (16.0) | 4% | 0% | 0% | 1.2 | 100% | 100% | 1.2° | 100% |
|  | no fragment promotion | 16.6 (16.0) | 4% | 0% | 0% | 1.2 | 100% | 100% | 1.2° | 100% |
|  | arrival order, no appearance, no promotion (old behaviour) | 16.6 (16.0) | 4% | 0% | 0% | 1.2 | 100% | 100% | 1.2° | 50% |
| hard (FOV +10%, 15% misses, more clipped views, noisier boxes) | full | 17.4 (16.0) | 9% | 0% | 0% | 3.2 | 100% | 100% | 1.5° | 100% |
|  | no appearance check | 17.4 (16.0) | 9% | 0% | 0% | 3.2 | 100% | 100% | 1.5° | 100% |
|  | no fragment promotion | 17.4 (16.0) | 9% | 0% | 0% | 3.2 | 100% | 100% | 1.5° | 100% |
|  | arrival order, no appearance, no promotion (old behaviour) | 17.6 (16.0) | 10% | 0% | 0% | 3.2 | 100% | 100% | 1.6° | 63% |
| twins (4 pairs of same-label objects 3 degrees apart, different colours) | full | 20.0 (20.0) | 0% | 2% | 0% | 0.4 | 100% | 100% | 0.8° | 100% |
|  | no appearance check | 20.0 (20.0) | 0% | 17% | 0% | 6.4 | 100% | 100% | 0.8° | 100% |
|  | no fragment promotion | 20.0 (20.0) | 0% | 2% | 0% | 0.4 | 100% | 100% | 0.8° | 100% |
|  | arrival order, no appearance, no promotion (old behaviour) | 20.0 (20.0) | 2% | 20% | 2% | 5.0 | 100% | 100% | 0.8° | 13% |
| sparse sweep (30 degree steps: each object seen once or twice, often clipped) | full | 16.2 (16.0) | 1% | 0% | 0% | 0.4 | 100% | 100% | 1.1° | 100% |
|  | no appearance check | 16.2 (16.0) | 1% | 0% | 0% | 0.4 | 100% | 100% | 1.1° | 100% |
|  | no fragment promotion | 16.2 (16.0) | 1% | 0% | 0% | 0.4 | 100% | 100% | 1.1° | 100% |
|  | arrival order, no appearance, no promotion (old behaviour) | 16.2 (16.0) | 1% | 0% | 0% | 0.4 | 100% | 100% | 1.1° | 100% |
| very sparse sweep (60 degree steps: most objects seen once) | full | 16.0 (15.8) | 3% | 0% | 1% | 0.4 | 98% | 99% | 1.4° | 100% |
|  | no appearance check | 16.0 (15.8) | 3% | 0% | 1% | 0.4 | 98% | 99% | 1.4° | 100% |
|  | no fragment promotion | 15.6 (15.8) | 3% | 0% | 4% | 0.4 | 98% | 96% | 1.4° | 100% |
|  | arrival order, no appearance, no promotion (old behaviour) | 15.6 (15.8) | 3% | 0% | 4% | 0.4 | 98% | 96% | 1.4° | 100% |

### Real scans replayed on a copy of the database

No ground truth exists for these, so only counts and decisions are reported. Appearance histograms were recomputed from the stored frames. Stored detections are replayed as they were saved: the scan pipeline's same-frame box merge (`scan.merge_parts`) happened before they were stored, or, for scans made before it existed, did not happen, and is not repeated here.

| Scan | Frames | Objects stored | Objects now | Appearance vetoes | Fragments promoted | Fragments held | Largest labels (stored→now) |
|---|---|---|---|---|---|---|---|
| 5 | 8 | 36 | 36 | 0 | 0 | 1 | cable 15→15, laptop 5→5, backpack 4→4, power adapter 4→4 |
| 4 | 16 | 64 | 52 | 2 | 1 | 9 | laptop 14→8, cable 11→9, paper 10→11, bag 7→4 |
| 3 | 3 | 8 | 5 | 0 | 0 | 2 | water bottle 4→2, mouse 1→1, phone 1→0, cable 1→1 |
| 2 | 18 | 37 | 29 | 1 | 0 | 5 | laptop 7→5, cable 6→4, water bottle 5→4, power adapter 3→3 |
| 1 | 11 | 13 | 10 | 0 | 0 | 2 | water bottle 4→2, flag 3→3, computer mouse 1→1, smartphone 1→0 |

Colour similarity (histogram intersection, 1 = identical) of same-label detections from different frames, split by whether the memory put them in one object. The veto threshold is 0.35: it refuses a geometric match below it, so a threshold that is too high creates duplicates.

| Scan | Same-object pairs | min / median | Different-object pairs | p10 / median | Below threshold (same / different) |
|---|---|---|---|---|---|
| 5 | 15 | 0.59 / 0.83 | 159 | 0.25 / 0.57 | 0 / 35 |
| 4 | 23 | 0.44 / 0.68 | 229 | 0.27 / 0.54 | 0 / 51 |
| 3 | 3 | 0.81 / 0.82 | 1 | 0.86 / 0.86 | 0 / 0 |
| 2 | 19 | 0.37 / 0.59 | 62 | 0.35 / 0.46 | 0 / 7 |
| 1 | 6 | 0.80 / 0.81 | 9 | 0.74 / 0.83 | 0 / 0 |

<!-- eval_memory:end -->
