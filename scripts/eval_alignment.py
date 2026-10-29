"""Evaluate scan alignment and the change classes against known truth.

Two modes, both working on a COPY of the database (the real one is never opened by this code: opening migrates it).

  Real scans (the protocol in docs/measurements.md): one reference scan of a desk and one scan per scenario, described in a manifest

      {"reference": 12, "scenarios": [
          {"name": "same spot",        "scan": 13, "yaw_offset_deg": 0,  "moved": []},
          {"name": "20 cm sideways",   "scan": 14, "yaw_offset_deg": 0,  "moved": []},
          {"name": "heading +90",      "scan": 15, "yaw_offset_deg": 90, "moved": []},
          {"name": "two moved",        "scan": 16, "yaw_offset_deg": 0,  "moved": ["mug", "charger"]}]}

      python scripts/eval_alignment.py --db data/world.sqlite --manifest manifest.json

  `yaw_offset_deg` is the known offset of the scan's reference frame from the reference scan's (B minus A, degrees): 0 for scans that used
  the compass, the rotation you applied otherwise; leave it out when unknown. `moved` lists the labels of objects you really moved.

  Synthetic (no phone): a rendered room (tests/synthetic_world.py) with the same four scenarios, the sideways one simulated by shifting each
  object by the parallax a 20 cm step causes at its estimated distance. It checks the code, not the camera.

      python scripts/eval_alignment.py --synthetic

Reported: yaw-offset error, the false-move rate (objects called "moved" that were not moved, over all objects compared in scenarios where
nothing moved), the true-move recall (really moved objects called "moved"), and the counts in "possibly moved" and "can't compare".
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

from app import align, changes  # noqa: E402
from app.world_model import WorldModel  # noqa: E402


def wrap180(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


def evaluate(wm: WorldModel, ref: int, scenarios: list[dict], viewpoint_cm: float | None = None) -> dict:
    rows = []
    for s in scenarios:
        al = align.align_scans(wm, ref, s["scan"])
        d = changes.compare_scans(wm, ref, s["scan"], align=al, viewpoint_cm=viewpoint_cm)
        truth = s.get("moved") or []
        got = {k: [e["label"] for e in d[k]] for k in ("moved", "possibly_moved", "removed", "added")}
        compared = d["unchanged"] + len(d["moved"]) + len(d["possibly_moved"])
        truth_off = s.get("yaw_offset_deg")
        rows.append({
            "name": s["name"], "scan": s["scan"], "quality": al["quality"], "inliers": al["inliers"], "pairs": al["pairs"], "spread_deg": al["spread_deg"],
            "method": al["method"], "offset_deg": al["offset_deg"], "truth_offset_deg": truth_off,
            "offset_error_deg": None if truth_off is None or al["quality"] == "failed" else round(abs(wrap180(al["offset_deg"] - truth_off)), 2),
            "moved": got["moved"], "possibly_moved": got["possibly_moved"], "removed": got["removed"], "added": got["added"],
            "unchanged": d["unchanged"], "cant_compare": len(d["cant_compare"]), "compared": compared, "truth_moved": truth,
            "false_moved": [l for l in got["moved"] if l not in truth], "true_moved_found": [l for l in truth if l in got["moved"]],
            "true_moved_possibly": [l for l in truth if l in got["possibly_moved"]],
        })
    errs = [r["offset_error_deg"] for r in rows if r["offset_error_deg"] is not None]
    still = [r for r in rows if r["compared"]]
    n_cmp = sum(r["compared"] for r in still)
    n_false = sum(len(r["false_moved"]) for r in still)
    n_truth = sum(len(r["truth_moved"]) for r in rows)
    return {
        "scenarios": rows,
        "yaw_offset_error_deg": {"mean": round(sum(errs) / len(errs), 2) if errs else None, "max": max(errs) if errs else None, "n": len(errs)},
        "false_move_rate": round(n_false / n_cmp, 3) if n_cmp else None, "false_moves": n_false, "objects_compared": n_cmp,
        "true_move_recall": round(sum(len(r["true_moved_found"]) for r in rows) / n_truth, 3) if n_truth else None,
        "true_moves": n_truth, "true_found": sum(len(r["true_moved_found"]) for r in rows), "true_moves_as_possibly": sum(len(r["true_moved_possibly"]) for r in rows),
        "possibly_moved_total": sum(len(r["possibly_moved"]) for r in rows), "cant_compare_total": sum(r["cant_compare"] for r in rows),
    }


def report(res: dict) -> str:
    out = ["| Scenario | Alignment | Matches | Pairs | Offset | Offset error | Moved | Possibly moved | Can't compare | Unchanged |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["scenarios"]:
        err = "–" if r["offset_error_deg"] is None else f"{r['offset_error_deg']}°"
        out.append(f"| {r['name']} | {r['quality']} | {r['inliers']} | {r['pairs']} | {r['offset_deg']}° | {err} | {', '.join(r['moved']) or '–'} | "
                   f"{', '.join(r['possibly_moved']) or '–'} | {r['cant_compare']} | {r['unchanged']} |")
    e = res["yaw_offset_error_deg"]
    out += ["",
            f"- Yaw-offset error: mean {e['mean']}°, max {e['max']}° over {e['n']} scenarios with a known offset.",
            f"- False-move rate: {res['false_moves']} of {res['objects_compared']} objects compared were called moved without being moved ({res['false_move_rate']}).",
            f"- True-move recall: {res['true_move_recall']} ({res['true_found']} of {res['true_moves']} really moved objects were called moved;"
            f" {res['true_moves_as_possibly']} more only as possibly moved).",
            f"- Possibly moved (all scenarios): {res['possibly_moved_total']}. Can't compare (all scenarios): {res['cant_compare_total']}."]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ synthetic

def run_synthetic(seed: int = 1, viewpoint_cm: float | None = None) -> dict:
    from synthetic_world import add_scan, make_panorama
    from app import scale

    pano = make_panorama(seed=seed)
    wm = WorldModel(frames_dir=Path(tempfile.mkdtemp(prefix="align-eval-")) / "frames")
    rid = wm.create_room("Desk", active=True)["id"]
    yaws = list(range(0, 360, 40))
    objs = [("mug", 80.0, 0.0), ("book", 140.0, 0.0), ("laptop", 200.0, 0.0), ("plant", 300.0, 0.0)]
    ref = add_scan(wm, rid, pano, yaws, ts=100.0, objects=objs)
    refs = {o["label"]: o for o in wm.list_objects(ref)}
    side = []                                                    # a 20 cm step sideways: each object appears to shift by atan(20 cm / its distance)
    for label, y, p in objs:
        size, src = scale.size_for(wm, label)
        est = scale.distance_estimate(wm, refs[label], size, src)
        side.append((label, y + math.degrees(math.atan2(20.0, est["distance_cm"])), p))
    moved = [(l, y + 35.0, p) if l in ("mug", "book") else (l, y, p) for l, y, p in objs]
    scenarios = [
        {"name": "same spot", "scan": add_scan(wm, rid, pano, yaws, ts=1000.0, true_jitter=(9.0, 1.0), objects=objs), "yaw_offset_deg": 0.0, "moved": []},
        {"name": "20 cm sideways (simulated parallax)", "scan": add_scan(wm, rid, pano, yaws, ts=2000.0, true_jitter=(7.0, 0.5), objects=side), "yaw_offset_deg": 0.0, "moved": []},
        {"name": "start heading +90", "scan": add_scan(wm, rid, pano, yaws, meta_yaw_shift=90.0, true_jitter=(9.0, 1.0), ts=3000.0, objects=objs), "yaw_offset_deg": 90.0, "moved": []},
        {"name": "two objects moved", "scan": add_scan(wm, rid, pano, yaws, ts=4000.0, true_jitter=(5.0, 0.5), objects=moved), "yaw_offset_deg": 0.0, "moved": ["mug", "book"]},
    ]
    return evaluate(wm, ref, scenarios, viewpoint_cm)


# ------------------------------------------------------------------------------------------------ real scans

def run_real(db: str, manifest: dict, viewpoint_cm: float | None = None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="align-eval-")) / "copy.sqlite"
    shutil.copy(db, tmp)
    wm = WorldModel(tmp)
    return evaluate(wm, manifest["reference"], manifest["scenarios"], viewpoint_cm)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/world.sqlite")
    ap.add_argument("--manifest")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--viewpoint-cm", type=float, default=None, help="assumed viewpoint error (default 15, or VIEWPOINT_ERROR_CM)")
    ap.add_argument("--json", help="also write the full result here")
    a = ap.parse_args()
    if a.synthetic:
        res = run_synthetic(a.seed, a.viewpoint_cm)
    elif a.manifest:
        res = run_real(a.db, json.loads(Path(a.manifest).read_text(encoding="utf-8")), a.viewpoint_cm)
    else:
        ap.error("give --synthetic or --manifest")
    print(report(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
