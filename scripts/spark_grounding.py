"""M0: how accurate are Muse Spark's object boxes, using SAM concept boxes as reference?

For each image and concept: SAM concept prompt gives reference boxes; Spark is asked (structured
output) for boxes of the same concept. Because Spark's native coordinate convention is not
documented [VERIFY], the script scores the answer under three interpretations (pixels, 0-1000
normalized, 0-1 normalized) and reports which fits best.

    python scripts/spark_grounding.py --images data/frames --concepts "red brick,black wheel"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

from common import (
    SAM_USD_PER_IMAGE,
    SPARK_MODEL,
    SPARK_USD_PER_M_IN,
    SPARK_USD_PER_M_OUT,
    box_iou,
    load_frames,
    log_cost,
    make_client,
    sam_call,
    summarize,
    total_logged_cost,
    write_results,
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                },
                "required": ["label", "box"],
            },
        }
    },
    "required": ["objects"],
}

CONVENTIONS = {
    "pixels": lambda b, w, h: (b[0], b[1], b[2], b[3]),
    "norm1000": lambda b, w, h: (b[0] / 1000 * w, b[1] / 1000 * h, b[2] / 1000 * w, b[3] / 1000 * h),
    "norm1": lambda b, w, h: (b[0] * w, b[1] * h, b[2] * w, b[3] * h),
}


def match_score(pred_boxes, ref_boxes):
    """Greedy one-to-one matching by IoU. Returns list of matched IoUs (unmatched refs count as 0)."""
    ious = []
    used = set()
    for r in ref_boxes:
        best, bi = 0.0, None
        for i, p in enumerate(pred_boxes):
            if i in used:
                continue
            v = box_iou(p, r)
            if v > best:
                best, bi = v, i
        if bi is not None:
            used.add(bi)
        ious.append(best)
    return ious


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, default=Path("data/frames"))
    ap.add_argument("--concepts", required=True)
    ap.add_argument("--max-side", type=int, default=960)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--effort", default="low", choices=["minimal", "low", "medium", "high"])
    args = ap.parse_args()

    concepts = [c.strip() for c in args.concepts.split(",") if c.strip()]
    frames = load_frames(args.images, args.max_side, 80, args.limit)
    client = make_client()
    rows = []
    spark_in = spark_out = 0
    sam_n = 0

    for fr in frames:
        for concept in concepts:
            sam = await sam_call(client, fr, concept)
            sam_n += 1
            ref = [o.box for o in sam.objects if o.box]
            prompt = (
                f"The image is {fr.width}x{fr.height} pixels. Find every instance of: {concept}. "
                f"Return a tight bounding box [x1, y1, x2, y2] in pixel coordinates of this image "
                f"for each instance (x right, y down, origin top-left). Return an empty list if none are visible."
            )
            t0 = time.perf_counter()
            try:
                resp = await client.responses.create(
                    model=SPARK_MODEL,
                    input=[{"role": "user", "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": fr.data_url},
                    ]}],
                    text={"format": {"type": "json_schema", "name": "boxes", "schema": SCHEMA, "strict": True}},
                    reasoning={"effort": args.effort},
                    max_output_tokens=8000,
                )
                ms = (time.perf_counter() - t0) * 1000
                data = json.loads(resp.output_text)
                u = getattr(resp, "usage", None)
                if u:
                    spark_in += getattr(u, "input_tokens", 0) or 0
                    spark_out += getattr(u, "output_tokens", 0) or 0
                preds = [o["box"] for o in data["objects"]]
                err = None
            except Exception as e:  # noqa: BLE001
                ms = (time.perf_counter() - t0) * 1000
                preds, err = [], f"{type(e).__name__}: {e}"
            row = {"image": fr.name, "concept": concept, "w": fr.width, "h": fr.height, "spark_ms": ms,
                   "n_ref": len(ref), "n_spark": len(preds), "sam_ms": sam.total_ms, "error": err, "iou": {}}
            for name, conv in CONVENTIONS.items():
                converted = [conv(p, fr.width, fr.height) for p in preds]
                row["iou"][name] = match_score(converted, ref)
            rows.append(row)
            print(f"{fr.name:30s} {concept:20s} sam={len(ref)} spark={len(preds)} "
                  f"spark {ms:6.0f}ms  meanIoU(px)={(sum(row['iou']['pixels']) / max(1, len(ref))):.2f}"
                  + (f"  ERR {err}" if err else ""))

    cost = sam_n * SAM_USD_PER_IMAGE + spark_in / 1e6 * SPARK_USD_PER_M_IN + spark_out / 1e6 * SPARK_USD_PER_M_OUT
    log_cost("spark_grounding", cost, f"{len(rows)} spark calls, {spark_in} in / {spark_out} out tokens")

    ok = [r for r in rows if not r["error"]]
    print(f"\n### Spark box grounding vs SAM reference ({len(ok)}/{len(rows)} calls ok, Spark latency {summarize([r['spark_ms'] for r in ok])})\n")
    print("| convention | refs scored | mean IoU | IoU>=0.5 | IoU>=0.75 |")
    print("|---|---|---|---|---|")
    best = None
    for name in CONVENTIONS:
        v = [i for r in ok for i in r["iou"][name]]
        if not v:
            continue
        m = sum(v) / len(v)
        if best is None or m > best[1]:
            best = (name, m)
        print(f"| {name} | {len(v)} | {m:.2f} | {sum(i >= .5 for i in v) / len(v):.0%} | {sum(i >= .75 for i in v) / len(v):.0%} |")
    if best:
        print(f"\nBest-fitting convention: {best[0]} (mean IoU {best[1]:.2f})")

    print("\n### Count agreement (Spark count == SAM count)\n")
    by = defaultdict(list)
    for r in ok:
        by[r["concept"]].append(r["n_spark"] == r["n_ref"])
    for c, v in by.items():
        print(f"- {c}: {sum(v)}/{len(v)}")
    print("\nNote: SAM is the reference, not ground truth. Eyeball a few frames before trusting low IoU as a Spark failure.")
    out = write_results("spark_grounding", {"args": {k: str(v) for k, v in vars(args).items()}, "rows": rows})
    print(f"\nraw results: {out}\nrun cost est: ${cost:.3f}; total logged ${total_logged_cost():.3f}")


if __name__ == "__main__":
    asyncio.run(main())
