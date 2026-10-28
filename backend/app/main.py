"""Reality OS backend: serves the phone page and the WebSocket (frame echo for M0 checks, live room scan)."""
from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import AccessTokenMiddleware
from app import scan_service

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SAMPLES = ROOT / "data" / "frames"

app = FastAPI(title="Reality OS")
app.add_middleware(AccessTokenMiddleware)

# Binary frame layout from the client (big endian):
#   flags u8 | seq u32 | client_ts f64 (ms) | meta_len u16 | meta (UTF-8 JSON, may be empty) | JPEG bytes
# Text messages from the client are JSON: {"type": "scan_start" | "scan_end" | "find", ...}.
HEADER = struct.Struct(">BIdH")  # 15 bytes
FLAG_SAVE = 1  # store the frame and its sidecar in data/frames (M0 samples)
FLAG_SCAN = 2  # keyframe for the running scan

_service: scan_service.ScanService | None = None
service_factory = scan_service.build_default_service  # tests replace this


def get_service() -> scan_service.ScanService:
    global _service
    if _service is None:
        _service = service_factory()
    return _service


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/tv")
async def tv():
    return FileResponse(WEB / "tv.html")


@app.get("/debug")
async def debug():
    return FileResponse(WEB / "debug.html")


@app.get("/probe")
async def probe():
    return FileResponse(WEB / "probe.html")


@app.get("/orientation")
async def orientation():
    return FileResponse(WEB / "orientation.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": time.time()}


@app.get("/world/frame/{frame_id}.jpg")
async def world_frame(frame_id: int):
    """A stored keyframe (replay for find results)."""
    jpeg = _service.wm.frame_jpeg(frame_id) if _service else None
    if jpeg is None:
        raise HTTPException(404, "no such frame")
    return Response(jpeg, media_type="image/jpeg")


def parse_frame(msg: bytes) -> tuple[int, int, float, dict, bytes]:
    flags, seq, client_ts, meta_len = HEADER.unpack_from(msg)
    meta_end = HEADER.size + meta_len
    try:
        meta = json.loads(msg[HEADER.size:meta_end]) if meta_len else {}
    except ValueError:
        meta = {}
    return flags, seq, client_ts, meta if isinstance(meta, dict) else {}, msg[meta_end:]


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    session: scan_service.ScanSession | None = None

    async def send(obj: dict) -> None:
        await sock.send_text(json.dumps(obj))

    async def fail(where: str, e: Exception) -> None:
        await send({"type": "error", "where": where, "message": f"{type(e).__name__}: {e}"[:300]})

    try:
        while True:
            msg = await sock.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text") is not None:  # control message
                try:
                    ctl = json.loads(msg["text"])
                    kind = ctl.get("type")
                except (ValueError, AttributeError):
                    continue
                try:
                    if kind == "scan_start":
                        if session is None:
                            session = get_service().session(send)
                        await session.start(ctl.get("name"))
                    elif kind == "scan_end":
                        if session is None or not session.active:
                            await send({"type": "error", "where": "scan_end", "message": "no scan running"})
                        else:
                            await session.end()
                    elif kind == "find":
                        await send(await get_service().find(str(ctl.get("query", ""))))
                except Exception as e:  # noqa: BLE001 - report to the phone, keep the socket
                    await fail(kind or "control", e)
                continue

            msg_bytes = msg.get("bytes")
            if not msg_bytes:
                continue
            t_in = time.perf_counter()
            flags, seq, client_ts, meta, jpeg = parse_frame(msg_bytes)
            if flags & FLAG_SCAN:
                if session is None:
                    await send({"type": "frame_rejected", "seq": seq, "reason": "no scan running"})
                else:
                    await session.submit(seq, jpeg, meta)
                continue
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            reply = {"seq": seq, "client_ts": client_ts, "bytes": len(jpeg)}
            if img is None:
                reply["error"] = "undecodable jpeg"
            else:
                reply["w"], reply["h"] = int(img.shape[1]), int(img.shape[0])
            if flags & FLAG_SAVE and img is not None:
                SAMPLES.mkdir(parents=True, exist_ok=True)
                name = f"phone-{time.strftime('%Y%m%d-%H%M%S')}-{seq}.jpg"
                (SAMPLES / name).write_bytes(jpeg)
                # Sidecar: orientation (absolute + relative sources) and capture info for this frame.
                sidecar = {"frame": name, "seq": seq, "client_ts_ms": client_ts, "saved_at": time.time(),
                           "width": int(img.shape[1]), "height": int(img.shape[0]), **meta}
                (SAMPLES / name).with_suffix(".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
                reply["saved"] = name
                reply["orientation"] = {k: bool(isinstance(meta.get("orientation"), dict) and meta["orientation"].get(k))
                                        for k in ("absolute", "relative")}
                reply["saved_total"] = len(list(SAMPLES.glob("*.jpg")))
            reply["server_ms"] = round((time.perf_counter() - t_in) * 1000, 2)
            await send(reply)
    finally:
        if session is not None:
            await session.close()


app.mount("/js", StaticFiles(directory=WEB / "js"), name="js")
