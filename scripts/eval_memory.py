"""Score the spatial memory on scans whose contents are known, and write the table to docs/measurements.md.

Duplicate rate, identity switches and find success need ground truth, which a real scan does not have, so the main
evaluation uses synthetic rooms (backend/app/simworld.py): known objects, a camera sweeping around them, detections with
jitter, misses, partial views at the image border, per-object colour, and a field of view that may differ from the
one the memory assumes. What is measured is the memory (identity, merging, anchoring, find), not SAM.

Metrics, per scene (the table shows the mean over scenes):
  duplicates    memory objects beyond one per real object, as a share of the real objects seen (0 = none)
  wrong merges  memory objects made of detections of more than one real object, as a share of memory objects
  missed        real objects that were seen but ended up in no memory object
  id switches   times a real object's memory id changed between two consecutive sightings (frames in arrival order)
  find success  querying a label returns an object within 12 degrees of a real object with that label
  recall        real objects that have a memory object of their label within 12 degrees
  order stable  random frame arrival orders that give exactly the same final objects as capture order

Real scans: with --real the stored scans of data/world.sqlite are replayed through the current memory on a COPY of the
database (appearance histograms recomputed from the stored frames); there is no ground truth, so only object counts
and decisions are reported.

  python scripts/eval_memory.py                 # run and write the section in docs/measurements.md
  python scripts/eval_memory.py --no-write      # print only
  python scripts/eval_memory.py --scenes 3 --objects 12 --real
"""
import argparse
import datetime
import json
import random
import shutil
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import anchoring as A  # noqa: E402
from app import memory, simworld  # noqa: E402
from app.world_model import WorldModel  # noqa: E402

TOL_DEG = 12.0
START, END = "<!-- eval_memory:start -->", "<!-- eval_memory:end -->"
# scene: how the room is built; sweep: how the camera sees it
CONDITIONS = {
    "clean (FOV as assumed)": dict(scene={}, sweep=dict(true_hfov=60.0, miss_p=0.08, min_visible=0.3, noise_px=5.0)),
    "FOV 10% wider than assumed": dict(scene={}, sweep=dict(true_hfov=66.0, miss_p=0.08, min_visible=0.3, noise_px=5.0)),
    "hard (FOV +10%, 15% misses, more clipped views, noisier boxes)": dict(scene={}, sweep=dict(true_hfov=66.0, miss_p=0.15, min_visible=0.1, noise_px=8.0)),
    "twins (4 pairs of same-label objects 3 degrees apart, different colours)": dict(scene=dict(twins=4), sweep=dict(true_hfov=60.0, miss_p=0.08, min_visible=0.3, noise_px=5.0)),
    "sparse sweep (30 degree steps: each object seen once or twice, often clipped)": dict(scene={}, sweep=dict(true_hfov=60.0, miss_p=0.08, min_visible=0.1, noise_px=5.0, step_deg=30.0)),
    "very sparse sweep (60 degree steps: most objects seen once)": dict(scene={}, sweep=dict(true_hfov=60.0, miss_p=0.05, min_visible=0.05, noise_px=5.0, step_deg=60.0)),
}
SYSTEMS = {
    "full": memory.Config(),
    "no appearance check": memory.Config(use_appearance=False),
    "no fragment promotion": memory.Config(promote_fragments=False),
    "arrival order, no appearance, no promotion (old behaviour)": memory.Config(order="arrival", use_appearance=False, promote_fragments=False),
}


def run_once(truths, frames, cfg, order):
    wm = WorldModel(memory_cfg=cfg, default_hfov_deg=60.0)  # the memory always assumes 60; the camera may differ
    s = wm.start_scan()
    seen_ids = defaultdict(list)       # truth id -> memory object id at each sighting, in arrival order
    frame_of = {}
    for i in order:
        f = frames[i]
        fid = wm.add_frame(s, f["ts"], f["yaw"], f["pitch"], simworld.W, simworld.H, roll=0.0, screen_angle=0.0, hfov_deg=60.0)
        frame_of[fid] = i
        for t, e in zip(f["truth"], wm.observe(fid, [dict(d) for d in f["dets"]])):
            if e["object_id"] is not None:
                seen_ids[t].append(e["object_id"])
    return wm, s, frame_of, seen_ids


