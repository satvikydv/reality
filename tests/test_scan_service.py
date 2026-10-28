import asyncio
import json
import struct

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main
from app.reasoner.base import Result
from app.sam_client import SamCall, SamObject
from app.scan import ScanPipeline
from app.scan_service import ScanService, count_phrase, yaw_coverage
from app.world_model import WorldModel

HEADER = struct.Struct(">BIdH")


def jpeg(w=480, h=640):
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 90, dtype=np.uint8))
    return buf.tobytes()


def meta(yaw, pitch=-10.0):
    return {"orientation": {"absolute": {"yaw": yaw, "pitch": pitch, "roll": 0.0, "api": "sensor:Abs"},
                            "relative": {"yaw": yaw, "pitch": pitch, "roll": 0.0}}, "camera": {"screen_angle": 0}}


def frame_msg(seq, flags, m, data):
    mb = json.dumps(m).encode()
    return HEADER.pack(flags, seq, 1.0, len(mb)) + mb + data


class FakeReasoner:
    async def respond(self, messages, *, schema=None, **kw):
        if "objects" in schema["properties"]:
            parsed = {"objects": ["mug"]}
        else:
            parsed = {"concept": "mug", "alternates": [], "confidence": 0.9}
        return Result(text="", parsed=parsed, input_tokens=100, output_tokens=10, latency_ms=1.0)


async def seg(data_url, concept):
    await asyncio.sleep(0.01)
    return SamCall([SamObject("1", (220.0, 300.0, 260.0, 340.0), 0.9, None)], 5.0, 2.0, "completed", 0)


def make_service(tmp_path, **kw):
    wm = WorldModel(frames_dir=tmp_path / "frames")
    reasoner = FakeReasoner()
    return ScanService(wm, ScanPipeline(wm, reasoner, seg), reasoner, **kw)


def test_yaw_coverage_and_gaps():
    f = lambda y: {"yaw": y, "hfov_deg": 50.0}  # noqa: E731 - each frame covers yaw +- 20
    full = yaw_coverage([f(y) for y in range(0, 360, 30)])
    assert full == {"covered_deg": 360, "gaps": []}
    part = yaw_coverage([f(10), f(40)])  # covers -10..60
    assert part["covered_deg"] == 71 and part["gaps"] == [[61, 349]]
    across = yaw_coverage([f(180)])  # one gap wrapping north is a single interval
    assert across["gaps"] == [[201, 159]]
    assert yaw_coverage([]) == {"covered_deg": 0, "gaps": [[0, 359]]}


def test_count_phrase():
    assert count_phrase(["mug", "laptop", "mug"]) == "2 mugs and a laptop"
    assert count_phrase(["keys"]) == "keys" and count_phrase(["keys", "keys"]) == "2 keys"
    assert count_phrase(["mouse"] * 3 + ["water bottle"] * 2) == "3 mice and 2 water bottles"
    assert count_phrase([]) == ""
    assert count_phrase(["mug", "laptop", "keys"]) == "a mug, a laptop and keys"


def test_session_scan_end_to_end(tmp_path):
    svc = make_service(tmp_path)
    events = []

    async def go():
        async def send(o):
            events.append(o)
        s = svc.session(send)
        await s.start("room")
        for i, y in enumerate((0, 20, 40)):
            await s.submit(i + 1, jpeg(), meta(y))
        await s.submit(9, jpeg(), {})  # no orientation
        summary = await s.end()
        assert not s.active
        return summary

    summary = asyncio.run(go())
    types = [e["type"] for e in events]
    assert types[0] == "scan_started" and types.count("frame_queued") == 3 and types.count("frame_done") == 3
    assert {"type": "frame_rejected", "seq": 9, "reason": "no orientation with the frame"} in events
    assert summary["frames"] == {"received": 3, "stored": 3, "failed": 0, "rejected": 1}
    assert summary["objects"][0]["label"] == "mug" and summary["objects"][0]["n_obs"] >= 1
    assert summary["speech"].startswith("I found 3 mugs.") and summary["cost_usd"] > 0
    assert svc.wm.list_frames(summary["scan_id"]) and len(svc.wm.list_frames(summary["scan_id"])) == 3


