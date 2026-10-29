"""Reality API as an MCP server: another agent asks questions about the room Reality OS has scanned.

Mounted on the main server at /mcp (Streamable HTTP, see app.main), so agent calls show up live on the
dashboard and `guide_user_to` can reach the connected phone. Also runnable alone over stdio for clients
that launch their own subprocess:

    cd backend && python -m app.mcp_server

All tools except `guide_user_to` are read-only; the logic lives in app.reality_api.
"""
from __future__ import annotations

import inspect
import os
import time
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import Image, MCPServer

from app import reality_api as api
from app.scan_service import ScanService

INSTRUCTIONS = """Reality OS remembers the objects in the rooms ("spaces": Living Room, Desk, Wardrobe...) that a person scanned with their phone.
A room's memory is its latest saved scan. Rooms never share objects, directions or comparisons. Every tool takes a `room`
(an id or a name); leave it out to use the room the user chose as active. If no room is active and none is named, the answer is
status "no_active_room" with the list of rooms: ask the user which one, or pass `room`. Call list_rooms to see them. Positions are directions
(compass yaw and pitch) from where the phone stood, not coordinates: there is no distance, and nothing is known about
places the scan did not point at.
Answers say how far to trust them. status "found" gives the object, when it was last seen, how many frames saw it
(supporting_frames), its confidence, and uncertainty_deg: how many degrees its stored direction may be off, from sightings
that disagree plus the camera field-of-view error. Treat a direction as a cone of that half-angle, not a line.
status "not_seen" means it is not in memory, NOT that it is absent from the room: check the coverage in the reply before
telling anyone it is gone. Use what_changed to compare two scans; objects in directions only one scan covered come back
as "unverified" instead of added or removed."""


def build_server(get_service: Callable[[], ScanService], allow_frames: bool | None = None) -> MCPServer:
    """`get_service` is called per request so the server can start before the model API key is configured."""
    if allow_frames is None:
        allow_frames = os.getenv("REALITY_API_FRAMES", "1") != "0"
    server = MCPServer("reality-os", instructions=INSTRUCTIONS)

    async def traced(tool: str, args: dict, fn: Callable[[ScanService], Awaitable[dict] | dict]) -> dict:
        svc = get_service()
        t0 = time.perf_counter()
        try:
            result = fn(svc)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:  # noqa: BLE001 - show the failure on the dashboard, then let MCP report it
            svc.bus.publish({"type": "agent_call", "source": "agent", "tool": tool, "args": args, "status": "error",
                             "answer": f"{type(e).__name__}: {e}"[:300], "ms": round((time.perf_counter() - t0) * 1000)})
            raise
        svc.bus.publish({"type": "agent_call", "source": "agent", "tool": tool, "args": args, "status": result.get("status"),
                         "answer": result.get("answer") or result.get("message") or result.get("summary") or "",
                         "object_id": (result.get("object") or {}).get("id"), "sighting": result.get("sighting"),  # lets the dashboard show what the agent was told
                         "ms": round((time.perf_counter() - t0) * 1000)})
        return result

    @server.tool()
    async def list_rooms() -> dict[str, Any]:
        """The rooms Reality OS knows: id, name, which is active (the default for the other tools), how many objects each
        remembers, when it was last scanned and how much of it the scan covered."""
        return await traced("list_rooms", {}, lambda svc: api.room_list(svc))

    @server.tool()
    async def where_is(query: str, room: str | None = None) -> dict[str, Any]:
        """Where an object is in the scanned room. `query` can be a name ("keys") or a description of what it does
        ("the thing I charge my laptop with"); English or a Hindi/English mix. Returns the compass direction,
        the direction relative to where the scan started, the neighbouring objects, a photo reference, and how
        much to trust the position. status is found, not_seen or unresolved."""
        return await traced("where_is", {"query": query, "room": room}, lambda svc: api.where_is(svc, query, room))

    @server.tool()
    async def list_objects(label: str | None = None, scan_id: int | None = None, room: str | None = None) -> dict[str, Any]:
        """All remembered objects of a room's scan (default: its memory, the latest saved scan), each with its direction,
        confidence and how long ago it was seen. Pass `label` to keep only objects of that kind."""
        return await traced("list_objects", {"label": label, "scan_id": scan_id, "room": room}, lambda svc: api.list_objects(svc, label, scan_id, room))

    @server.tool()
    async def scan_summary(scan_id: int | None = None, room: str | None = None) -> dict[str, Any]:
        """What a room's scan found (default: its latest saved scan): counts per kind of object, how many degrees of the
        room it covered and where the gaps are."""
        return await traced("scan_summary", {"scan_id": scan_id, "room": room}, lambda svc: api.scan_summary(svc, scan_id, room))

    @server.tool()
    async def what_changed(scan_before: int | None = None, scan_after: int | None = None, room: str | None = None) -> dict[str, Any]:
        """Compare two saved scans of ONE room (default: its latest against its baseline, else the one before). Returns objects
        added, removed and moved (with the angular shift), plus `unverified` objects in directions that only one scan looked at.
        Scans of different rooms are refused (status wrong_room)."""
        return await traced("what_changed", {"scan_before": scan_before, "scan_after": scan_after, "room": room},
                            lambda svc: api.what_changed(svc, scan_before, scan_after, room))

    @server.tool()
    async def guide_user_to(query: str, room: str | None = None) -> dict[str, Any]:
        """Ask the connected phone to guide its user to an object in the ACTIVE room: the phone shows the answer and offers the
        direction arrow. Returns status sent, not_found, no_phone_connected or wrong_room (the named room is not the active
        one: guidance never crosses rooms). The only tool with a side effect."""
        async def run(svc: ScanService) -> dict:
            rm, err = api.resolve_room(svc, room)
            if err:
                return err
            active = svc.wm.active_room()
            if active is None or active["id"] != rm["id"]:
                return {"status": "wrong_room", "room": {"id": rm["id"], "name": rm["name"]},
                        "message": f"{rm['name']} is not the room the user is in (active: {active['name'] if active else 'none'}). Ask them to switch first."}
            result = await svc.find(query, source="agent", room_id=rm["id"])
            if not result["found"]:
                return {"status": "not_found", "answer": result["speech"]}
            phones = svc.bus.count("phone")
            if not phones:
                return {"status": "no_phone_connected", "answer": result["speech"]}
            svc.bus.publish({"type": "guide_request", "result": result, "source": "agent"})
            return {"status": "sent", "phones": phones, "answer": result["speech"]}
        return await traced("guide_user_to", {"query": query, "room": room}, run)

    if allow_frames:
        @server.tool()
        def get_frame(frame_id: int) -> Image:
            """The stored photo of a keyframe, as referenced by `sighting.frame_id` in other answers. Photos can show
            people and screens; set REALITY_API_FRAMES=0 on the server to turn this tool off."""
            svc = get_service()
            jpeg = api.get_frame(svc, frame_id)
            svc.bus.publish({"type": "agent_call", "source": "agent", "tool": "get_frame", "args": {"frame_id": frame_id},
                             "status": "ok" if jpeg else "not_found", "answer": "", "ms": 0})
            if jpeg is None:
                raise ValueError(f"no frame {frame_id}")
            return Image(data=jpeg, format="jpeg")

    return server


def main() -> None:
    from dotenv import load_dotenv

    from app.scan_service import build_default_service

    load_dotenv()
    svc = build_default_service()
    build_server(lambda: svc).run("stdio")


if __name__ == "__main__":
    main()
