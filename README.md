# Reality OS

An agent that watches the physical world through a phone camera, remembers it, guides and verifies real-world tasks, and exposes that world to other agents through an API.

Entry for the Meta Global AI Developer Hackathon (Indie track, solo). Experimental prototype; no medical or safety-critical claims.

> Status: **M0 (de-risk)**. See `docs/decisions.md` for scope decisions and `docs/measurements.md` for measured results.

## What was built when

| Date | Built |
|---|---|
| 2026-10-27 | Repo skeleton, FastAPI HTTPS server with phone camera page + WebSocket frame echo, M0 measurement scripts |

## Run (Windows)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        # then set MODEL_API_KEY

.\.venv\Scripts\python.exe scripts\make_cert.py   # self-signed cert for localhost + LAN IPs
.\.venv\Scripts\python.exe backend\run.py         # https://0.0.0.0:8443
```

HTTPS mode binds `0.0.0.0`, so it needs `ACCESS_TOKEN` in `.env` (the server refuses to start without it). Phone (same Wi-Fi): open `https://<laptop-LAN-IP>:8443/?token=<ACCESS_TOKEN>` once (it is then kept in a cookie), accept the certificate warning, allow the camera. The Windows Wi-Fi profile must be **Private**, or allow inbound TCP 8443 (admin PowerShell):

```powershell
New-NetFirewallRule -DisplayName "RealityOS dev 8443" -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow -Profile Any
```

Use **Save sample** on the phone page to drop full-size frames into `data/frames/`.

## Phone over USB

Android only. No Wi-Fi, firewall rules, certificates or tunnels: `adb reverse` makes the phone's `localhost:8000` reach the server on your machine, and `http://localhost` counts as a secure context in Chrome, so the camera works over plain HTTP. In this mode the server binds `127.0.0.1` only and needs no `ACCESS_TOKEN`.

1. On the phone: Settings, About phone, tap **Build number** 7 times to enable Developer options. Then Settings, System, Developer options, turn on **USB debugging**.
2. On your computer, install [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) and add the extracted folder to `PATH`. Check with `adb version`.
3. Connect the phone with a data-capable USB cable. Unlock it and accept the **Allow USB debugging?** prompt.
4. Start the server in plain HTTP mode:
   ```powershell
   $env:PLAIN_HTTP = "1"; .\.venv\Scripts\python.exe backend\run.py   # http://127.0.0.1:8000
   ```
   (bash: `PLAIN_HTTP=1 python backend/run.py`)
5. Forward the port (the script checks that `adb` exists and that an authorized device is listed):
   ```powershell
   .\scripts\usb_connect.ps1        # bash: scripts/usb_connect.sh
   ```
   With several devices attached, pass `-Serial <id>` (bash: the serial as the first argument).
6. On the phone, open Chrome and go to **http://localhost:8000**. The page opens on Home without touching the camera. The first time, a three-card intro explains the app and leads to a permission primer; only then does the browser ask for the camera and motion sensors. `http://localhost:8000/probe` shows whether the camera and motion sensors work (results go in `docs/measurements.md`).

If the port is already in use, set `PLAIN_HTTP_PORT` and change the port in `scripts/usb_connect.*` to match.

## Scan a room

Needs `MODEL_API_KEY` in `.env`. On the phone tap **Scan** in the bottom bar, pick the room to rescan (the active one is preselected) or name a new one (suggestions include Desk, Shelf and Wardrobe), then **Continue to scan**. Check the pre-scan list (a lit, empty room), tap **Start scanning**, then pan slowly one way, about 20 to 30 s for a full circle, holding the phone upright and roughly level. The page sends a frame whenever you have turned about 18 degrees and the phone is steady. The coverage compass shows which compass directions and which pitch bands (floor, eye level, ceiling) have been looked at, with the camera's view cone, and the hint names the next gap; it shows coverage, not where things are. It finishes by itself at 97% coverage, or tap **Done**. The review groups what was found by kind with thumbnails: **Save** makes it the room's memory, **Discard** deletes it, **View in room** opens the room (an unsaved scan stays there, marked UNSAVED, until you save or discard it).

Server progress goes to the uvicorn log. To keep a log file while the server runs in the background:

