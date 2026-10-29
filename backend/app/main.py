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
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from mcp.server.transport_security import TransportSecuritySettings

from app.auth import AccessTokenMiddleware
from app import inspector, mcp_server, reality_api, scan_service, status
from app.world_model import RoomNameError

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
key_check = status.KeyCheck()  # tests replace this


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


@app.get("/api/status")
async def api_status(refresh: bool = False):
    """Home screen health: server up (this answer), model API key state, the newest scan. Cheap enough to poll."""
    return await status.build(get_service, key_check, force=refresh)


@app.delete("/api/scans/{scan_id}")
async def api_delete_scan(scan_id: int):
    """Delete one scan with its frames, detections and objects."""
    if not _service_or_503().wm.delete_scan(scan_id):
        raise HTTPException(404, f"no scan {scan_id}")
    return {"deleted": [scan_id]}


@app.delete("/api/scans")
async def api_delete_scans():
    """Delete every scan (the phone asks for a confirmation first)."""
    wm = _service_or_503().wm
    ids = [s["id"] for s in wm.list_scans()]
    return {"deleted": [i for i in ids if wm.delete_scan(i)]}


@app.get("/api/changes")
async def api_changes(before: int | None = None, after: int | None = None, room_id: int | None = None):
    """What changed between two scans of one room (default room: the active one): same answer the Reality API gives.
    Two scans from different rooms are a 400."""
    svc = _service_or_503()
    if room_id is None and before is not None and after is not None:  # the room is the one both scans belong to
        a, b = svc.wm.get_scan(before), svc.wm.get_scan(after)
        if a and b and a["room_id"] != b["room_id"]:
            raise HTTPException(400, "those scans are from different rooms; scans of different rooms cannot be compared")
        room_id = a["room_id"] if a else None
    return _changes_for(svc, before, after, room_id)


def _changes_for(svc, before, after, room_id):
    r = reality_api.what_changed(svc, before, after, room_id)
    if r.get("status") in ("wrong_room", "not_saved"):
        raise HTTPException(400, r["message"])
    return r


@app.get("/api/overlay")
async def api_overlay(scan_id: int | None = None, room_id: int | None = None):
    """Objects of one room's memory (default room: the active one) with what the phone needs to draw them over the camera."""
    return reality_api.overlay(_service_or_503(), scan_id, room_id)


class RoomIn(BaseModel):
    name: str
    active: bool = False


class RoomPatch(BaseModel):
    name: str | None = None
    active: bool | None = None


class BaselineIn(BaseModel):
    scan_id: int | None = None


class ReviewIn(BaseModel):
    before: int
    after: int
    key: str
    state: str | None = None


class GuideIn(BaseModel):
    object_id: int
    room_id: int | None = None


def _svc() -> scan_service.ScanService:
    return _service_or_503()


def _room_or_404(room_id: int) -> dict:
    room = _svc().wm.get_room(room_id)
    if room is None:
        raise HTTPException(404, f"no room {room_id}")
    return room


def _http_for(result: dict) -> dict:
    """Reality API answers carry a `status`; the ones that mean "that request mixes rooms" or names nothing are HTTP errors."""
    st = result.get("status")
    if st in ("wrong_room", "stale", "bad_request", "not_saved"):
        raise HTTPException(400, result.get("message") or st)
    if st in ("no_such_room", "no_such_scan", "no_such_object"):
        raise HTTPException(404, result.get("message") or st)
    return result


@app.get("/api/rooms")
async def api_rooms():
    """Every room with its card data, the active room, and the most recently seen objects across all of them."""
    svc = _svc()
    rooms = svc.wm.list_rooms()
    return {"rooms": rooms, "active_id": next((r["id"] for r in rooms if r["active"]), None), "recent": reality_api.recent_objects(svc)}


@app.post("/api/rooms", status_code=201)
async def api_create_room(body: RoomIn):
    try:
        return _svc().wm.create_room(body.name, active=body.active)
    except RoomNameError as e:
        raise HTTPException(409 if e.duplicate else 400, str(e))


@app.get("/api/rooms/{room_id}")
async def api_room(room_id: int):
    return _http_for(reality_api.room_detail(_svc(), room_id))


