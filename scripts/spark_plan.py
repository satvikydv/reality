"""M0: can Muse Spark emit a valid perception-program plan (PRD section 8) via structured output?

Tries strict json_schema first; if the endpoint rejects the schema (recursive oneOf/anyOf is
the likely culprit [VERIFY]) falls back to plain JSON mode with the schema in the prompt.
Validates every result with pydantic and checks SAM-friendliness of concepts (short noun phrase,
no commas/negation).

    python scripts/spark_plan.py --repeat 5
    python scripts/spark_plan.py --image data/frames/kit.jpg --task "Assemble the kit on the mat"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from common import (
    SPARK_MODEL,
    SPARK_USD_PER_M_IN,
    SPARK_USD_PER_M_OUT,
    load_frames,
    log_cost,
    make_client,
    summarize,
    total_logged_cost,
    write_results,
)
from app.programs.schema import Plan, leaf_concepts, predicate_ops

DEFAULT_TASKS = [
    "Assemble the small color-coded brick kit on the desk: base plate first, then the red bricks, then the blue bricks on top.",
    "Attach both black wheels to the red chassis and place it in the middle of the mat.",
    "Wire an LED with a resistor on the breadboard so the LED lights from the 5V rail.",
]

SYSTEM = """You compile a physical task into a verifiable perception program.
Each step has: an instruction for the person, a predicate that a deterministic checker evaluates
on segmentation masks of the camera view, and mistake_hints (predicate + short message) for likely errors.

Predicate ops: exists{concept}, absent{concept}, count{concept,cmp,n}, overlaps{a,b,min_iou},
contained_in{a,b,min_frac}, near{a,b,max_px}, moved{concept,min_px,window_s},
judge{concept,question,min_confidence}, all{items}, any{items}, not{item}.

Rules:
- Every concept is ONE short noun phrase (1-3 words) naming a single visible object type, e.g. "red brick". No commas, no negation, no spatial words.
- Prefer geometry ops (exists/count/overlaps/contained_in/near). Use judge only for fine states geometry cannot decide.
- 3 to 6 steps. Steps are ordered and each predicate must be checkable from the camera view alone.
- mistake_hints may be empty but include at least one for the trickiest step.
Return JSON only."""


def sam_friendly(concept: str) -> bool:
    words = concept.split()
    return 1 <= len(words) <= 3 and not re.search(r"[,;:]|\b(not|no|without|next to|on top of|left|right|above|below)\b", concept.lower())


async def one_call(client, task: str, frame, mode: str, effort: str):
    user = [{"type": "input_text", "text": f"Task: {task}"}]
    if frame:
        user.append({"type": "input_image", "image_url": frame.data_url})
        user[0]["text"] += "\nThe image shows the current workspace; name only parts you can see or that the task implies."
    sys_text = SYSTEM
    kwargs = {}
    if mode == "strict":
        schema = Plan.model_json_schema()
        kwargs["text"] = {"format": {"type": "json_schema", "name": "plan", "schema": schema, "strict": True}}
    else:
        sys_text += "\n\nOutput must match this JSON schema exactly:\n" + json.dumps(Plan.model_json_schema())
        kwargs["text"] = {"format": {"type": "json_object"}}
    t0 = time.perf_counter()
    resp = await client.responses.create(
        model=SPARK_MODEL,
        input=[{"role": "system", "content": sys_text}, {"role": "user", "content": user}],
        reasoning={"effort": effort},
        max_output_tokens=12000,
        **kwargs,
    )
    ms = (time.perf_counter() - t0) * 1000
    u = getattr(resp, "usage", None)
    toks = (getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0) if u else (0, 0)
    return resp.output_text, ms, toks


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", action="append", help="task text; repeatable. Defaults to 3 built-in tasks")
    ap.add_argument("--image", type=Path, help="optional workspace photo")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--effort", default="low", choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--mode", default="auto", choices=["auto", "strict", "json"])
    args = ap.parse_args()

    tasks = args.task or DEFAULT_TASKS
    frame = load_frames(args.image, 1024, 80, 1)[0] if args.image else None
    client = make_client()

    mode = args.mode
    if mode == "auto":
        try:
            await one_call(client, tasks[0], frame, "strict", args.effort)
            mode = "strict"
            print("strict json_schema accepted by endpoint")
        except Exception as e:  # noqa: BLE001
            print(f"strict json_schema rejected ({type(e).__name__}: {str(e)[:200]}); falling back to json_object mode")
            mode = "json"

    rows, tin, tout = [], 0, 0
    for task in tasks:
        for rep in range(args.repeat):
            row = {"task": task, "rep": rep, "mode": mode}
            try:
                text, ms, (ti, to) = await one_call(client, task, frame, mode, args.effort)
                tin += ti
                tout += to
                row |= {"ms": ms, "tokens_in": ti, "tokens_out": to, "raw": text}
                plan = Plan.model_validate_json(text)
                concepts = [c for s in plan.steps for c in leaf_concepts(s.predicate)]
                hint_concepts = [c for s in plan.steps for h in s.mistake_hints for c in leaf_concepts(h.when)]
                row |= {
                    "valid": True, "steps": len(plan.steps),
                    "ops": [o for s in plan.steps for o in predicate_ops(s.predicate)],
                    "concepts": concepts + hint_concepts,
                    "bad_concepts": [c for c in concepts + hint_concepts if not sam_friendly(c)],
                }
            except ValidationError as e:
                row |= {"valid": False, "error": f"schema: {e.errors()[0]['loc']} {e.errors()[0]['msg']}"}
            except Exception as e:  # noqa: BLE001
                row |= {"valid": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            rows.append(row)
            print(f"{task[:48]:48s} #{rep} {'OK ' if row['valid'] else 'BAD'} {row.get('ms', 0):6.0f}ms "
                  + (f"{row['steps']} steps, {len(row['bad_concepts'])} non-SAM concepts" if row["valid"] else row.get("error", "")))

    cost = tin / 1e6 * SPARK_USD_PER_M_IN + tout / 1e6 * SPARK_USD_PER_M_OUT
    log_cost("spark_plan", cost, f"{len(rows)} calls")
    valid = [r for r in rows if r["valid"]]
    print(f"\n### Spark plan generation (mode={mode}, effort={args.effort})\n")
    print(f"- schema-valid: {len(valid)}/{len(rows)}")
    print(f"- latency ms: {summarize([r['ms'] for r in rows if 'ms' in r])}")
    if valid:
        ops = Counter(o for r in valid for o in r["ops"])
        print(f"- ops used: {dict(ops)}")
        all_c = [c for r in valid for c in r["concepts"]]
        bad = [c for r in valid for c in r["bad_concepts"]]
        print(f"- concepts SAM-friendly: {len(all_c) - len(bad)}/{len(all_c)}" + (f"; offenders: {sorted(set(bad))[:8]}" if bad else ""))
        print(f"- judge share of predicate nodes: {ops.get('judge', 0) / max(1, sum(ops.values())):.0%}")
        print(f"- tokens avg in/out: {tin // len(rows)} / {tout // len(rows)}")
    errs = Counter(r["error"] for r in rows if not r["valid"])
    if errs:
        print(f"- failures: {dict(errs)}")
    if valid:
        print("\nSample plan:\n" + json.dumps(json.loads(valid[0]["raw"]), indent=2)[:2500])
    out = write_results("spark_plan", {"args": {k: str(v) for k, v in vars(args).items()}, "rows": rows})
    print(f"\nraw results: {out}\nrun cost est: ${cost:.3f}; total logged ${total_logged_cost():.3f}")


if __name__ == "__main__":
    asyncio.run(main())