```powershell
$env:PLAIN_HTTP = "1"; Start-Process .\.venv\Scripts\python.exe -ArgumentList "backend\run.py" -RedirectStandardError data\server.err.log -RedirectStandardOutput data\server.log -WindowStyle Hidden
Get-Content data\server.err.log -Wait -Tail 20          # one line per scan frame: names, objects, wait, cost
.\.venv\Scripts\python.exe scripts\replay_scan.py --only 1301   # replay saved frames through the same WebSocket (no phone)
```

Tests: `pytest tests` (server, plus the phone page in headless Chrome or Edge when one is installed: `tests/test_entry_flow.py`; set `CHROME_BIN` if it is not found), `node --test tests/js/*.test.mjs` (auto-capture, guidance and projection maths checked against the Python versions, and the first-run, permission and camera-lifecycle logic).

The phone UI also runs without the backend: open `/?demo` for mock data, or `/?demo&screen=<id>&state=<state>` to jump to any state of any screen (for example `/?demo&screen=find&state=all-results`; the screens and states are in `docs/screens.md`). It falls back to demo mode by itself when the server is not reachable.

**World-locked overlay.** On the scan, guide and Explore screens the camera view shows the room's remembered objects where they are: each one's stored direction is projected through the live phone pose and the server's field of view, with its label and an outline. The outline is the stored SAM mask carried into the current view by the rotation-only homography `H = K R K^-1`; objects stored without a mask (or from before masks were kept) get their angular footprint instead, drawn dashed. Objects whose sightings disagree by more than 12 degrees are not drawn, and opacity fades with the time since the object was last seen. Nothing is shown as a distance, because only directions are known. The AR toggle on the Explore and guide screens turns the objects off, and Settings, Overlay detail chooses **Calm** (the default: at most five labels scored by confidence x centrality x recency, duplicates grouped as "3 bottles", labels kept apart, one outline for the Find / Guide target or the object under the reticle, mask outlines simplified and rounded, background classes and unsure objects hidden, the compass tape only on Guide), **Normal** (everything, as before) or **Debug** (Normal plus confidence and uncertainty). All thresholds are in `web/js/overlay-config.js`; the compass tape, horizon and guide arrow stay. The server serves the data at `/api/overlay` (the field of view is the calibrated one, or 60 degrees as a placeholder: see "Calibrate the camera"). The projection math has a Python reference in `backend/app/anchoring.py` and `tests/js/project.test.mjs` checks the phone's JavaScript against it. The dev menu (the dots, only when the page is opened with `?debug`) shows the live frame rate and draw time.

