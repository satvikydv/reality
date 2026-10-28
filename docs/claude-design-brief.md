Design the frontend for **Reality OS**, a spatial memory for a room, used from an Android phone in Chrome (portrait, one hand). It is for a hackathon demo that gets judged in 3 minutes and in a 30-second video clip, so it must look striking, feel like a precise instrument, and be understandable at a glance.

## What the product does
You pan the phone around a room once. It watches the camera, notices objects (keys, mug, charger, bottle, laptop...) and remembers where each one is as a compass direction (yaw/pitch), not as a map. Later you ask in plain language ("the thing I charge my laptop with") and it answers with a spoken sentence ("The charger was last seen about 60 degrees to the left of where the scan started, below the laptop"), a replay photo with the object boxed, and, in the next version, an arrow that steers you to it. A later feature compares two scans: what was added, removed or moved.

It is NOT a chatbot and NOT a camera that labels things. Never show a chat thread. The memory and the direction are the hero.

## Screens to design (phone, 390x844 reference, dark only)
1. **Live camera (home).** Full-bleed camera preview. Minimal chrome. Big primary "Scan room" button in the thumb zone, a text/voice "Find" field beside it. Small status pill (connected, sensor ok).
2. **Scanning.** The centrepiece is a **coverage compass ring**: 36 ten-degree segments that fill as the user turns, a "N" marker, a heading dot, and the percentage in the middle. Under it a single coaching line that changes: "slow down", "hold steady", "gap 40 degrees to the left", "almost there". Frame counter and live object count ("14 objects"). Each captured frame gets a subtle shutter pulse and a short vibration. A red "Done" button appears. Make the ring beautiful and easy to read over any video.
3. **Processing and summary.** After the scan the app says what it found: "7 laptops, 6 cables, 5 water bottles, a charger...". Show a compact list or a radial "room map" (objects as labelled dots placed by yaw around the ring, pitch as distance from centre or vertical offset). Show frames, seconds, cost.
4. **Find result.** Spoken answer as large text, the replay photo with an animated highlight box, the object's direction ("60 degrees left, 10 below level"), a "Guide me" button.
5. **Guide mode (design now, wired next).** Live camera with a large direction arrow that rotates smoothly (arrow angle comes from the backend, clockwise from up), the distance in degrees, a one-line spoken hint ("Turn right", "Tilt up", "Almost there, a little left"), and a clear "found it" state that highlights the object in view.
6. **What changed (design now, wired next).** Two scans compared: three groups with icons, **Added / Removed / Moved**, each with a before/after photo pair and a one-line sentence, plus an "unverified" group for areas not rescanned both times.

## Second screen: demo dashboard for a TV (16:9, 1920x1080)
For the live demo on a big screen: the room map with objects appearing as the scan progresses, a log of events ("frame 7 stored: laptop, mouse, cable"), live counters (frames, objects, seconds, cost in dollars), and an "Agent query" panel showing another AI agent asking "where are my keys?" and the answer. Design it with mock data; it will be wired later.

## Visual direction
Dark, calm, precise, like an avionics HUD crossed with a modern camera app. One accent colour (suggest a warm amber, #FFD54F, for heading and highlights) plus green for covered or found, red only for stop and errors. Monospace for numbers, a clean geometric sans for words. Thin lines, generous spacing, no heavy gradients, no stock illustrations. Everything must stay legible over a bright, busy camera feed: use translucent dark panels with blur, strong contrast, large type. Tap targets at least 48px, controls reachable by the thumb at the bottom, respect safe areas. Motion is smooth and meaningful (ring fill, arrow rotation, box highlight), never decorative.

## Technical constraints (important)
- Deliver plain **HTML, CSS and ES modules, no build step, no framework**. Single page `index.html` plus a `tv.html`.
- Camera is a `<video id="v">`. Keep these existing JS modules and call them as they are: `/js/camera.js`, `/js/ws.js` (`FrameSocket`), `/js/orientation.js` (`OrientationTracker`), `/js/keyframe.js` (`Coverage`, `KeyframePolicy`), `/js/scan.js` (`ScanController`). Do not rewrite their logic. Restyle and re-lay out the page around them.
- Existing element ids the current script uses (keep or map them): `v`, `hud`, `ring` (canvas, you may replace the drawing), `scan`, `scanstatus`, `scanline`, `hint`, `result`, `resulttext`, `replay` (canvas), `scanbtn`, `donebtn`, `q`, `findbtn`. Dev tools (flip camera, stream, save sample) stay available but tucked away in a collapsed section.
- `ScanController` calls `onChange(state, coverage)`. State fields: `phase` (idle, starting, scanning, finishing, done, error), `sent`, `done`, `rejected`, `objects`, `names` (last frame's object names), `hint`, `status`, `yaw`, `error`, `summary`. `coverage.bins` is an array of 36 booleans, `coverage.fraction` is 0 to 1.
- Server events over the WebSocket (JSON): `scan_started`, `frame_queued`, `frame_done {seq, stored, names, kept, objects}`, `scan_summary {speech, objects:[{label, yaw, pitch, n_obs}], coverage, frames, seconds, cost_usd}`, `find_result {speech, concept, found, object, replay:{frame_id, box:[x1,y1,x2,y2], width, height}}`. Replay image is `/world/frame/<id>.jpg`.
- The page must work with the browser's built-in speech (`speechSynthesis`) for spoken answers and `navigator.vibrate` for haptics, and must degrade without them.
- Portrait only, no horizontal scroll, works at 360 to 430px width. Target 60 fps animations without heavy libraries.

## Quality bar
A judge should understand the product from the first 5 seconds of the screen recording, with no explanation: pan, ring fills, "I found 14 objects", ask a question, get the answer with a boxed photo. Make that sequence feel inevitable and polished.
