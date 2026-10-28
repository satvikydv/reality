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
- [ ] Voice handshake JSON shape (flat as in PRD 7.3) and event field names (`text` vs `transcript`)
- [x] `reasoning.effort: "low"` accepted on `muse-spark-1.3` (2026-10-28, `resolve_queries.py`); other values untested
- [x] Spark strict `json_schema` via the Responses API works for a flat schema (concept/alternates/confidence); the recursive plan schema is still untested

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
