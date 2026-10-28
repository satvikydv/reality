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
