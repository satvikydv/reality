# Reality OS

An agent that watches the physical world through a phone camera, remembers it, guides and verifies real-world tasks, and exposes that world to other agents through an API.

Entry for the Meta Global AI Developer Hackathon (Indie track, solo). Experimental prototype; no medical or safety-critical claims.

> Status: **M0 (de-risk)**. See `PRD.md` for scope and `docs/measurements.md` for measured results.

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

Phone (same Wi-Fi): open `https://<laptop-LAN-IP>:8443`, accept the certificate warning once, allow the camera. The Windows Wi-Fi profile must be **Private**, or allow inbound TCP 8443 (admin PowerShell):

```powershell
New-NetFirewallRule -DisplayName "RealityOS dev 8443" -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow -Profile Any
```

Use **Save sample** on the phone page to drop full-size frames into `data/frames/`.

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
