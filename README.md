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
6. On the phone, open Chrome and go to **http://localhost:8000**. Allow the camera when asked. `http://localhost:8000/probe` shows whether the camera and motion sensors work (results go in `docs/measurements.md`).

If the port is already in use, set `PLAIN_HTTP_PORT` and change the port in `scripts/usb_connect.*` to match.

## Scan a room

Needs `MODEL_API_KEY` in `.env`. On the phone page tap **Scan room**, then pan slowly one way, about 20 to 30 s for a full circle, holding the phone upright and roughly level. The page sends a frame whenever you have turned about 18 degrees and the phone is steady; the ring shows coverage and the hint names the next gap. It finishes by itself at 97% coverage, or tap **Done**. The summary lists what was found, and **Find** answers things like "the thing I drink water from" with a replay frame.

Server progress goes to the uvicorn log. To keep a log file while the server runs in the background:

```powershell
$env:PLAIN_HTTP = "1"; Start-Process .\.venv\Scripts\python.exe -ArgumentList "backend\run.py" -RedirectStandardError data\server.err.log -RedirectStandardOutput data\server.log -WindowStyle Hidden
Get-Content data\server.err.log -Wait -Tail 20          # one line per scan frame: names, objects, wait, cost
.\.venv\Scripts\python.exe scripts\replay_scan.py --only 1301   # replay saved frames through the same WebSocket (no phone)
```

Tests: `pytest tests` (server), `node --test tests/js/keyframe.test.mjs tests/js/guide.test.mjs` (auto-capture and guidance maths; the JS guidance is checked against the Python one).

The phone UI (`web/index.html`, `web/css/theme.css`, `web/js/app.js`) also runs without the backend: open `/?demo` for mock data, or `/?demo&screen=summary|find|guide|changes|scan` to jump to a screen. It falls back to demo mode by itself when the server is not reachable.

**World-locked overlay.** On the home, scan and guide screens the camera view shows the remembered objects where they are: each one's stored direction is projected through the live phone pose and the server's field of view, with its label and an outline. The outline is the stored SAM mask carried into the current view by the rotation-only homography `H = K R K^-1`; objects stored without a mask (or from before masks were kept) get their angular footprint instead, drawn dashed. Objects whose sightings disagree by more than 12 degrees are not drawn, and opacity fades with the time since the object was last seen. Nothing is shown as a distance, because only directions are known. The AR toggle on the home and guide screens turns the objects off; the compass tape, horizon and guide arrow stay. The server serves the data at `/api/overlay` (the field of view is the calibrated one, or 60 degrees as a placeholder: see "Calibrate the camera"). The projection math has a Python reference in `backend/app/anchoring.py` and `tests/js/project.test.mjs` checks the phone's JavaScript against it. The dev menu (the dots) shows the live frame rate and draw time.

After two scans of the same room, "What changed?" on the home screen compares the latest scan with the one before and shows what moved, disappeared or appeared, with before and after photos. Directions that were not scanned both times are listed as not checked rather than reported as changes.

### Calibrate the camera

The field of view depends on the camera mode (resolution, lens, zoom), so it is measured in the exact state the app scans in, and any later change is refused.

1. Open the app on the phone and, in the dots menu, press **Save sample** while holding a ruler or A4 sheet flat and square to the camera. Do it at two distances (say 40 and 70 cm). Do not touch zoom or switch lenses. The photos and a `.json` sidecar that records the camera state land in `data/frames/`.
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
| `where_is(query)` | where something is, from a name or a description ("the thing I charge my laptop with"): compass direction, direction from the scan start, neighbours, a photo reference, last seen, confidence. `status` is `found`, `not_seen` (with how much of the room the latest scan covered) or `unresolved` |
| `list_objects(label?, scan_id?)`, `scan_summary(scan_id?)` | what a scan found, counts per kind, coverage and gaps |
| `what_changed(scan_before?, scan_after?)` | added, removed and moved objects between two scans, plus `unverified` objects in directions only one scan looked at |
| `guide_user_to(query)` | the one action: the connected phone shows the answer and starts the direction arrow |
| `get_frame(frame_id)` | the stored photo (may show people: turn it off with `REALITY_API_FRAMES=0`) |

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
