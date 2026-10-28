# Decisions

## 2026-10-28: pivot to memory-first (spatial memory as the headline)

**Decision.** The product headline changes from "guide and verify a physical task" to **spatial memory**: scan a room by panning the phone, find objects by what they do, get guided to them, and see what changed between two scans. Plan-and-verify (F3) stays as a secondary feature for the Best Agentic bonus. The brick-kit hero scenario is dropped. The scope, user stories, demo storyboard, requirements and risk list were updated; new requirements are F10 (room scan with orientation anchors), F11 (semantic find), F12 (guided retrieval) and F13 (what-changed).

**Why.**
- **Fits the rubric and the anti-project warning.** The original scope already says the value is memory, verification and the agent API, and that the product must never read as "a camera that labels things and chats". Memory, retrieval by function and change over time are the parts a plain image analyzer cannot do, and they give the Reality API a stronger story (an agent asking where something is or what moved).
- **Lower technical risk than the old hero.** The riskiest claim in the old plan was fine-grained step verification of brick placement (listed as a risk with a fallback hero task). Memory features rest on coarse, robust signals: object presence, rough direction and mask geometry.
- **The phone supplies the missing signal.** Orientation sensors give yaw/pitch/roll for every frame, so objects can be anchored to a direction in the room without SLAM, and guidance has a "target minus current heading" to point at.
- **Better demo.** A spoken functional query followed by an arrow that steers you to the object is understandable in seconds and works as the 30-second X clip. What-changed gives a second, different beat.
- **Cheap to scope.** Reuses what M0 is already building (SAM, Spark structured output, voice). Plan-and-verify code is not thrown away.

**Costs and constraints accepted.**
- Anchors are angular only. Translation is unknown, so guidance assumes the user rotates roughly in place and claims no distances.
- Orientation quality is unproven: magnetometer noise indoors and drift over a scan are M0 measurements (the `/orientation` page), and the lower-drift source is chosen from them.
- What-changed needs a shared yaw frame between scans (absolute source, or alignment from matched static objects).
- "Behind" cannot come from angular anchors alone and is marked [VERIFY].
- Schedule is tight: M4 now carries the Reality API, plan-and-verify and the open-weight backend in one day. The cut line was changed so F3 shrinks first among the kept features (after F9, F8 second option, F7 polish, F5).
- Priority changes: F3 moved from P0 to P1 (secondary), F5 watches from P1 to P2 (not in the new storyboard), F10 to F12 are P0 and F13 is P1.

**Implementation changes made with the pivot.**
- WS frame protocol is now `flags u8 | seq u32 | client_ts f64 | meta_len u16 | meta JSON | JPEG`. "Save sample" sends orientation (both sources) and camera info as `meta`; the server writes it to a `.json` sidecar next to each saved frame in `data/frames/`.
- New shared module `web/js/orientation.js` (yaw/pitch/roll of the rear camera from either source) and the `/orientation` drift test page.
- `scripts/sam_latency.py` defaults to desk-object concepts: keys, headphones, mug, notebook, remote control, water bottle, charger, scissors, laptop.

**Not updated (still describe the old hero).** The README M0 commands and the default task list in `scripts/spark_plan.py`. `scripts/sam_latency.py` has no labels file, so counting accuracy against a true count per frame is not implemented yet.

## 2026-10-28: laptop dashboard (replaces the TV view)

