# Decisions

## 2026-10-28: pivot to memory-first (spatial memory as the headline)

**Decision.** The product headline changes from "guide and verify a physical task" to **spatial memory**: scan a room by panning the phone, find objects by what they do, get guided to them, and see what changed between two scans. Plan-and-verify (F3) stays as a secondary feature for the Best Agentic bonus. The brick-kit hero scenario is dropped. PRD sections 2, 3, 4, 5, 12 and 15 are updated; new requirements are F10 (room scan with orientation anchors), F11 (semantic find), F12 (guided retrieval) and F13 (what-changed).

**Why.**
- **Fits the rubric and the anti-project warning.** The PRD already says the value is memory, verification and the agent API, and that the product must never read as "a camera that labels things and chats". Memory, retrieval by function and change over time are the parts a plain image analyzer cannot do, and they give the Reality API a stronger story (an agent asking where something is or what moved).
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

**Not updated (still describe the old hero).** PRD sections 14 and 16, the README M0 commands, and the default task list in `scripts/spark_plan.py`. `scripts/sam_latency.py` has no labels file, so counting accuracy against a true count per frame is not implemented yet.

## 2026-10-28: World Model and anchoring (first memory-first build)

- **Anchors are directions, not positions.** `backend/app/anchoring.py` turns a pixel plus the frame's camera pose (yaw, pitch, roll) into a world (yaw, pitch) with a pinhole model. Image roll is sensor roll plus `screen.orientation.angle`, assuming Chrome rotates the video with the screen [VERIFY]. `DEFAULT_HFOV_DEG = 60` is a placeholder until the phone is calibrated [VERIFY]; every frame stores the FOV it was anchored with.
- **Identity inside a scan.** A detection joins an existing object with the same normalised label within `merge_deg` (default 8 degrees) of its anchor; two detections in one frame never merge. The anchor is a confidence-weighted mean and `spread_deg` records the worst disagreement, which doubles as the anchor-error metric for the README. Appearance re-identification and cross-scan matching are not built.
- **"Where" is relative to the scan start.** The spoken description uses the yaw offset from the first frame of the scan, so it works with the relative source (arbitrary yaw zero) as well as the absolute one. Pitch is absolute (gravity-referenced).
- **Relations from anchors only.** `neighbors()` gives left of / right of / above / below / next to. "On" and "behind" need masks or depth and are not produced yet.
- **Frames on disk, rows in SQLite.** JPEGs go under `frames_dir/<scan>/<frame>.jpg` so a find query can return a replay frame; no pruning yet (about 60 frames per 30 s scan at 2 fps).
- Not wired into `main.py` yet. Tests: `pytest tests` (28 pass).

## 2026-10-27

- **Predicate encoding.** PRD section 8's example uses key-per-op objects (`{"all":[{"exists":...}]}`). The code uses an `op`-tagged tree (`{"op":"all","items":[...]}`) in `backend/app/programs/schema.py`. A discriminator is easier to validate with pydantic and to express in a strict JSON schema. Revisit if `spark_plan.py` shows the endpoint rejecting it.
- **HTTPS for the phone.** Self-signed cert covering localhost and the laptop's LAN IPs (`scripts/make_cert.py`), accepted once on the phone. mkcert and ngrok are not installed/enabled; ngrok would expose the backend publicly, so it is opt-in only.
- **Frame protocol.** Binary WS frame: `flags u8 | seq u32 | client_ts f64 | JPEG`. Flag bit 0 saves the frame to `data/frames/` so the M0 scripts run on real phone captures.
- **SAM box-prompt test source.** `sam_latency.py` seeds box prompts from the concept pass's own boxes, so it measures box-prompt latency without depending on Spark grounding.
- **No commits yet.** The folder was not a git repo; nothing has been initialised or committed.
