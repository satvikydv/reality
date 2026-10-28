"""The real server over real sockets: phone WebSocket, dashboard WebSocket and an MCP client over HTTP, all against one app."""
import asyncio
import json
import threading
import time

import pytest
import uvicorn
import websockets
from mcp import Client

from app import main
from test_scan_service import frame_msg, jpeg, make_service, meta


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    svc = make_service(tmp_path)
    monkeypatch.setattr(main, "_service", None)
    monkeypatch.setattr(main, "service_factory", lambda: svc)
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    port = server.servers[0].sockets[0].getsockname()[1]
    yield svc, port
    server.should_exit = True
    thread.join(timeout=10)


async def until(ws, kind, limit=30):
    for _ in range(limit):
        m = json.loads(await asyncio.wait_for(ws.recv(), 20))
        if m.get("type") == kind:
            return m
    raise AssertionError(f"no {kind} event")


def test_agent_dashboard_and_phone_share_one_memory(live):
    svc, port = live

    async def go():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as phone:
            await phone.send(json.dumps({"type": "scan_start"}))
            await until(phone, "scan_started")
            for i, yaw in enumerate((100, 120)):
                await phone.send(frame_msg(i + 1, 2, meta(yaw), jpeg()))
            await phone.send(json.dumps({"type": "scan_end"}))
            summary = await until(phone, "scan_summary")

            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/events") as dash:
                replayed = await until(dash, "scan_summary")  # a dashboard opened later still sees the scan
                assert replayed["scan_id"] == summary["scan_id"]

                async with Client(f"http://127.0.0.1:{port}/mcp") as agent:
                    found = (await agent.call_tool("where_is", {"query": "the thing I drink coffee from"})).structured_content
                    assert found["status"] == "found" and found["object"]["label"] == "mug"
                    call = await until(dash, "agent_call")
                    assert call["tool"] == "where_is" and call["status"] == "found" and call["source"] == "agent"

                    sent = (await agent.call_tool("guide_user_to", {"query": "my mug"})).structured_content
                    assert sent["status"] == "sent"
                    request = await until(phone, "guide_request")
                    assert request["result"]["found"] and request["result"]["concept"] == "mug"
                    image = await agent.call_tool("get_frame", {"frame_id": found["sighting"]["frame_id"]})
                    assert image.content[0].mime_type == "image/jpeg"

    asyncio.run(go())
    assert svc.bus.count("phone") == 0 and svc.bus.count("dashboard") == 0  # sockets closed: unsubscribed


def test_mcp_rejects_a_foreign_host_header(live):
    """DNS-rebinding guard: a web page that resolved its own name to 127.0.0.1 must not reach the tools."""
    import httpx

    _, port = live
    r = httpx.post(f"http://127.0.0.1:{port}/mcp", headers={"Host": "evil.example:80", "Accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code in (400, 403, 421)