def score(truths, frames, wm, s, frame_of, seen_ids):
    by_id = {t.id: t for t in truths}
    members = defaultdict(list)
    seen_truth = set()
    for fid, idx, oid in wm.db.execute("SELECT frame_id, idx, object_id FROM detections WHERE scan_id=?", (s,)):
        t = frames[frame_of[fid]]["truth"][idx]
        seen_truth.add(t)
        if oid is not None:
            members[oid].append(t)
    objs = {o["id"]: o for o in wm.list_objects(s)}
    owner = {oid: Counter(ts).most_common(1)[0][0] for oid, ts in members.items()}
    per_truth = Counter(owner.values())
    dup = sum(max(0, n - 1) for n in per_truth.values())
    impure = sum(1 for ts in members.values() if len(set(ts)) > 1)
    missed = sum(1 for t in seen_truth if t not in per_truth)
    switches = sum(sum(1 for a, b in zip(ids, ids[1:]) if a != b) for ids in seen_ids.values())
    errs = [A.angular_distance(objs[o]["yaw"], objs[o]["pitch"], by_id[t].yaw, by_id[t].pitch) for o, t in owner.items()]
    near = lambda o, t: A.angular_distance(o["yaw"], o["pitch"], t.yaw, t.pitch) <= TOL_DEG  # noqa: E731
    labels = sorted({by_id[t].label for t in seen_truth})
    hits = 0
    for lab in labels:
        top = wm.find(lab, scan_id=s)[:1]
        hits += bool(top) and any(near(top[0], t) for t in truths if t.label == lab)
    recall = sum(1 for t in seen_truth if any(o["label"] == by_id[t].label and near(o, by_id[t]) for o in objs.values()))
    n = max(1, len(seen_truth))
    return {"objects": len(objs), "seen": len(seen_truth), "dup": dup / n, "wrong": impure / max(1, len(members)), "missed": missed / n,
            "switches": switches, "find": hits / max(1, len(labels)), "recall": recall / n, "err": statistics.median(errs) if errs else float("nan")}


def evaluate(n_scenes, n_objects, n_orders):
    rows, stable = [], []
    for cname, cond in CONDITIONS.items():
        for sname, cfg in SYSTEMS.items():
            res, same = [], []
            for seed in range(n_scenes):
                truths = simworld.make_scene(seed=100 + seed, n=n_objects, **cond["scene"])
                frames = simworld.sweep(truths, seed=100 + seed, **cond["sweep"])
                n = len(frames)
                orders = []
                for k in range(n_orders):
                    o = list(range(n))
                    random.Random(seed * 1000 + k).shuffle(o)
                    orders.append(o)
                base = simworld.fingerprint(*run_once(truths, frames, cfg, list(range(n)))[:2])
                same.append(sum(simworld.fingerprint(*run_once(truths, frames, cfg, o)[:2]) == base for o in orders) / n_orders)
                # the scored run delivers the frames in a random order: what a busy server does
                res.append(score(truths, frames, *run_once(truths, frames, cfg, orders[0])))
            mean = {k: statistics.mean(r[k] for r in res) for k in res[0]}
            rows.append((cname, sname, mean, statistics.mean(same)))
    return rows