- **Laptop browser, not a 1920x1080 TV stage.** The Claude Design `tv.html` scaled a fixed stage; this is a fluid grid for a laptop window (min 1100x640), reusing the design's theme and `drawMap`. `/tv` and `web/tv.html` are removed.
- **One feed for everything**: the page opens `/ws/events` and renders the server's event stream (scan progress, `world_update` with live objects and coverage, find results, agent calls, guide requests). It keeps no state of its own, so replaying the last 200 events rebuilds the screen when it is opened late or reloaded.
- **Compare panel on REST**: `/api/scans` lists scans, `/api/changes?before=&after=` returns the same answer as the `what_changed` agent tool. Cards show before and after photos with the object boxed, grouped Moved, Removed, Added; `unverified` is only counted in the summary line.
- **Agent panel shows what the agent was told**: `agent_call` events now carry the sighting and object id, so the photo and the map highlight match the agent's answer.
- **Model text goes in with `textContent` only** (object names and answers come from a model). Replay photos can show people; the compare cards and the agent panel show them at thumbnail size, so shoot the demo scene without bystanders.
- **Bug found while screenshotting**: `ui.js` `fit()` resized a canvas only when its width changed, so a height-only layout change left a stretched drawing (duplicated compass labels). It now compares both.
- Checked in headless Chrome at 1440x900 with scripted data and with real events (a replayed scan, an MCP `where_is`, a compare of two stored scans). The compare on real data shows many moves because the two scans are unrelated (different frame sets); a controlled before/after scan is still to do.

## 2026-10-28: Reality API (MCP), event feed, agent guide requests

- **MCP over Streamable HTTP, mounted on the main app at /mcp**, not a separate process. A second process could not share the live event feed or reach the connected phone. Each startup builds its own MCP server because a session manager can only run once (this also lets tests start the app repeatedly). `python -m app.mcp_server` stays as a read-only stdio fallback.
- **Answers carry their own trust information.** `where_is` returns frames seen, best confidence, anchor spread, age, and whether the object is from the latest scan. A miss is `not_seen` with the latest scan's coverage, never "not there"; the server's instructions tell agents this explicitly. `what_changed` keeps `unverified` as its own group for the same reason.
- **Read-only except `guide_user_to`.** It resolves the query, then publishes `guide_request` on the event bus; every connected phone socket subscribes to that type only and the phone UI jumps to the Guide screen. With no phone connected it returns `no_phone_connected` instead of pretending. `get_frame` returns photos that may show people, so `REALITY_API_FRAMES=0` removes the tool.
- **DNS-rebinding guard stays on** (allowed hosts 127.0.0.1, localhost, ::1, plus `MCP_ALLOWED_HOSTS`); a request with a foreign Host header is rejected (tested over a real socket). Non-loopback binds still need `ACCESS_TOKEN`.
- **Event bus** (`app/events.py`): in-process pub/sub with a 300-event history, bounded per-subscriber queues that drop their oldest events, and an optional type filter. Scan progress, `world_update` (the live object list after each stored frame), find results and agent calls are published; `/ws/events` streams them to the laptop dashboard with the last 200 replayed, so a dashboard opened mid-demo catches up.
- **What-changed speech** groups repeated labels ("3 flags are gone") and stops after 6 sentences with "And N more changes", because one noisy scan produced a paragraph of duplicates when run on real data. The structured lists are complete.
- **Tried on real data**: the CLI client against the live server answered `scan_summary` and `where_is` from the stored office scan. The `what_changed` run compared two unrelated scans (a replay of different frames against the live scan) and reported many "moves"; it proves the plumbing, not the accuracy. A controlled before/after test (move two objects between two real scans) is still to do.
- Tests: `tests/test_reality_api.py`, `test_mcp_server.py` (in-process MCP client), `test_http_integration.py` (real sockets: phone, dashboard and MCP client on one app), `test_events.py`.

## 2026-10-28: auto capture and the live scan queue