def test_queue_full_and_failed_frames(tmp_path):
    svc = make_service(tmp_path, workers=1, max_queue=1)
    events = []

    async def go():
        async def send(o):
            events.append(o)
        s = svc.session(send)
        await s.start()
        await s.submit(1, jpeg(), meta(0))
        await s.submit(2, jpeg(), meta(10))
        await s.submit(3, jpeg(), meta(20))  # worker has not started yet: queue (size 1) is full
        return await s.end()

    summary = asyncio.run(go())
    assert any(e["type"] == "frame_rejected" and "queue full" in e["reason"] for e in events)
    assert summary["frames"]["received"] + summary["frames"]["rejected"] == 3

    class BoomReasoner(FakeReasoner):
        async def respond(self, *a, **k):
            raise RuntimeError("boom")

    wm = WorldModel()
    svc2 = ScanService(wm, ScanPipeline(wm, BoomReasoner(), seg), BoomReasoner())
    ev2 = []

    async def go2():
        async def send(o):
            ev2.append(o)
        s = svc2.session(send)
        await s.start()
        await s.submit(1, jpeg(), meta(0))
        return await s.end()

    s2 = asyncio.run(go2())
    assert [e for e in ev2 if e["type"] == "frame_error"][0]["error"].startswith("RuntimeError: boom")
    assert s2["frames"]["failed"] == 1 and s2["objects"] == [] and s2["speech"].startswith("I did not find any objects.")


def test_close_ends_scan_without_summary(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        async def send(o):
            raise ConnectionError("gone")  # emit must swallow a closed socket
        s = svc.session(send)
        await s.start()
        await s.submit(1, jpeg(), meta(0))
        sid = s.scan_id
        await s.close()
        return sid, s.active

    sid, active = asyncio.run(go())
    assert not active
    assert svc.wm.db.execute("SELECT ended_at FROM scans WHERE id=?", (sid,)).fetchone()["ended_at"] is not None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    svc = make_service(tmp_path)
    monkeypatch.setattr(main, "_service", None)
    monkeypatch.setattr(main, "service_factory", lambda: svc)
    with TestClient(main.app) as c:
        c.svc = svc
        yield c


def collect(ws, until):
    got = []
    while True:
        m = ws.receive_json()
        got.append(m)
        if m.get("type") == until:
            return got


def test_websocket_scan_find_and_frame_route(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_bytes(frame_msg(1, 0, {}, jpeg()))  # plain frame: M0 echo path still works
        echo = ws.receive_json()
        assert echo["seq"] == 1 and echo["w"] == 480 and "type" not in echo

        ws.send_bytes(frame_msg(2, 2, meta(0), jpeg()))  # scan frame before scan_start
        assert ws.receive_json() == {"type": "frame_rejected", "seq": 2, "reason": "no scan running"}

        ws.send_text(json.dumps({"type": "scan_end"}))
        assert ws.receive_json()["type"] == "error"

        ws.send_text(json.dumps({"type": "scan_start", "name": "t"}))
        assert ws.receive_json()["type"] == "scan_started"
        for i, y in enumerate((0, 15, 30)):
            ws.send_bytes(frame_msg(10 + i, 2, meta(y), jpeg()))
        ws.send_text(json.dumps({"type": "scan_end"}))
        got = collect(ws, "scan_summary")
        summary = got[-1]
        assert summary["frames"]["stored"] == 3 and summary["objects"][0]["label"] == "mug"
        assert sum(m["type"] == "frame_done" for m in got) == 3

        ws.send_text(json.dumps({"type": "find", "query": "the thing I drink coffee from"}))
        r = ws.receive_json()
        assert r["type"] == "find_result" and r["found"] and r["concept"] == "mug" and r["replay"]["width"] == 480
        assert set(r["from_scan_start"]) == {"dyaw", "dpitch"}
        fid = r["replay"]["frame_id"]
    img = client.get(f"/world/frame/{fid}.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get("/world/frame/99999.jpg").status_code == 404


def test_websocket_scan_start_without_api_key(monkeypatch):
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(main, "_service", None)

    def no_key():
        raise RuntimeError("MODEL_API_KEY not set")

    monkeypatch.setattr(main, "service_factory", no_key)
    with TestClient(main.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "scan_start"}))
        e = ws.receive_json()
        assert e["type"] == "error" and e["where"] == "scan_start" and "MODEL_API_KEY" in e["message"]
        ws.send_bytes(frame_msg(1, 0, {}, jpeg()))  # the socket is still usable
        assert ws.receive_json()["seq"] == 1


def test_bus_sees_scan_events_world_updates_and_find(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        async def send(o):
            pass
        sub = svc.bus.subscribe()
        s = svc.session(send)
        await s.start()
        await s.submit(1, jpeg(), meta(0))
        await s.end()
        await svc.find("something to drink coffee from")
        types = []
        while not sub.queue.empty():
            types.append((await sub.get())["type"])
        return types

    types = asyncio.run(go())
    assert types[0] == "scan_started" and "world_update" in types and "frame_done" in types
    assert types.index("world_update") < types.index("frame_done")  # map updates before the frame is reported
    assert types[-2:] == ["scan_summary", "find_result"]
    ev = [e for e in svc.bus.history() if e["type"] == "world_update"][0]
    assert ev["objects"][0]["label"] == "mug" and "source" not in ev
