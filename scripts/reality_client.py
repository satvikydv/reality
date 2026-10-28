"""Ask the Reality API (MCP) a question from the command line, like a second agent would.

    python scripts/reality_client.py tools
    python scripts/reality_client.py where_is "the thing I charge my laptop with"
    python scripts/reality_client.py what_changed
    python scripts/reality_client.py guide_user_to "keys"
    python scripts/reality_client.py scan_summary --url http://127.0.0.1:8000/mcp
"""
from __future__ import annotations

import argparse
import asyncio
import json

from mcp import Client


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tool", help="tools | where_is | list_objects | scan_summary | what_changed | guide_user_to | get_frame")
    ap.add_argument("arg", nargs="?", help="the query (where_is, guide_user_to), label (list_objects), scan id (scan_summary) or frame id (get_frame)")
    ap.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    args = ap.parse_args()
    async with Client(args.url) as c:
        if args.tool == "tools":
            for t in (await c.list_tools()).tools:
                print(f"{t.name}: {(t.description or '').splitlines()[0]}")
            return
        params = {"where_is": "query", "guide_user_to": "query", "list_objects": "label"}
        ints = {"scan_summary": "scan_id", "get_frame": "frame_id"}
        call = {}
        if args.arg is not None:
            if args.tool in params:
                call[params[args.tool]] = args.arg
            elif args.tool in ints:
                call[ints[args.tool]] = int(args.arg)
        r = await c.call_tool(args.tool, call)
        if r.is_error:
            raise SystemExit(f"error: {r.content[0].text if r.content else r}")
        if r.structured_content is not None:
            print(json.dumps(r.structured_content, indent=2))
        else:
            for part in r.content:
                print(f"[{part.type}] {getattr(part, 'mime_type', '')} {len(getattr(part, 'data', '') or '')} base64 chars")


if __name__ == "__main__":
    asyncio.run(main())
