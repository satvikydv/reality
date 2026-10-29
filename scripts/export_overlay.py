"""Export one stored scan's overlay objects (what /api/overlay serves the phone) to JSON, for scripts/measure_overlay.mjs.

Works on a COPY of the database, so the real one is never opened by the new code (opening migrates it).

    python scripts/export_overlay.py --db data/world.sqlite --scan 4 --out overlay_scan4.json
    python scripts/export_overlay.py --db data/world.sqlite --list
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import anchoring as A  # noqa: E402
from app.world_model import WorldModel  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/world.sqlite")
    ap.add_argument("--scan", type=int)
    ap.add_argument("--out", default="overlay.json")
    ap.add_argument("--list", action="store_true", help="list the scans and exit")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="overlay-export-")) / "copy.sqlite"
    shutil.copy(a.db, tmp)
    wm = WorldModel(tmp)
    if a.list or a.scan is None:
        for s in wm.list_scans():
            print(f"scan {s['id']}: {s['n_objects']} objects, {s['n_frames']} frames, room {s['room_name']}, {s['status']}")
        return
    objs = wm.overlay_objects(a.scan)
    newest = max((o["last_seen"] for o in objs), default=0.0)
    # age as if the scan were viewed right after it ended (the real age is days, which would only push every recency to its floor)
    out = [{**{k: v for k, v in o.items() if k != "last_seen"}, "age_s": round(newest - o["last_seen"], 1)} for o in objs]
    Path(a.out).write_text(json.dumps({"scan_id": a.scan, "hfov_deg": A.DEFAULT_HFOV_DEG, "objects": out}))
    print(f"scan {a.scan}: {len(out)} objects -> {a.out}")


if __name__ == "__main__":
    main()
