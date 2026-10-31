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

  Synthetic (no phone), two worlds from tests/synthetic_world.py. It checks the code, not the camera:
    - a room panorama seen from one point (no depth): same spot, start heading +90, two objects moved;
    - a desk rendered with depth, 40 to 60 cm from the phone, so a few centimetres of hand drift cause real parallax: the real near-field
      desk scans (an unmoved rescan and one with the mouse moved), then the protocol on that desk (20 cm sideways, start heading +90, two
      objects moved), over several seeds (`--near-seeds`) and a texture level (`--near-detail`, 0.2 by default; 0.1 is a plainer desk).

      python scripts/eval_alignment.py --synthetic

Reported: yaw-offset error, the false-move rate (objects called "moved" or "moved, low confidence" that were not moved, over all objects
compared), the true-move recall (really moved objects called "moved"; "moved, low confidence" is counted apart), false new and removed, and
the counts in "possibly moved" and "can't compare".
"""
from __future__ import annotations

import argparse
import json
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
        got = {k: [e["label"] for e in d.get(k, [])] for k in ("moved", "possibly_moved", "moved_low_confidence", "removed", "added")}
        compared = d["unchanged"] + len(d["moved"]) + len(d["possibly_moved"]) + len(d.get("moved_low_confidence", []))
        truth_off = s.get("yaw_offset_deg")
        rows.append({
            "name": s["name"], "scan": s["scan"], "quality": al["quality"], "inliers": al["inliers"], "pairs": al["pairs"], "spread_deg": al["spread_deg"],
            "method": al["method"], "offset_deg": al["offset_deg"], "truth_offset_deg": truth_off,
            "offset_error_deg": None if truth_off is None or al["quality"] == "failed" else round(abs(wrap180(al["offset_deg"] - truth_off)), 2),
            "moved": got["moved"], "possibly_moved": got["possibly_moved"], "moved_low_confidence": got["moved_low_confidence"], "removed": got["removed"], "added": got["added"],
            "unchanged": d["unchanged"], "cant_compare": len(d["cant_compare"]), "compared": compared, "truth_moved": truth,
            "false_moved": [l for l in got["moved"] + got["moved_low_confidence"] if l not in truth], "true_moved_found": [l for l in truth if l in got["moved"]],
            "true_moved_low_confidence": [l for l in truth if l in got["moved_low_confidence"]],
            "offset_sem_deg": al.get("offset_sem_deg"), "pitch_bias_deg": al.get("pitch_bias_deg"),
            "local": {e["label"]: e["local"] for k in ("moved", "possibly_moved", "moved_low_confidence") for e in d.get(k, []) if e.get("local")},
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

    pano = make_panorama(seed=seed)
    wm = WorldModel(frames_dir=Path(tempfile.mkdtemp(prefix="align-eval-")) / "frames")
    rid = wm.create_room("Desk", active=True)["id"]
    yaws = list(range(0, 360, 40))
    objs = [("mug", 80.0, 0.0), ("book", 140.0, 0.0), ("laptop", 200.0, 0.0), ("plant", 300.0, 0.0)]
    ref = add_scan(wm, rid, pano, yaws, ts=100.0, objects=objs)
    moved = [(l, y + 35.0, p) if l in ("mug", "book") else (l, y, p) for l, y, p in objs]
    scenarios = [
        {"name": "same spot", "scan": add_scan(wm, rid, pano, yaws, ts=1000.0, true_jitter=(9.0, 1.0), objects=objs), "yaw_offset_deg": 0.0, "moved": []},
        {"name": "start heading +90", "scan": add_scan(wm, rid, pano, yaws, meta_yaw_shift=90.0, true_jitter=(9.0, 1.0), ts=3000.0, objects=objs), "yaw_offset_deg": 90.0, "moved": []},
        {"name": "two objects moved", "scan": add_scan(wm, rid, pano, yaws, ts=4000.0, true_jitter=(5.0, 0.5), objects=moved), "yaw_offset_deg": 0.0, "moved": ["mug", "book"]},
    ]
    return evaluate(wm, ref, scenarios, viewpoint_cm)


# ------------------------------------------------------------------------------------------------ synthetic: the near field (a desk at 40 to 60 cm)

# Mirrors the real desk scans that aligned only weakly (docs/measurements.md, "near-field desk scans"): 3 to 4 frames of 540x960 at a 60 degree
# FOV, tilted down 35 to 45 degrees at a desk 40 to 60 cm away, the phone 10 to 12 cm from where the reference was taken plus a few centimetres
# of hand drift per frame, a laptop screen whose content changes every shot, a different exposure, a little blur, a per-scan tilt bias in the
# stored pitch, and a plain desk (`detail`), so that only a handful of frame pairs share enough texture. Rendered with depth, so the parallax is real.
NEAR_DETAIL, NEAR_BLUR = 0.2, 0.8
NEAR_MOUSE_TO = (50.0, 100.0)       # the mouse goes to the back right corner of the desk: from yaw 43, pitch -39 to 27, -16 (the real one: 39/-43 to 23/-16)
NEAR_PHONE_TO = (-50.0, 85.0)       # the phone goes to the back left: from yaw -41, pitch -38 to -30, -19
NEAR_NAMES = ["desk, rescan, nothing moved", "desk, rescan, mouse moved", "desk, 20 cm sideways", "desk, start heading +90", "desk, two objects moved"]


def near_field_world(seed: int = 1, detail: float | None = None, only: list[str] | None = None):
    """(world model, reference scan, scenarios, truth) for the near-field desk; `seed` varies the textures, the drift and the noise. `truth` holds
    every frame's true pose by frame id. The first two scenarios mirror the real desk scans; the other three are the protocol of
    docs/measurements.md on the same desk. `only` limits which scenarios are scanned (by name)."""
    from synthetic_world import DESK_OBJECTS, DeskScene, add_desk_scan

    detail = NEAR_DETAIL if detail is None else detail
    wm = WorldModel(frames_dir=Path(tempfile.mkdtemp(prefix="align-eval-near-")) / "frames")
    rid = wm.create_room("Desk", active=True)["id"]

    def scene(**moves):
        objs = dict(DESK_OBJECTS)
        for label, to in moves.items():
            objs[label] = (*to, *objs[label][2:])
        return DeskScene(seed=seed + 2, detail=detail, objects=objs)

    still, mouse, two = scene(), scene(mouse=NEAR_MOUSE_TO), scene(mouse=NEAR_MOUSE_TO, phone=NEAR_PHONE_TO)
    s, truth = seed * 10, {}
    common = dict(drift_cm=3.0, blur=NEAR_BLUR, truth=truth)
    ref = add_desk_scan(wm, rid, still, ts=100.0, seed=s + 1, pitch=-42.0, **common)
    specs = [
        (NEAR_NAMES[0], 0.0, [], lambda: add_desk_scan(wm, rid, still, yaws=(-28.0, -4.0, 22.0), ts=1000.0, seed=s + 2, pitch=-37.0, base=(9.0, -6.0, 4.0),
                                                       meta_pitch_shift=4.0, gain=1.12, **common)),
        (NEAR_NAMES[1], 0.0, ["mouse"], lambda: add_desk_scan(wm, rid, mouse, ts=2000.0, seed=s + 3, pitch=-34.0, base=(-7.0, 5.0, -6.0), meta_pitch_shift=-3.0,
                                                              gain=0.92, **common)),
        (NEAR_NAMES[2], 0.0, [], lambda: add_desk_scan(wm, rid, still, ts=3000.0, seed=s + 4, pitch=-40.0, base=(20.0, 0.0, 0.0), gain=1.05, **common)),
        (NEAR_NAMES[3], 90.0, [], lambda: add_desk_scan(wm, rid, still, ts=4000.0, seed=s + 5, pitch=-41.0, base=(4.0, 3.0, -2.0), meta_yaw_shift=90.0, gain=0.95, **common)),
        (NEAR_NAMES[4], 0.0, ["mouse", "phone"], lambda: add_desk_scan(wm, rid, two, ts=5000.0, seed=s + 6, pitch=-38.0, base=(5.0, -4.0, 3.0), gain=1.08, **common)),
    ]
    scenarios = [{"name": n, "yaw_offset_deg": off, "moved": mv, "scan": make()} for n, off, mv, make in specs if only is None or n in only]
    return wm, ref, scenarios, truth


def run_near_field(seed: int = 1, viewpoint_cm: float | None = None, detail: float | None = None, only: list[str] | None = None) -> dict:
    wm, ref, scenarios, _ = near_field_world(seed, detail, only)
    return evaluate(wm, ref, scenarios, viewpoint_cm)


def run_near_field_seeds(seeds: list[int], viewpoint_cm: float | None = None, detail: float | None = None, only: list[str] | None = None) -> dict:
    """The near-field scenarios over several seeds: how often each quality comes out, and the move counts summed."""
    runs = [run_near_field(sd, viewpoint_cm, detail, only) for sd in seeds]
    rows = [r for res in runs for r in res["scenarios"]]
    q = {k: sum(1 for r in rows if r["quality"] == k) for k in ("good", "weak", "failed")}
    errs = [r["offset_error_deg"] for r in rows if r["offset_error_deg"] is not None]
    truth = sum(len(r["truth_moved"]) for r in rows)
    return {"seeds": seeds, "detail": NEAR_DETAIL if detail is None else detail, "runs": runs, "quality_counts": q, "scenario_runs": len(rows),
            "yaw_offset_error_deg": {"mean": round(sum(errs) / len(errs), 2) if errs else None, "max": max(errs) if errs else None, "n": len(errs)},
            "false_moves": sum(len(r["false_moved"]) for r in rows), "objects_compared": sum(r["compared"] for r in rows),
            "true_moves": truth, "true_found": sum(len(r["true_moved_found"]) for r in rows),
            "true_found_low_confidence": sum(len(r.get("true_moved_low_confidence", [])) for r in rows),
            "true_moves_as_possibly": sum(len(r["true_moved_possibly"]) for r in rows),
            "false_removed": sum(len(r["removed"]) for r in rows), "false_added": sum(len(r["added"]) for r in rows),
            "cant_compare_total": sum(r["cant_compare"] for r in rows), "possibly_moved_total": sum(len(r["possibly_moved"]) for r in rows)}


def report_seeds(res: dict) -> str:
    q = res["quality_counts"]
    e = res["yaw_offset_error_deg"]
    out = ["| Seed | Scenario | Alignment | Matches | Pairs | Offset error | Moved | Moved (low confidence) | Possibly moved | New | Removed | Can't compare | Unchanged |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for sd, run in zip(res["seeds"], res["runs"]):
        for r in run["scenarios"]:
            err = "–" if r["offset_error_deg"] is None else f"{r['offset_error_deg']}°"
            out.append(f"| {sd} | {r['name']} | {r['quality']} | {r['inliers']} | {r['pairs']} | {err} | {', '.join(r['moved']) or '–'} | "
                       f"{', '.join(r.get('moved_low_confidence', [])) or '–'} | {', '.join(r['possibly_moved']) or '–'} | {', '.join(r['added']) or '–'} | "
                       f"{', '.join(r['removed']) or '–'} | {r['cant_compare']} | {r['unchanged']} |")
    out += ["",
            f"- Alignment over {res['scenario_runs']} scenario runs: good {q['good']}, weak {q['weak']}, failed {q['failed']}.",
            f"- Yaw-offset error: mean {e['mean']}°, max {e['max']}°.",
            f"- False moves: {res['false_moves']} of {res['objects_compared']} objects compared. False new: {res['false_added']}. False removed: {res['false_removed']}.",
            f"- True moves: {res['true_found']} of {res['true_moves']} called moved, {res['true_found_low_confidence']} more as moved (low confidence),"
            f" {res['true_moves_as_possibly']} only as possibly moved.",
            f"- Possibly moved: {res['possibly_moved_total']}. Can't compare: {res['cant_compare_total']}."]
    return "\n".join(out)


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
    ap.add_argument("--near-seeds", type=int, default=5, help="seeds for the near-field desk scenarios (with --synthetic)")
    ap.add_argument("--near-detail", type=float, default=None, help="texture of the near-field desk (default 0.2; 0.1 is plainer)")
    ap.add_argument("--viewpoint-cm", type=float, default=None, help="assumed viewpoint error (default 15, or VIEWPOINT_ERROR_CM)")
    ap.add_argument("--json", help="also write the full result here")
    a = ap.parse_args()
    if a.synthetic:
        res = run_synthetic(a.seed, a.viewpoint_cm)
        near = run_near_field_seeds(list(range(1, a.near_seeds + 1)), a.viewpoint_cm, a.near_detail)
        print("Room panorama (no depth):\n")
        print(report(res))
        print("\nNear-field desk (rendered with depth):\n")
        print(report_seeds(near))
        if a.json:
            Path(a.json).write_text(json.dumps({"panorama": res, "near_field": near}, indent=2), encoding="utf-8")
        return
    elif a.manifest:
        res = run_real(a.db, json.loads(Path(a.manifest).read_text(encoding="utf-8-sig")), a.viewpoint_cm)
    else:
        ap.error("give --synthetic or --manifest")
    print(report(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