- **The user never saves frames.** "Scan room" starts a scan, then the page watches the camera heading (`web/js/keyframe.js:KeyframePolicy`) and sends a keyframe when the camera has turned `stepDeg` (18 degrees, great-circle so pitch counts) from the last sent frame and the phone is steady (under 12 deg/s over the last 400 ms; between 12 and 30 deg/s a frame is only taken once 1.6x overdue; above 30 deg/s it says "slow down"). Min 500 ms between frames. The 12 deg/s limit comes from the manual run, where frames taken while turning at about 8 deg/s were visibly smaller JPEGs (203 to 224 KB vs 400+); not tuned beyond that [VERIFY on the phone].
- **Coverage ring on the phone.** 36 ten-degree compass bins, each keyframe fills +-15 degrees; the ring shows percent and heading, and the hint names the nearest gap ("gap 40 deg to the left"). The scan ends by itself at 97% or when the user taps Done. The server independently reports coverage from the frames it actually stored (`yaw_coverage`, +-0.4 x FOV), so a frame that failed server-side shows up as a gap in the summary.
- **Wire protocol.** Binary frames keep the existing header; flag bit 2 marks a scan keyframe (bit 1 is still "save sample"). JSON control messages: `scan_start`, `scan_end`, `find`. Server events: `scan_started`, `frame_queued`, `frame_rejected`, `frame_done`, `frame_error`, `scan_summary`, `find_result`, `error`. Replies without a `type` are the old M0 echo, so the dev tools still work.
- **Server queue.** `app/scan_service.py`: one `ScanSession` per socket, a bounded queue (32) and 3 workers running `ScanPipeline.process` concurrently, 4 SAM calls in flight in total. A full queue rejects the frame with a reason instead of blocking. `scan_end` waits for the queue (120 s cap), closes the scan and sends the summary; a dropped socket stops the workers and keeps what was stored. Progress lines go to the uvicorn log (`data/server.err.log` when started in the background).
- **Concurrent frames do not share new names.** A frame in flight cannot see names another in-flight frame is about to add, so the "reuse known names" hint helps less. Observed on a replay: "mouse" and "computer mouse", "phone" and "smartphone". `concepts.ALIASES` folds the common synonyms; the rest needs a smarter merge later.
- **Spark timeout cut from 60 s to 25 s** (`SPARK_TIMEOUT_S`; SAM client 30 s). One listing call took 64 s in the first replay (a stall, then the retry). Both are retried, so a stall now costs about 25 s, not 60.
- **Find results carry the replay frame** (`/world/frame/<id>.jpg` plus box and size); the page draws the box on it and speaks the answer with the browser's speech synthesis.
- **Verified without the phone**: `scripts/replay_scan.py` replays saved frames through the real WebSocket against the live APIs (11 clean frames: 11/11 stored, 13 objects, find worked, $0.10, 86 s of which 66 s was the one stalled Spark call). The page loads on the phone without errors. The auto-capture itself has only been tested as logic (`node --test tests/js/keyframe.test.mjs`), not with a person panning.

## 2026-10-28: Scan pipeline (F10)

- **Spark names, SAM boxes, World Model anchors.** `app/scan.py:ScanPipeline.process` resizes the keyframe (max side 960), asks Spark for the object types in view, runs one SAM concept prompt per name concurrently, drops boxes under `min_conf` 0.5, collapses overlapping boxes (`suppress_overlaps`: IoU over 0.6, highest confidence wins, the more specific label wins a near tie) and observes the rest. Boxes are anchored with the resized dimensions because SAM returns pixels of the image it was sent.
- **Concept prompts per name, not box batches (for now).** The original plan ("Spark lists, then box-prompt batches of 16") needs boxes from Spark, and Spark's box convention is unmeasured (`spark_grounding.py` has not run). One concept prompt per name costs one SAM request each ($0.0025) but needs no boxes. Switch when Spark grounding is verified.
- **A frame is stored only if every SAM call succeeded** (one retry each). A stored frame says "this direction was looked at", which what-changed uses to call an object gone; a half-failed look would fake that. The caller gets `FrameReport(stored=False, errors=[...])` and can retry.
- **Known names are fed back.** Names already in the scan are passed to Spark as "reuse a name from this list" to keep labels stable across frames (what merging depends on).
- **What it cannot do yet.** Spark forgetting to list an object means that object is not looked for in that frame, and coverage still counts the direction as scanned. Cable-like objects fragment into several boxes. Frames are processed in the order given and not deduplicated by pose. Not wired into `main.py`.
- **Refactor.** `sam_call` moved from `scripts/common.py` to `app/sam_client.py` (takes a data URL; closes the stream; never raises), prices to `app/pricing.py`. Scripts keep working through `common.py` wrappers.
- **Speech fix.** `where_is` no longer says "left of the water bottle" about a water bottle; it names a different object first, else "another water bottle".

