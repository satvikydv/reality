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

## Phone first run and navigation: manual checklist

Status: **not run on a phone yet.** The page logic is covered by `tests/test_entry_flow.py` (headless Chrome over the DevTools protocol with a canvas as the camera, 55 tests) and `tests/js/`, which cannot show how a real Android or iOS browser behaves. Work through this on the phone, tick what holds, and write what does not under the item. Setup: `PLAIN_HTTP=1`, `adb reverse tcp:8000 tcp:8000`, inspect the page from `chrome://inspect` to read `__ui.session().state` (the camera, wake lock and portrait lock as the page sees them). To repeat a first run: Chrome, site settings, clear data for localhost (or use a private tab).

Record: device ______ · browser and version ______ · date ______

**Nothing starts by itself**
- [ ] First visit (cleared data): the intro card appears. No permission prompt, no camera indicator in the status bar, no camera picture behind it.
- [ ] Returning visit: lands on Home on a plain dark background. No prompt, no camera indicator. In the inspector `__ui.session().state.camera` is `"off"`.
- [ ] Wait 30 s on Home: still no camera indicator, no prompt, no battery drain from the camera.

**Intro and permissions**
- [ ] Three cards: what it does, how to scan, your data. Back, Next and Skip work. The last button says "Allow camera and motion".
- [ ] Tapping it shows the primer. Still no browser prompt. Text names Camera and Motion sensors and says the microphone is only for the mic button.
- [ ] Continue: the browser asks for the camera (and, on iPhone, motion first). Allow: lands on Home with "Allowed".
- [ ] Reload: no intro, no primer. Settings, Replay intro: the cards show, finishing returns to Settings, a reload still shows no intro.
- [ ] Block the camera (tap Block, or site settings), tap New scan: the primer shows "Camera access was blocked" with Try again. Unblock, Try again: the scan screen opens.
- [ ] iPhone only: deny motion: "Motion access was blocked" with the steps; allow on retry.
- [ ] Android: does motion need any prompt? Note what happened (`__ui.session()` and whether the compass tape moves).

**Home**
- [ ] The pill reads "Connected · API key valid". Record what it reads with the real Meta key (the key check lists models; if the API refuses that call the pill says "API key set" instead, which is expected).
- [ ] Stop the server: within about 15 s the pill turns red "Server offline", New scan, Find something and What changed dim, Settings still opens. Start the server: it recovers within 10 s without a reload.
- [ ] Remove `MODEL_API_KEY` and restart: "API key missing", New scan and Find dim, What changed still works.
- [ ] Last scan card: after a scan it shows the same object count and coverage as the summary, and "just now" or minutes ago. With no scans: "No scan yet".

**Camera lifecycle**
- [ ] New scan: the camera opens, the red "Camera on · STOP" pill shows, the checklist shows. The light line reads fine in a lit room. Cover the lens: it reads dark within about a second.
- [ ] STOP (or Cancel on the checklist): back on Home, the status-bar camera indicator is gone within a second.
- [ ] Find something: camera on, Back turns it off. Ask for something: on the result screen the camera is off. Guide me: camera on again; END turns it off; Done turns it off.
- [ ] Switch to another app, or to another tab, while on a camera screen: the indicator goes out at once. Coming back shows Home with "The camera was turned off because the page was hidden". The camera does not restart by itself.
- [ ] During a scan, press the power button: the scan stops, "What it found so far is kept", and a new scan can be started afterwards.
- [ ] Wake lock: set the phone's screen timeout to 15 s. On a camera screen the screen stays on past 15 s. After STOP it dims again. Record `state.wake`.
- [ ] Portrait lock: record `state.portrait`. If it is `false` (expected on Chrome outside fullscreen), rotate to landscape during a scan and record what happens ("Hold the phone upright", or the camera guard stopping the scan).
- [ ] Open the phone's own camera app during a scan: the scan stops with "The camera stopped".

**Scan coaching**
- [ ] Turn faster than a lap in 10 s: "Rotate slower". Point at the floor: "Tilt up a little". Point at the ceiling: "Tilt down a little".
- [ ] Skip a part of the room: once the ring passes half, "Area not covered yet: N° to the left (right)".
- [ ] The ring and percentage rise as frames are sent; the frame count matches the server log.
- [ ] Cancel during a scan: "Discard this scan?" Keep scanning continues. Discard goes Home, and the scan is not in Settings, Your scans.
- [ ] Done: the processing screen shows "Reading frames: n of m" and a bar that moves, then the summary.
- [ ] Done, then break the link (stop `adb reverse` or the server): after about 30 s "No answer from the server for a while" with Check the server and Go home. Restore the link, Check the server: the summary appears from the stored scan.

**Find, errors and captions**
- [ ] Ask for an object that is in the scan: result with replay, Guide me works.
- [ ] Ask for something that is not in the room: "Not found in your last scan", with the coverage percentage and the "not checked" explanation. With no scans stored: "Nothing has been scanned yet".
- [ ] Slow answer (block the model API on the laptop): "Still looking" after 8 s, then "No answer from the server" with Try again after 30 s. Cancel in between works.
- [ ] Every spoken reply is also on screen (result, summary, changes, guide hint, and the caption bar elsewhere). Mute the phone: everything still works by reading.
- [ ] Typing instead of speaking works everywhere. Mic button: deny the microphone: "Microphone access is blocked. Type your question instead" and the text box is focused. Allow it: speak "where is my keys". Change the voice language in Settings and speak in it.
- [ ] What changed with one scan only: "Nothing to compare yet" with a Scan the room button. With two scans: results as before.

**Settings**
- [ ] Keyframes per second 1 against 3: scan 30 s each and record the frame counts: 1/s ____ , 3/s ____ .
- [ ] Voice language survives a reload. Delete one scan (confirm dialog, Keep it cancels), then Delete all scans.
- [ ] No dots menu and no Debug HUD row on a normal URL. With `/?debug` both exist and the toggle works.

**Demo**
- [ ] `/?demo` and `/?demo&screen=welcome|primer|primer-denied|settings|ask|scan-ready|notfound|processing|changes-empty|home-offline|home-empty|home-nokey` open with no prompt of any kind.

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
