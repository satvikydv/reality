"""Reality OS backend. M0: serves the phone page and a WebSocket frame echo."""
from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SAMPLES = ROOT / "data" / "frames"

app = FastAPI(title="Reality OS")

# Binary frame layout from the client: flags u8 | seq u32 | client_ts f64 (ms) | JPEG bytes (big endian)
HEADER = struct.Struct(">BId")  # 13 bytes
FLAG_SAVE = 1


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/tv")
async def tv():
    return FileResponse(WEB / "tv.html")


@app.get("/debug")
async def debug():
    return FileResponse(WEB / "debug.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": time.time()}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    saved = 0
    try:
        while True:
            msg = await sock.receive_bytes()
            t_in = time.perf_counter()
            flags, seq, client_ts = HEADER.unpack_from(msg)
            jpeg = msg[HEADER.size:]
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
                saved += 1
                reply["saved"] = name
                reply["saved_total"] = len(list(SAMPLES.glob("*.jpg")))
            reply["server_ms"] = round((time.perf_counter() - t_in) * 1000, 2)
            await sock.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        pass


app.mount("/js", StaticFiles(directory=WEB / "js"), name="js")