## 2026-10-28: orientation source = absolute

On the Realme RMX2001 the "relative" sensor is not gyro-only: both sources gave the same yaw (to 0.01 degrees) and under 0.5 degrees of drift over three 360 degree turns (`docs/measurements.md`). The pick is a tie, so use the absolute source: it keeps a shared yaw frame between scans. `app/changes.py:estimate_yaw_offset` stays as the fallback for devices where the relative source has its own zero. Heading error mid-turn (magnetic distortion) is not measured yet, so `MOVE_DEG` and `merge_deg` stay placeholders.

## 2026-10-28: Guidance and what-changed (F12, F13)

- **Guidance in image axes.** `app/guidance.py` expresses the target direction in the camera's right/up/forward basis, so the overlay arrow (`arrow_deg`, clockwise from up) and the hint ("Turn right", "Tilt up", "A bit more left", "Almost there, a little right", "Turn around", "That's it.") stay correct when the phone is rolled or pitched steeply. `dyaw`/`dpitch` are returned for display only. Hint bands (`ARRIVE_DEG` 6, `FAR_DEG` 45, `TURN_AROUND_DEG` 110) are untested guesses until a person walks it [VERIFY]. No smoothing of the live heading yet; add it if the arrow jitters on the phone.
- **Yaw frames need an explicit offset.** The relative sensor's yaw zero is arbitrary per session, so a stored anchor is mapped to the live frame with `yaw_offset` (live minus scan). `app/changes.py:estimate_yaw_offset` finds it by majority vote over same-label object pairs (most agreeing objects, then the smallest rotation), so a few moved objects do not spoil it. It reports `confident` only with two or more agreeing objects; with one shared label a moved object cannot be told from a rotated frame. Pitch is gravity-referenced and is not shifted.
- **What-changed matches by label, then angle.** Pairs are taken nearest-first across scans (label match score above 0). A pair closer than `MOVE_DEG` (15 degrees, placeholder until the drift measurements are in) is unchanged; farther is moved. Leftovers are added or removed.
- **Coverage guard.** Removed and added are only claimed if the other scan's frames actually looked at that direction (projected inside the image with a 5% margin). Otherwise the object goes to `unverified` and the spoken summary says part of the room was not scanned both times. This prevents the commonest false alarm: a partial second scan.
- **Known limits.** A label that vanishes in one place and appears in another reads as "moved"; swapping two identical objects is invisible; appearance is not compared; the coverage test ignores occlusion. Both modules are pure logic and are not wired into `main.py` yet. `WorldModel.list_frames` was added for coverage. Tests: `pytest tests` (60 pass).

## 2026-10-28: Reasoner and semantic resolver

- **Reasoner interface.** `app/reasoner/`: a `Reasoner.respond(messages, tools, schema, effort)` protocol with neutral message parts (`text`, `image`). `MetaApiReasoner` uses the Responses API with strict `json_schema`; `OpenAICompatReasoner` uses Chat Completions for Modal Glimmer and Baseten Spark Open and is only mock-tested [VERIFY live]. `make_reasoner()` picks by `REASONER_BACKEND`. Every call goes through `with_retries` (429, 5xx and connection errors; exponential backoff with jitter; honours `Retry-After`). `MetaApiReasoner` refuses any model id containing "contributor".
- **Resolver judges the raw phrase.** `clean()` checks SAM-friendliness before normalising, because normalising strips the commas that make a phrase unfriendly (caught by a test: "a, b" became "b").
- **Skip the model for exact labels.** If the query is already a known label, `semantic_find` goes straight to the World Model. Only whole-query matches skip; "where are my keys" still goes to Spark.
- **Not-in-memory is explicit.** A resolved concept with no World Model hit returns `needs_live_look`; the SAM live-lookup step is not built yet.

## 2026-10-28: World Model and anchoring (first memory-first build)

