"""Replay saved Save-sample frames through the live scan WebSocket, as if the phone were panning.

Exercises the real server (queue, pipeline, World Model, find) without the phone. Frames are sent in
saved order with their stored orientation; events from the server are printed as they arrive.

    python scripts/replay_scan.py --only 130123-18,130123-19 --find "the thing I drink from"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
HEADER = struct.Struct(">BIdH")
FLAG_SCAN = 2


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    ap.add_argument("--images", type=Path, default=ROOT / "data" / "frames")
    ap.add_argument("--only", default="", help="comma separated substrings of file names to send")
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between frames")
    ap.add_argument("--find", action="append", default=[])
    args = ap.parse_args()

    keys = [k for k in args.only.split(",") if k]
    files = [p for p in sorted(args.images.glob("*.jpg")) if (not keys or any(k in p.name for k in keys)) and p.with_suffix(".json").exists()]
    side = {p: json.loads(p.with_suffix(".json").read_text(encoding="utf-8")) for p in files}
    files.sort(key=lambda p: side[p].get("saved_at", 0))
    if not files:
        raise SystemExit("no frames matched")

    async with websockets.connect(args.url, max_size=None) as ws:
        async def reader(stop_on: str):
            while True:
                ev = json.loads(await ws.recv())
                t = ev.get("type")
                brief = {k: v for k, v in ev.items() if k not in ("objects", "coverage") or t == "scan_summary"}
                print(f"<- {json.dumps(brief)[:400]}")
                if t == stop_on or t == "error":
                    return ev

        await ws.send(json.dumps({"type": "scan_start", "name": "replay"}))
        await reader("scan_started")
        for i, p in enumerate(files):
            meta = {k: side[p][k] for k in ("orientation", "camera") if k in side[p]}
            mb = json.dumps(meta).encode()
            await ws.send(HEADER.pack(FLAG_SCAN, i + 1, 0.0, len(mb)) + mb + p.read_bytes())
            print(f"-> frame {i + 1} {p.name} yaw {meta['orientation']['absolute']['yaw']:.0f}")
            await asyncio.sleep(args.interval)
        await ws.send(json.dumps({"type": "scan_end"}))
        summary = await reader("scan_summary")
        for q in args.find:
            await ws.send(json.dumps({"type": "find", "query": q}))
            await reader("find_result")
        print("\nobjects:", [(o["label"], o["yaw"], o["pitch"], o["n_obs"]) for o in summary.get("objects", [])])


if __name__ == "__main__":
    asyncio.run(main())
