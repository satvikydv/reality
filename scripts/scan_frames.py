"""Run the scan pipeline (PRD F10) on saved phone frames and print what the World Model learned.

Each JPEG needs its Save-sample sidecar (same stem, .json) for the camera orientation. Frames are
processed in the order they were saved. Spark names the objects, SAM boxes them, the World Model
anchors them; the script then prints every object with its anchor and spoken location.

    python scripts/scan_frames.py --images data/frames --only 120049,120050,120052,120104
    python scripts/scan_frames.py --images data/frames --ask "the thing I drink water from"
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from common import DATA_DIR, log_cost, make_client, summarize, total_logged_cost, write_results
from app.reasoner import make_reasoner
from app.resolver import semantic_find
from app.sam_client import make_segmenter
from app.scan import ScanPipeline, pose_from_meta
from app.world_model import WorldModel


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, default=DATA_DIR / "frames")
    ap.add_argument("--only", default="", help="comma separated substrings; keep frames whose file name contains one")
    ap.add_argument("--db", default=str(DATA_DIR / "world.sqlite"), help="World Model database (':memory:' to discard)")
    ap.add_argument("--source", default="absolute", choices=["absolute", "relative"])
    ap.add_argument("--max-side", type=int, default=960)
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--ask", action="append", default=[], help="semantic find query to run after the scan (repeatable)")
    ap.add_argument("--name", default=None, help="scan name")
    args = ap.parse_args()

    keys = [k for k in args.only.split(",") if k]
    files = [p for p in sorted(args.images.glob("*.jpg")) if (not keys or any(k in p.name for k in keys)) and p.with_suffix(".json").exists()]
    metas = {p: json.loads(p.with_suffix(".json").read_text(encoding="utf-8")) for p in files}
    files.sort(key=lambda p: metas[p].get("saved_at", 0))
    if not files:
        raise SystemExit(f"No JPEG+JSON pairs found in {args.images}")

    frames_dir = DATA_DIR / "world_frames"
    wm = WorldModel(args.db if args.db == ":memory:" else Path(args.db), frames_dir=frames_dir)
    scan = wm.start_scan(name=args.name, source=args.source)
    pipe = ScanPipeline(wm, make_reasoner(), make_segmenter(make_client()), max_side=args.max_side, min_conf=args.min_conf,
                        concurrency=args.concurrency)

    reports, cost = [], 0.0
    for p in files:
        pose = pose_from_meta(metas[p], args.source)
        if pose is None:
            print(f"{p.name}: no orientation in sidecar, skipped")
            continue
        rep = await pipe.process(scan, p.read_bytes(), metas[p].get("saved_at", 0.0), pose)
        reports.append(rep)
        cost += rep.usd
        print(f"{p.name[-18:]} yaw {pose['yaw']:5.1f} pitch {pose['pitch']:5.1f}  names {rep.names}\n"
              f"    kept {[(d['label'], round(d['conf'], 2)) for d in rep.detections]}  low-conf dropped {rep.dropped_low_conf}"
              f"  spark {rep.spark_ms:.0f} ms  sam stage {rep.sam_ms:.0f} ms ({rep.sam_calls} calls)  ${rep.usd:.4f}"
              + ("" if rep.stored else f"\n    NOT STORED: {rep.errors}"))
    wm.end_scan(scan)
    log_cost("scan_frames", cost, f"{len(reports)} frames")

    print(f"\n### Scan {scan}: {len(wm.list_objects(scan))} objects from {sum(r.stored for r in reports)}/{len(reports)} stored frames\n")
    print("| object | yaw | pitch | seen in frames | anchor spread deg | best conf |")
    print("|---|---|---|---|---|---|")
    for o in wm.list_objects(scan):
        print(f"| {o['label']} | {o['yaw']:.1f} | {o['pitch']:.1f} | {o['n_obs']} | {o['spread_deg']:.1f} | {o['conf_best']:.2f} |")
    print()
    for o in wm.list_objects(scan):
        w = wm.where_is(o["label"], scan_id=scan)
        print(f"- {w['speech']}")
    spark = summarize([r.spark_ms for r in reports])
    sam = summarize([r.sam_ms for r in reports])
    print(f"\nper frame: spark p50 {spark.get('p50')} ms, sam stage p50 {sam.get('p50')} ms; names/frame mean "
          f"{sum(len(r.names) for r in reports) / max(1, len(reports)):.1f}; run cost ${cost:.4f} (logged total ${total_logged_cost():.3f})")

    asks = []
    for q in args.ask:
        r = await semantic_find(make_reasoner(), wm, q, scan)
        asks.append({"query": q, **{k: r[k] for k in ("resolution", "needs_live_look", "skipped_llm")}, "speech": (r["found"] or {}).get("speech")})
        print(f"\nask {q!r} -> {r['resolution']['concept']!r}: {(r['found'] or {}).get('speech') or 'not in memory (needs live look)'}")
    out = write_results("scan_frames", {"scan_id": scan, "frames": [r.__dict__ | {"usd": r.usd} for r in reports], "asks": asks})
    print(f"\nraw results: {out}")


if __name__ == "__main__":
    asyncio.run(main())
