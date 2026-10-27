"""M0: SAM 3.1 keyframe latency and match counts on phone-style JPEGs.

For every image x max-side x concept, runs a concept-prompt request. Then re-prompts the
same image with the boxes the concept pass returned (box-prompt request, up to 16 objects)
to measure the box-prompt path. Reports p50/p95 total latency, time-to-first-event, match
counts and confidence.

    python scripts/sam_latency.py --images data/frames --max-sides 640,960,1280 --repeat 3
    python scripts/sam_latency.py --images data/frames --concepts "mug,laptop"   # override the defaults
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path

from common import (
    SAM_USD_PER_IMAGE,
    load_frames,
    log_cost,
    make_client,
    sam_call,
    summarize,
    total_logged_cost,
    write_results,
)
from meta_sam_parser import PromptBox, FramePrompt, PromptObject, build_object_prompt


DEFAULT_CONCEPTS = "keys,headphones,mug,notebook,remote control,water bottle,charger,scissors,laptop"


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, default=Path("data/frames"), help="image file or directory")
    ap.add_argument("--concepts", default=DEFAULT_CONCEPTS,
                    help="comma separated short noun phrases, one SAM request each (default: common desk objects)")
    ap.add_argument("--max-sides", default="960", help="comma separated long-side sizes to test")
    ap.add_argument("--quality", type=int, default=80, help="JPEG quality")
    ap.add_argument("--repeat", type=int, default=2, help="repeats per (image, size, concept)")
    ap.add_argument("--limit", type=int, default=10, help="max images")
    ap.add_argument("--concurrency", type=int, default=1, help="1 = clean latency; raise to test throughput")
    ap.add_argument("--no-box-pass", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=1.0, help="abort if estimate exceeds this")
    ap.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    return ap.parse_args()


async def main():
    a = parse_args()
    concepts = [c.strip() for c in a.concepts.split(",") if c.strip()]
    sizes = [int(s) for s in a.max_sides.split(",")]
    n_imgs = len(load_frames(a.images, None, a.quality, a.limit))
    n_calls = n_imgs * len(sizes) * len(concepts) * a.repeat * (1 if a.no_box_pass else 2)
    est = n_calls * SAM_USD_PER_IMAGE
    print(f"{n_imgs} images x {len(sizes)} sizes x {len(concepts)} concepts x {a.repeat} repeats "
          f"(+box pass) = {n_calls} requests, est ${est:.3f}. Logged so far: ${total_logged_cost():.3f}")
    if est > a.budget_usd and not a.yes:
        raise SystemExit(f"Estimate exceeds --budget-usd {a.budget_usd}. Reduce the run or pass --yes.")

    client = make_client()
    sem = asyncio.Semaphore(a.concurrency)
    rows = []

    async def run(kind, size, frame, concept, rep, prompt):
        async with sem:
            r = await sam_call(client, frame, prompt)
        rows.append({
            "kind": kind, "max_side": size, "image": frame.name, "w": frame.width, "h": frame.height,
            "jpeg_kb": round(len(frame.jpeg) / 1024, 1), "concept": concept, "rep": rep,
            "total_ms": r.total_ms, "first_event_ms": r.first_event_ms, "n": len(r.objects),
            "conf": [o.confidence for o in r.objects], "boxes": [o.box for o in r.objects],
            "outcome": r.outcome, "error": r.error, "diagnostics": r.n_diagnostics,
        })
        return r

    for size in sizes:
        frames = load_frames(a.images, size, a.quality, a.limit)
        print(f"\n== max side {size}px ({frames[0].width}x{frames[0].height} first image) ==")
        # warm-up: first request pays connection setup
        await run("warmup", size, frames[0], concepts[0], -1, concepts[0])
        for fr in frames:
            for concept in concepts:
                last = None
                for rep in range(a.repeat):
                    last = await run("concept", size, fr, concept, rep, concept)
                if a.no_box_pass or not last or not last.objects:
                    continue
                objs = [
                    PromptObject(i + 1, [FramePrompt(0, PromptBox(
                        max(0, int(o.box[0])), max(0, int(o.box[1])),
                        min(fr.width, int(o.box[2]) + 1), min(fr.height, int(o.box[3]) + 1)))])
                    for i, o in enumerate(last.objects[:16]) if o.box
                ]
                if not objs:
                    continue
                prompt = build_object_prompt(size=(fr.width, fr.height), objects=objs)
                for rep in range(a.repeat):
                    await run("box", size, fr, concept, rep, prompt)
        print(f"   done, {sum(1 for r in rows if r['max_side'] == size)} rows")

    real = [r for r in rows if r["kind"] != "warmup"]
    log_cost("sam_latency", len(rows) * SAM_USD_PER_IMAGE, f"{len(rows)} requests")

    print("\n### Latency (ms), warm-up excluded\n")
    print("| mode | max side | JPEG KB (mean) | n | p50 | p95 | first-event p50 | errors |")
    print("|---|---|---|---|---|---|---|---|")
    summary = []
    for kind in ("concept", "box"):
        for size in sizes:
            sel = [r for r in real if r["kind"] == kind and r["max_side"] == size]
            if not sel:
                continue
            ok = [r for r in sel if not r["error"]]
            s = summarize([r["total_ms"] for r in ok])
            f = summarize([r["first_event_ms"] for r in ok if r["first_event_ms"]])
            kb = sum(r["jpeg_kb"] for r in sel) / len(sel)
            errs = len(sel) - len(ok)
            summary.append({"kind": kind, "max_side": size, "latency": s, "first_event": f, "errors": errs})
            print(f"| {kind} | {size} | {kb:.0f} | {s.get('n', 0)} | {s.get('p50')} | {s.get('p95')} | {f.get('p50')} | {errs} |")

    print("\n### Matches per concept (concept pass, mean over images/repeats)\n")
    print("| concept | max side | mean matches | images with 0 matches | mean confidence |")
    print("|---|---|---|---|---|")
    for concept in concepts:
        for size in sizes:
            sel = [r for r in real if r["kind"] == "concept" and r["concept"] == concept and r["max_side"] == size and not r["error"]]
            if not sel:
                continue
            per_img = defaultdict(list)
            for r in sel:
                per_img[r["image"]].append(r["n"])
            zero = sum(1 for v in per_img.values() if max(v) == 0)
            confs = [c for r in sel for c in r["conf"] if c is not None]
            mc = f"{sum(confs) / len(confs):.2f}" if confs else "n/a"
            print(f"| {concept} | {size} | {sum(r['n'] for r in sel) / len(sel):.2f} | {zero}/{len(per_img)} | {mc} |")

    # run-to-run stability: same image/concept/size, count varies between repeats?
    unstable = 0
    groups = defaultdict(list)
    for r in real:
        if r["kind"] == "concept" and not r["error"]:
            groups[(r["image"], r["concept"], r["max_side"])].append(r["n"])
    for v in groups.values():
        if len(set(v)) > 1:
            unstable += 1
    print(f"\nRepeat stability: {unstable}/{len(groups)} (image, concept, size) groups changed match count between identical runs")

    errors = [r for r in rows if r["error"]]
    if errors:
        print(f"\n{len(errors)} errors; first: {errors[0]['error']}")
    out = write_results("sam_latency", {"args": vars(a) | {"images": str(a.images)}, "summary": summary, "rows": rows})
    print(f"\nraw results: {out}\nrun cost est: ${len(rows) * SAM_USD_PER_IMAGE:.3f}; total logged ${total_logged_cost():.3f}")


if __name__ == "__main__":
    asyncio.run(main())