@app.patch("/api/rooms/{room_id}")
async def api_patch_room(room_id: int, body: RoomPatch):
    wm = _svc().wm
    _room_or_404(room_id)
    try:
        if body.name is not None:
            wm.rename_room(room_id, body.name)
        if body.active is True:
            wm.set_active(room_id)
        elif body.active is False and (wm.active_room() or {}).get("id") == room_id:
            wm.set_active(None)
    except RoomNameError as e:
        raise HTTPException(409 if e.duplicate else 400, str(e))
    return wm.room_stats(wm.get_room(room_id))


@app.delete("/api/rooms/{room_id}")
async def api_delete_room(room_id: int):
    """Erase a room with all its scans, frames and photos."""
    _room_or_404(room_id)
    _svc().wm.delete_room(room_id)
    return {"deleted": room_id}


@app.get("/api/rooms/{room_id}/scans")
async def api_room_scans(room_id: int):
    """A room's scan history, newest first, with which scan is the baseline and which is an unsaved draft."""
    _room_or_404(room_id)
    return {"room_id": room_id, "scans": _svc().wm.room_scans(room_id)}


def _scan_of_room(room_id: int, scan_id: int) -> dict:
    _room_or_404(room_id)
    s = _svc().wm.get_scan(scan_id)
    if s is None:
        raise HTTPException(404, f"no scan {scan_id}")
    if s["room_id"] != room_id:
        raise HTTPException(400, f"scan {scan_id} is not a scan of room {room_id}")
    return s


@app.post("/api/rooms/{room_id}/baseline")
async def api_set_baseline(room_id: int, body: BaselineIn):
    """Choose the saved scan What changed compares against (null: the scan before the latest)."""
    _room_or_404(room_id)
    try:
        _svc().wm.set_baseline(room_id, body.scan_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"room_id": room_id, "baseline": body.scan_id}


@app.post("/api/rooms/{room_id}/scans/{scan_id}/save")
async def api_save_scan(room_id: int, scan_id: int):
    """Keep a reviewed scan: it becomes the room's memory (the older scans stay for What changed)."""
    s = _scan_of_room(room_id, scan_id)
    if s["status"] == "saved":
        return {"saved": scan_id, "already": True}
    if not _svc().wm.save_scan(scan_id):
        raise HTTPException(400, "this scan stored no frames, so there is nothing to save")
    return {"saved": scan_id}


@app.post("/api/rooms/{room_id}/scans/{scan_id}/discard")
async def api_discard_scan(room_id: int, scan_id: int):
    """Throw away a scan that was not saved. A saved scan is deleted with DELETE instead."""
    s = _scan_of_room(room_id, scan_id)
    if s["status"] != "draft":
        raise HTTPException(409, "this scan is already saved; delete it from the room's history instead")
    _svc().wm.delete_scan(scan_id)
    return {"discarded": scan_id}


@app.delete("/api/rooms/{room_id}/scans/{scan_id}")
async def api_delete_room_scan(room_id: int, scan_id: int):
    _scan_of_room(room_id, scan_id)
    _svc().wm.delete_scan(scan_id)
    return {"deleted": [scan_id]}


@app.get("/api/rooms/{room_id}/changes")
async def api_room_changes(room_id: int, before: int | None = None, after: int | None = None):
    """What changed between two saved scans of this room. Scans of another room are a 400."""
    _room_or_404(room_id)
    return _changes_for(_svc(), before, after, room_id)


@app.post("/api/rooms/{room_id}/changes/review")
async def api_review_change(room_id: int, body: ReviewIn):
    _room_or_404(room_id)
    return _http_for(reality_api.review_change(_svc(), room_id, body.before, body.after, body.key, body.state))


class MergeIn(BaseModel):
    ids: list[int]
    source: str = "api"


class LabelIn(BaseModel):
    label: str
    source: str = "api"


def _announce(svc, kind: str, **info) -> None:
    """Every correction is also an event, so the dashboard's log shows it as it happens."""
    last = svc.wm.list_corrections(limit=1)
    svc.bus.publish({"type": "correction", "kind": kind, "source": (last[0]["source"] if last else "api"), **info})


@app.get("/api/scans/{scan_id}/objects")
async def api_scan_objects(scan_id: int):
    """What a scan believes, grouped by label: instances, sightings, mean confidence, with a thumbnail per instance."""
    r = inspector.scan_objects(_svc().wm, scan_id)
    if r is None:
        raise HTTPException(404, f"no scan {scan_id}")
    return r


