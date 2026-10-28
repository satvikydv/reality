import asyncio

import pytest
from mcp import Client

from app.mcp_server import build_server
from test_reality_api import scan
from test_scan_service import make_service


def run(coro):
    return asyncio.run(coro)


async def call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    assert not r.is_error, r.content
    return r.structured_content


def test_tools_are_listed_with_descriptions(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        async with Client(build_server(lambda: svc)) as c:
            return {t.name: t for t in (await c.list_tools()).tools}

    tools = run(go())
    assert set(tools) == {"where_is", "list_objects", "scan_summary", "what_changed", "guide_user_to", "get_frame"}
    assert "plain-language" not in tools["where_is"].description and "compass direction" in tools["where_is"].description
    assert tools["where_is"].input_schema["required"] == ["query"]


def test_agent_asks_and_dashboard_sees_the_calls(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        await scan(svc, [100, 120])
        async with Client(build_server(lambda: svc)) as c:
            found = await call(c, "where_is", {"query": "the thing I drink coffee from"})
            summ = await call(c, "scan_summary")
            objs = await call(c, "list_objects", {"label": "mug"})
            img = await c.call_tool("get_frame", {"frame_id": found["sighting"]["frame_id"]})
            missing = await c.call_tool("get_frame", {"frame_id": 12345})
            return found, summ, objs, img, missing

    found, summ, objs, img, missing = run(go())
    assert found["status"] == "found" and found["object"]["label"] == "mug"
    assert summ["object_count"] == objs["count"] and summ["status"] == "ok"
    assert not img.is_error and img.content[0].type == "image" and img.content[0].mime_type == "image/jpeg"
    assert missing.is_error
    calls = [e for e in svc.bus.history() if e["type"] == "agent_call"]
    assert [e["tool"] for e in calls] == ["where_is", "scan_summary", "list_objects", "get_frame", "get_frame"]
    assert calls[0]["args"] == {"query": "the thing I drink coffee from"} and calls[0]["status"] == "found" and calls[0]["answer"].startswith("The mug")
    assert all(e["source"] == "agent" and "ms" in e for e in calls)
    assert calls[0]["sighting"]["frame_id"] == found["sighting"]["frame_id"] and calls[0]["object_id"] == found["object"]["id"]  # the dashboard shows what the agent was told


def test_what_changed_through_mcp(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        await scan(svc, [0])
        await scan(svc, [90])
        async with Client(build_server(lambda: svc)) as c:
            return await call(c, "what_changed")

    r = run(go())
    assert r["status"] == "ok" and r["moved"][0]["label"] == "mug"


def test_guide_user_to_reaches_a_connected_phone_only(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        await scan(svc, [100])
        async with Client(build_server(lambda: svc)) as c:
            none = await call(c, "guide_user_to", {"query": "my mug"})
            phone = svc.bus.subscribe("phone")
            sent = await call(c, "guide_user_to", {"query": "my mug"})
            ev = [await phone.get() for _ in range(2)]
            return none, sent, ev

    none, sent, ev = run(go())
    assert none["status"] == "no_phone_connected" and sent["status"] == "sent" and sent["phones"] == 1
    assert [e["type"] for e in ev] == ["find_result", "guide_request"] and ev[1]["result"]["found"] and ev[1]["source"] == "agent"


def test_frames_tool_can_be_switched_off(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        async with Client(build_server(lambda: svc, allow_frames=False)) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert "get_frame" not in run(go())


def test_failures_are_reported_and_traced(tmp_path):
    svc = make_service(tmp_path)

    def broken():
        raise RuntimeError("MODEL_API_KEY not set")

    async def go():
        async with Client(build_server(broken)) as c:
            return await c.call_tool("where_is", {"query": "keys"})

    assert run(go()).is_error