- **Anchors are directions, not positions.** `backend/app/anchoring.py` turns a pixel plus the frame's camera pose (yaw, pitch, roll) into a world (yaw, pitch) with a pinhole model. Image roll is sensor roll plus `screen.orientation.angle`, assuming Chrome rotates the video with the screen [VERIFY]. `DEFAULT_HFOV_DEG = 60` is a placeholder until the phone is calibrated [VERIFY]; every frame stores the FOV it was anchored with.
- **Identity inside a scan.** A detection joins an existing object with the same normalised label within `merge_deg` (default 8 degrees) of its anchor; two detections in one frame never merge. The anchor is a confidence-weighted mean and `spread_deg` records the worst disagreement, which doubles as the anchor-error metric for the README. Appearance re-identification and cross-scan matching are not built.
- **"Where" is relative to the scan start.** The spoken description uses the yaw offset from the first frame of the scan, so it works with the relative source (arbitrary yaw zero) as well as the absolute one. Pitch is absolute (gravity-referenced).
- **Relations from anchors only.** `neighbors()` gives left of / right of / above / below / next to. "On" and "behind" need masks or depth and are not produced yet.
- **Frames on disk, rows in SQLite.** JPEGs go under `frames_dir/<scan>/<frame>.jpg` so a find query can return a replay frame; no pruning yet (about 60 frames per 30 s scan at 2 fps).
- Not wired into `main.py` yet. Tests: `pytest tests` (28 pass).

## 2026-10-27

- **Predicate encoding.** The original spec's example uses key-per-op objects (`{"all":[{"exists":...}]}`). The code uses an `op`-tagged tree (`{"op":"all","items":[...]}`) in `backend/app/programs/schema.py`. A discriminator is easier to validate with pydantic and to express in a strict JSON schema. Revisit if `spark_plan.py` shows the endpoint rejecting it.
- **HTTPS for the phone.** Self-signed cert covering localhost and the laptop's LAN IPs (`scripts/make_cert.py`), accepted once on the phone. mkcert and ngrok are not installed/enabled; ngrok would expose the backend publicly, so it is opt-in only.
- **Frame protocol.** Binary WS frame: `flags u8 | seq u32 | client_ts f64 | JPEG`. Flag bit 0 saves the frame to `data/frames/` so the M0 scripts run on real phone captures.
- **SAM box-prompt test source.** `sam_latency.py` seeds box prompts from the concept pass's own boxes, so it measures box-prompt latency without depending on Spark grounding.
- **No commits yet.** The folder was not a git repo; nothing has been initialised or committed.

## 2026-10-28

- **Identity by footprint, not by centre.** Matching a detection to an object by the distance between box centres split big or cut-off objects: a laptop half out of frame has a centre many degrees from its whole-view centre, so one real scan gave 64 objects (14 laptops). Detections now carry an angular footprint (`anchoring.box_extent`); an object keeps the largest footprint seen and a detection joins it when it covers at least half of the smaller of the two (and a tenth of the larger, so a small box inside a big one is not swallowed). The centre rule within `merge_deg` stays as the fallback.
- **Parts of one thing.** SAM boxes the screen and the base of a laptop separately. `scan.merge_parts` unions same-label boxes in one frame that overlap by a third of the smaller box. Different labels are never merged (a cup in front of a laptop).
- **Fragments do not start objects.** A small box touching the image border can join an existing object but never creates one; the neighbouring frame sees the whole thing.
- **Consolidation.** Frames finish out of order, so `WorldModel.consolidate` re-merges same-label objects with overlapping footprints after every stored frame and at scan end. Two objects detected in the same frame are never merged: one view cannot see an object twice.
- Result on the stored scan 4 (79 detections, 16 frames): 64 objects, 14 laptops became 44 objects, 4 laptops. Comparing a scan with a reversed-order replay of itself now reports 0 moved (it reported many before). Not solved: results still depend a little on arrival order, and the FOV is still the 60 degree placeholder (at 45 degrees the same data gives fewer objects).
