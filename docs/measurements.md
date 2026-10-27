# Measurements

Every number here comes from a script in `scripts/` run on real data. Raw JSON lands in `data/results/` (gitignored); paste the printed tables below with the date and setup.

Status legend: **TODO** not run yet · **DONE** measured.

## M0 checklist

| Item | Script | Status |
|---|---|---|
| Phone camera over HTTPS + WS echo | `backend/run.py` | server side tested (localhost + LAN IP); **phone not yet tested** |
| SAM keyframe latency / matches | `scripts/sam_latency.py` | TODO (needs `MODEL_API_KEY` + phone frames) |
| Orientation drift, absolute vs relative | `/orientation` page | TODO (page built; needs a phone and 3 full turns) |
| Spark box grounding vs SAM | `scripts/spark_grounding.py` | TODO |
| Spark structured plan | `scripts/spark_plan.py` | TODO |
| Voice realtime from mic | `scripts/voice_ws.py` | TODO |

## [VERIFY] items to close in M0

- [ ] SAM stream event shape works with `meta-sam-parser` + `openai` SDK `AsyncStream` (script uses it directly)
- [ ] `metadata` (`mask_encoding`, `include_confidence`) accepted on `/v1/responses` for `sam-3.1`
- [ ] Spark native box coordinate convention (script scores pixels / 0-1000 / 0-1)
- [ ] Spark strict `json_schema` accepts the recursive tagged-union plan schema (else fall back to JSON mode)
- [ ] Voice handshake JSON shape (flat as in PRD 7.3) and event field names (`text` vs `transcript`)
- [ ] Which Spark `reasoning.effort` values the endpoint accepts

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

**Not measured yet.** Procedure: open `/orientation` on the phone (`http://localhost:8000/orientation` over USB), tap **Start sensors**, point the camera at a fixed target, tap **Mark START**, turn on the spot a full 360° in about 30 s back to the same target, hold still 2 s, tap **Mark END**. Do 3 trials. The page averages 0.5 s around each mark, reports the signed yaw/pitch/roll difference per source, and flags trials whose net turn is not within 45° of 360°. **Copy results** gives the JSON.

Sources: absolute = `AbsoluteOrientationSensor` (fallback `deviceorientationabsolute`), magnetometer-referenced; relative = `RelativeOrientationSensor` (fallback `deviceorientation`), gyro only, arbitrary zero per session. Angles are the rear camera's pointing direction (yaw clockwise from north, pitch up positive).

| Trial | Source | dYaw (deg) | dPitch (deg) | dRoll (deg) | Net turn (deg) | Duration (s) | API used |
|---|---|---|---|---|---|---|---|
| 1 | absolute | TODO | TODO | TODO | TODO | TODO | TODO |
| 1 | relative | TODO | TODO | TODO | TODO | TODO | TODO |

Decision (pending data): use the source with the lower mean |dYaw| over valid trials. Caveat for what-changed (PRD F13): a relative source has an arbitrary yaw zero per session, so comparing two scans needs the absolute source or a landmark-based yaw alignment. Record the choice here with the numbers.

### SAM latency
_TODO_

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
