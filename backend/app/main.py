"""Reality OS backend: serves the phone page and the WebSocket (frame echo for M0 checks, live room scan)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import struct
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from mcp.server.transport_security import TransportSecuritySettings

from app.auth import AccessTokenMiddleware
from app import mcp_server, reality_api, scan_service

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SAMPLES = ROOT / "data" / "frames"

_mcp_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"] + [h for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
_mcp_current: dict = {"app": None}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Reality API for other agents: an MCP server over Streamable HTTP at /mcp, sharing this process's memory and
    event bus. A session manager can only run once, so each startup builds its own server."""
    server = mcp_server.build_server(lambda: get_service())
    _mcp_current["app"] = server.streamable_http_app(
        streamable_http_path="/mcp",
        # DNS-rebinding protection stays on; add LAN hosts with MCP_ALLOWED_HOSTS="192.168.1.5:8443,laptop.local:8443"
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=_mcp_hosts, allowed_origins=["*"]))
    try:
        async with server.session_manager.run():
            yield
    finally:
        _mcp_current["app"] = None


class McpEndpoint:
    async def __call__(self, scope, receive, send):
        inner = _mcp_current["app"]
        if inner is None:
            return await Response("MCP server not running", status_code=503)(scope, receive, send)
        await inner(scope, receive, send)


app = FastAPI(title="Reality OS", lifespan=lifespan)
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


app.router.routes.append(Route("/mcp", McpEndpoint()))


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/dashboard")
async def dashboard():
    return FileResponse(WEB / "dashboard.html")


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


def _service_or_503() -> scan_service.ScanService:
    try:
        return get_service()
    except Exception as e:  # noqa: BLE001 - usually MODEL_API_KEY missing
        raise HTTPException(503, f"{type(e).__name__}: {e}"[:300])


@app.get("/api/scans")
async def api_scans():
    """Every scan, newest first (for the dashboard's history and compare pickers)."""
    return {"scans": _service_or_503().wm.list_scans()}


@app.get("/api/changes")
async def api_changes(before: int | None = None, after: int | None = None):
    """What changed between two scans (default: the latest against the one before): same answer the Reality API gives."""
    return reality_api.what_changed(_service_or_503(), before, after)


@app.get("/api/overlay")
async def api_overlay(scan_id: int | None = None):
    """Objects of a scan (default: the latest) with what the phone needs to draw them world-locked over the camera."""
    return reality_api.overlay(_service_or_503(), scan_id)


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

    async def forward_guide_requests(sub) -> None:
        while True:
            await send(await sub.get())

    guide_sub = forwarder = None
    try:
        guide_sub = get_service().bus.subscribe("phone", types={"guide_request"})  # lets an agent send this phone somewhere
        forwarder = asyncio.create_task(forward_guide_requests(guide_sub))
    except Exception:  # noqa: BLE001 - no model key yet: scanning is unavailable anyway, the echo tools still work
        pass
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
        if forwarder is not None:
            forwarder.cancel()
            guide_sub.close()
        if session is not None:
            await session.close()


@app.websocket("/ws/events")
async def ws_events(sock: WebSocket):
    """Live feed for the laptop dashboard: everything the phone and the Reality API do, newest 200 events first."""
    await sock.accept()
    try:
        bus = get_service().bus
    except Exception as e:  # noqa: BLE001
        await sock.send_text(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"[:300]}))
        await sock.close()
        return
    async with bus.subscribe("dashboard", replay=200) as sub:
        reader = asyncio.create_task(sock.receive())  # only here to notice the dashboard closing
        try:
            while True:
                getter = asyncio.create_task(sub.get())
                done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    getter.cancel()
                    return
                await sock.send_text(json.dumps(getter.result()))
        except Exception:  # noqa: BLE001 - socket closed mid-send
            return
        finally:
            reader.cancel()
            with contextlib.suppress(Exception):
                await sock.close()


app.mount("/js", StaticFiles(directory=WEB / "js"), name="js")
app.mount("/css", StaticFiles(directory=WEB / "css"), name="css")