**Changes** in the bottom bar compares two saved scans of the active room (by default the latest against the room's baseline, else the one before it; pick any two from the lists) and shows what moved, is new or is gone, with before and after photos, the move as an angle only, and Confirm or Dismiss per change. Scans of different rooms are never compared. Directions that were not scanned both times are listed as not checked rather than reported as changes.

### Rooms, first run and the camera

**Rooms.** A room (a "space": Living Room, Desk, Wardrobe...) is a place you scan. A room's memory is its **latest saved scan**; older saved scans are kept only for Changes. Rooms never share objects, overlay, guidance or comparison: a scan in one room is not found from another, and two scans of different rooms cannot be compared (the API answers 400). The room you choose is the **active room**, the default for Find, Explore, Changes and the Reality API; creating a room does not make it active. Existing scans from before rooms moved into a room called "Unassigned" (not active) on first start.

**Screens.** The bottom bar is Home, Find, Scan, Changes, Settings; the room switcher pill in the top bar changes the active room from anywhere. Home lists the rooms (search with room filter chips, recent objects, a card per room with objects, coverage, when it was last scanned and an ACTIVE badge). A room opens to its details: Find in this room, Rescan, View changes, Explore (AR), Room settings, recent objects and the scan history (which scan is the memory, which is the baseline, unsaved drafts, Save / Discard / Set baseline / Delete). Find searches the active room, or all rooms read-only with results grouped per room; opening a result from another room asks to switch to that room first, and guidance only ever works inside the active room. Settings holds the global options (voice language, keyframe rate, overlay detail, delete all data, replay intro) and each room's rename and delete (which erases its scans, photos and objects, with a warning). Every screen and state is listed in `docs/screens.md`, which is also the brief for restyling.

**First run and the camera.** Opening `/` never touches the camera, the motion sensors or the microphone. Home shows the server and model API key state (a cheap check on `/api/status`, never a frame or a prompt). The camera starts only from a tap that opens a camera screen: Continue to scan (after the wizard), **Explore (AR)** or **Guide me**; it stops when that screen is left, when the tab is hidden or closed, and when the browser takes it away. While it is on, a red "Camera on · STOP" pill shows, the screen is kept awake and portrait is requested where the browser allows it (Chrome usually only allows that in fullscreen or an installed app). The first visit shows a three-card intro (what it does, how to scan, what happens to your data) and a primer that explains the camera and motion prompts before the browser asks; a refusal gets a plain explanation and Try again. `tests/test_entry_flow.py` fails if the page ever calls `getUserMedia`, the motion APIs, the microphone APIs, the wake lock or the orientation lock on load. The manual phone checklist is in `docs/measurements.md`.

**Server API for rooms.** `GET/POST /api/rooms`, `PATCH/DELETE /api/rooms/{id}`, `GET /api/rooms/{id}/scans`, `POST /api/rooms/{id}/baseline`, `POST /api/rooms/{id}/scans/{scan}/save` and `/discard`, `DELETE /api/rooms/{id}/scans/{scan}`, `GET /api/rooms/{id}/changes`, `POST /api/rooms/{id}/changes/review`, `GET /api/search`, `GET /api/overlay?room_id=`, `POST /api/guide`, `DELETE /api/data`. The phone's scan and find messages carry `room_id`.

### Objects inspector and corrections

The memory can be wrong (two bottles that are one, a wall that is not an object), so it can be reviewed and corrected. On the laptop, **Objects** in the dashboard header opens the inspector: pick a room and a scan, see the objects grouped by label (instances, sightings, mean confidence), open an instance to see every sighting at a large size with its mask and box, the SAM confidence, the camera pose and the decisions that joined or separated it. From there: merge instances, detach a sighting into its own object, delete an object, mark a label as "not an object" for the room, review the possible-duplicates list, and read the corrections log. On the phone, **Review objects** (on a scan's review or in a room's history) goes label, instances, sightings with the same corrections.

Every correction is recorded (`GET /api/corrections`, with who made it) and is applied again by every later rebuild, so the next frame does not undo it. API: `GET /api/scans/{id}/objects`, `GET /api/scans/{id}/duplicates`, `GET /api/objects/{id}`, `POST /api/objects/merge`, `POST /api/sightings/{id}/detach`, `DELETE /api/objects/{id}`, `GET/POST /api/rooms/{id}/not-objects`, `DELETE /api/rooms/{id}/not-objects/{label}`, `GET /api/corrections`. A sighting's id is its detection's id.

### Calibrate the camera

The field of view depends on the camera mode (resolution, lens, zoom), so it is measured in the exact state the app scans in, and any later change is refused.

1. Open the app on the phone with `?debug`, open Explore (AR) on a room (or start a scan) and, in the dots menu, press **Save sample** while holding a ruler or A4 sheet flat and square to the camera. Do it at two distances (say 40 and 70 cm). Do not touch zoom or switch lenses. The photos and a `.json` sidecar that records the camera state land in `data/frames/`.
2. Measure the sheet's width in pixels in each photo, then run `python scripts/calibrate_fov.py --object-cm 29.7 --shot 40 612 data/frames/<first>.json --shot 70 352 data/frames/<second>.json`. It refuses shots taken in different camera states and writes `data/fov_calibration.json` with the angle and the resolution and zoom it holds for.
3. Restart the server. From then on the server refuses any frame from a camera in another state, and the phone stops the scan with a message naming what changed (zoom, resolution, lens, rotation). `HFOV_DEG=66` sets an angle without recording a camera state, so nothing can be checked.

The app locks zoom at 1x where the camera allows it and checks the camera settings before and after every frame.

### How the memory decides what is one object

Objects are recomputed from every stored detection after each frame, in capture-time order, so the result does not depend on the order frames finished processing (`backend/app/memory.py`). A detection joins the object whose angular footprint it overlaps most, unless two detections of one frame would share it or the colours of the two masks differ too much; edge-clipped fragments join an object when one matches and become objects of their own when seen in two frames. Every decision is logged (`memory` logger, `WorldModel.decisions()`). Each object keeps its confidence, last-seen time, the number of frames that saw it and an anchor uncertainty (disagreement between sightings combined with the field-of-view error), which the API returns and the guide arrow shows as a cone.

Coverage (the ring on the phone, the scan summary, the "not seen" answer) is one definition in `backend/app/coverage.py` and `web/js/coverage.js`, checked against each other by a golden test, and includes three pitch bands (floor, eye level, ceiling).

`python scripts/eval_memory.py` scores the memory on synthetic rooms with known contents (duplicate rate, wrong merges, id switches, find success, order independence) and writes the table to `docs/measurements.md`.

## Laptop dashboard

Open `http://127.0.0.1:8000/dashboard` on the laptop while the phone scans. It shows the room map filling in (compass, north up), counters (frames, objects, seconds, cost), a log of everything the phone and any agent does, the last Reality API call with the photo it was shown, and a compare panel (pick two scans, "What changed?") with before and after photos. A dashboard opened mid-scan catches up from the last 200 events. `/dashboard?demo` plays scripted data without the backend. The page needs the same `MODEL_API_KEY` setup as scanning, because it reads the same server state.

## Reality API (for other agents)

The scanned room is also an MCP server, so another agent can ask about it. It is mounted on the same server at `http://127.0.0.1:8000/mcp` (Streamable HTTP; needs `MODEL_API_KEY` in `.env`, like scanning).

| tool | what it answers |
|---|---|
| `list_rooms()` | the rooms, which one is active, how many objects each remembers, when it was last scanned |
| `where_is(query, room?)` | where something is, from a name or a description ("the thing I charge my laptop with"): compass direction, direction from the scan start, neighbours, a photo reference, last seen, confidence. `status` is `found`, `not_seen` (with how much of the room the latest scan covered) or `unresolved` |
| `list_objects(label?, scan_id?, room?)`, `scan_summary(scan_id?, room?)` | what a scan found, counts per kind, coverage and gaps |
| `what_changed(scan_before?, scan_after?, room?)` | added, removed and moved objects between two scans, plus `unverified` objects in directions only one scan looked at |
| `guide_user_to(query, room?)` | the one action: the connected phone shows the answer and starts the direction arrow |
| `get_frame(frame_id)` | the stored photo (may show people: turn it off with `REALITY_API_FRAMES=0`) |

Every tool takes an optional `room` (an id or a name) and defaults to the active room. With no active room and none named, the answer is `no_active_room` with the list of rooms. Scans of different rooms are refused (`wrong_room`), and `guide_user_to` only guides inside the active room.

```powershell
claude mcp add --transport http reality-os http://127.0.0.1:8000/mcp     # Claude Code as the second agent
.\.venv\Scripts\python.exe scripts
eality_client.py where_is "the thing I drink water from"   # or ask from the command line
.\.venv\Scripts\python.exe scripts
eality_client.py tools
```

It listens on loopback only, and the host header is checked (DNS-rebinding guard); on a LAN bind add your host with `MCP_ALLOWED_HOSTS="192.168.1.5:8443"` and use `ACCESS_TOKEN`. `cd backend; python -m app.mcp_server` runs the same tools over stdio for clients that start their own subprocess (read-only; no live phone or dashboard in that mode). Every agent call is also published on the event feed (`/ws/events`) for the laptop dashboard.

## M0 scripts

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py scripts\sam_latency.py --images data\frames --concepts "red brick,black wheel" --max-sides 640,960,1280 --repeat 3
& $py scripts\spark_grounding.py --images data\frames --concepts "red brick,black wheel"
& $py scripts\spark_plan.py --repeat 5
& $py scripts\voice_ws.py --seconds 30 --lang English,Hindi
```

Each prints markdown tables to paste into `docs/measurements.md` and logs estimated spend to `data/cost_log.jsonl`.

## Models used

Muse Spark 1.3 (Standard mode only), SAM 3.1, Muse Voice Transcribe via the Meta Model API. Open-weight models planned for M4.

## Attribution

No third-party code adapted so far. `meta-sam-parser` is used as a dependency.