def fmt_table(rows):
    out = ["| Condition | System | Objects (seen) | Duplicates | Wrong merges | Missed | ID switches | Find success | Recall | Anchor error (median) | Order stable |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    last = None
    for cname, sname, m, st in rows:
        out.append(f"| {cname if cname != last else ''} | {sname} | {m['objects']:.1f} ({m['seen']:.1f}) | {m['dup']:.0%} | {m['wrong']:.0%} | {m['missed']:.0%} | "
                   f"{m['switches']:.1f} | {m['find']:.0%} | {m['recall']:.0%} | {m['err']:.1f}° | {st:.0%} |")
        last = cname
    return "\n".join(out)


def real_scans():
    """Replay stored scans through the current memory on a copy of the database. Returns markdown, or None without data."""
    import sqlite3

    import cv2

    src = ROOT / "data" / "world.sqlite"
    if not src.exists():
        return None
    tmp = Path(tempfile.mkdtemp()) / "copy.sqlite"
    shutil.copy(src, tmp)
    wm = WorldModel(tmp)
    rows, sims = [], []
    cache = {}
    for sc in wm.list_scans():
        if sc["n_frames"] == 0:
            continue
        sid = sc["id"]
        wm._ensure_detections(sid)
        for r in wm.db.execute("SELECT d.id, d.box, d.poly, f.path FROM detections d JOIN frames f ON f.id=d.frame_id WHERE d.scan_id=?", (sid,)).fetchall():
            if not r["path"] or not Path(r["path"]).exists():
                continue
            img = cache.get(r["path"])
            if img is None:
                img = cache[r["path"]] = cv2.imread(r["path"])
            h = memory.mask_histogram(img, json.loads(r["poly"]) if r["poly"] else None, json.loads(r["box"])) if img is not None else None
            if h:
                wm.db.execute("UPDATE detections SET hist=? WHERE id=?", (json.dumps(h), r["id"]))
        wm.db.commit()
        stored = Counter(o["label"] for o in wm.list_objects(sid))
        wm.rebuild(sid)
        now = Counter(o["label"] for o in wm.list_objects(sid))
        acts = Counter(d["action"] for d in wm.decisions(sid))
        top = ", ".join(f"{k} {stored[k]}→{now[k]}" for k, _ in stored.most_common(4))
        same, diff = [], []
        dets = wm.db.execute("SELECT frame_id, label, hist, object_id FROM detections WHERE scan_id=? AND hist IS NOT NULL AND object_id IS NOT NULL", (sid,)).fetchall()
        for i, a in enumerate(dets):
            for b in dets[i + 1:]:
                if a["label"] == b["label"] and a["frame_id"] != b["frame_id"]:
                    v = memory.hist_similarity(json.loads(a["hist"]), json.loads(b["hist"]))
                    (same if a["object_id"] == b["object_id"] else diff).append(v)
        q = lambda v, f: f"{sorted(v)[int(f * (len(v) - 1))]:.2f}" if v else "–"  # noqa: E731
        sims.append(f"| {sid} | {len(same)} | {q(same, 0)} / {q(same, 0.5)} | {len(diff)} | {q(diff, 0.1)} / {q(diff, 0.5)} | "
                    f"{sum(v < memory.Config().appearance_min for v in same)} / {sum(v < memory.Config().appearance_min for v in diff)} |")
        rows.append(f"| {sid} | {sc['n_frames']} | {sum(stored.values())} | {sum(now.values())} | {acts.get('veto', 0) + acts.get('veto_merge', 0)} | "
                    f"{acts.get('promote', 0)} | {acts.get('hold_fragment', 0)} | {top} |")
    wm.close()
    if not rows:
        return None
    return ("| Scan | Frames | Objects stored | Objects now | Appearance vetoes | Fragments promoted | Fragments held | Largest labels (stored→now) |\n"
            "|---|---|---|---|---|---|---|---|\n" + "\n".join(rows)
            + "\n\nColour similarity (histogram intersection, 1 = identical) of same-label detections from different frames, split by whether the memory put them in one object. "
              f"The veto threshold is {memory.Config().appearance_min}: it refuses a geometric match below it, so a threshold that is too high creates duplicates.\n\n"
              "| Scan | Same-object pairs | min / median | Different-object pairs | p10 / median | Below threshold (same / different) |\n|---|---|---|---|---|---|\n" + "\n".join(sims))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenes", type=int, default=5)
    ap.add_argument("--objects", type=int, default=16)
    ap.add_argument("--orders", type=int, default=6, help="random arrival orders per scene for the order-stability check")
    ap.add_argument("--real", action="store_true", help="also replay the stored real scans (on a copy of the database)")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--doc", default=str(ROOT / "docs" / "measurements.md"))
    a = ap.parse_args()

    rows = evaluate(a.scenes, a.objects, a.orders)
    table = fmt_table(rows)
    real = real_scans() if a.real else None
    section = [START, "## Spatial memory evaluation", "",
               f"Run {datetime.date.today().isoformat()} with `python scripts/eval_memory.py{' --real' if a.real else ''}`: {a.scenes} synthetic rooms of {a.objects} known objects per condition, "
               f"each swept in 15 degree steps; the memory assumes a 60 degree FOV; frames reach the memory in a random order (the scored run) and in {a.orders} further random orders "
               f"(order stable). Match tolerance {TOL_DEG:.0f} degrees. Detections are simulated (jitter, misses, clipping, colour noise), so this measures the memory, not SAM.", "",
               table, ""]
    if real:
        section += ["### Real scans replayed on a copy of the database", "",
                    "No ground truth exists for these, so only counts and decisions are reported. Appearance histograms were recomputed from the stored frames. "
                    "Stored detections are replayed as they were saved: the scan pipeline's same-frame box merge (`scan.merge_parts`) happened before they were stored, "
                    "or, for scans made before it existed, did not happen, and is not repeated here.", "", real, ""]
    section.append(END)
    block = "\n".join(section)
    print(block)
    if not a.no_write:
        doc = Path(a.doc)
        text = doc.read_text(encoding="utf-8") if doc.exists() else ""
        if START in text and END in text:
            text = text[:text.index(START)] + block + text[text.index(END) + len(END):]
        else:
            text = text.rstrip() + "\n\n" + block + "\n"
        doc.write_text(text, encoding="utf-8")
        print(f"\nwrote {doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