@app.get("/api/scans/{scan_id}/duplicates")
async def api_scan_duplicates(scan_id: int):
    """Pairs of objects in this scan that may be one thing (close, never seen in one frame), strongest first."""
    if _svc().wm.get_scan(scan_id) is None:
        raise HTTPException(404, f"no scan {scan_id}")
    return {"scan_id": scan_id, "duplicates": inspector.possible_duplicates(_svc().wm, scan_id)}


@app.get("/api/objects/{object_id}")
async def api_object(object_id: int):
    """One instance: its sightings (frame, box, mask, camera pose, SAM confidence), the memory's decisions and the corrections made to it."""
    r = inspector.object_detail(_svc().wm, object_id)
    if r is None:
        raise HTTPException(404, f"no object {object_id}")
    return r


@app.post("/api/objects/merge")
async def api_merge_objects(body: MergeIn):
    """The user says these instances are one object (same scan, same label). Recorded as a correction and kept by every later rebuild."""
    svc = _svc()
    try:
        o = svc.wm.merge_objects(body.ids, source=body.source)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _announce(svc, "merge", object_id=o["id"], label=o["label"])
    return {"merged_into": o["id"], "label": o["label"], "scan_id": o["scan_id"], "detail": inspector.object_detail(svc.wm, o["id"])}


@app.post("/api/sightings/{sighting_id}/detach")
async def api_detach_sighting(sighting_id: int, source: str = "api"):
    """Take one sighting out of its object; it becomes an object of its own."""
    svc = _svc()
    try:
        o = svc.wm.detach_sighting(sighting_id, source=source)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _announce(svc, "detach", object_id=o["id"], label=o["label"])
    return {"object_id": o["id"], "label": o["label"], "scan_id": o["scan_id"]}


@app.delete("/api/objects/{object_id}")
async def api_delete_object(object_id: int, source: str = "api"):
    """The user says this is not a real object: its sightings are ignored from now on."""
    svc = _svc()
    try:
        r = svc.wm.delete_object(object_id, source=source)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))
    _announce(svc, "delete", **r)
    return r


@app.get("/api/rooms/{room_id}/not-objects")
async def api_not_objects(room_id: int):
    _room_or_404(room_id)
    return {"room_id": room_id, "labels": _svc().wm.not_object_labels(room_id)}


@app.post("/api/rooms/{room_id}/not-objects")
async def api_mark_not_object(room_id: int, body: LabelIn):
    """Objects with this label are not objects in this room: they leave every scan of the room, now and in future scans."""
    svc = _svc()
    _room_or_404(room_id)
    try:
        r = svc.wm.mark_not_object(room_id, body.label, source=body.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _announce(svc, "mark_not_object", **r)
    return r


@app.delete("/api/rooms/{room_id}/not-objects/{label}")
async def api_unmark_not_object(room_id: int, label: str, source: str = "api"):
    svc = _svc()
    _room_or_404(room_id)
    if not svc.wm.unmark_not_object(room_id, label, source=source):
        raise HTTPException(404, f"{label!r} is not marked as not an object in this room")
    _announce(svc, "unmark_not_object", room_id=room_id, label=label)
    return {"room_id": room_id, "label": label}


@app.get("/api/corrections")
async def api_corrections(scan_id: int | None = None, room_id: int | None = None, limit: int = 200):
    """Every manual correction, newest first."""
    return {"corrections": _svc().wm.list_corrections(scan_id=scan_id, room_id=room_id, limit=min(limit, 1000))}


@app.get("/api/search")
async def api_search(q: str = "", room_id: int | None = None):
    """Objects by name across rooms (or in one room), grouped per room. Read-only."""
    return _http_for(reality_api.search(_svc(), q, room_id))


@app.post("/api/guide")
async def api_guide(body: GuideIn):
    """Check that the phone may guide to this object: it must be in the active room. Another room is a 400."""
    return _http_for(reality_api.guide_target(_svc(), body.object_id, body.room_id))


@app.delete("/api/data")
async def api_delete_all_data():
    """Every room and scan, with their frames and photos."""
    return _svc().wm.delete_all()


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
                        await session.start(ctl.get("name"), ctl.get("room_id"))
                    elif kind == "scan_end":
                        if session is None or not session.active:
                            await send({"type": "error", "where": "scan_end", "message": "no scan running"})
                        else:
                            await session.end()
                    elif kind == "scan_cancel":
                        if session is not None and session.active:
                            await session.cancel()
                    elif kind == "find":
                        await send(await get_service().find(str(ctl.get("query", "")), room_id=ctl.get("room_id"), all_rooms=bool(ctl.get("all_rooms"))))
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
