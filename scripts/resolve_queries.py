"""M0: does Muse Spark resolve functional-language queries to a short noun concept? (PRD F11)

Runs a fixed set of queries (English and Hindi/English mixed) through app.resolver, with and without
a list of known room labels, and scores whether the concept or an alternate is an acceptable answer.

    python scripts/resolve_queries.py
    python scripts/resolve_queries.py --repeat 3
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from common import SPARK_USD_PER_M_IN, SPARK_USD_PER_M_OUT, log_cost, summarize, total_logged_cost, write_results
from app.concepts import is_sam_friendly
from app.reasoner import make_reasoner
from app.resolver import resolve_concept
from app.world_model import label_match_score

ROOM = ["keys", "headphones", "mug", "notebook", "remote control", "water bottle", "charger", "scissors", "laptop"]

# query -> acceptable concepts (any of them, as concept or alternate, counts as a hit)
CASES = [
    ("the thing I charge my laptop with", {"charger", "charging cable", "power adapter", "laptop charger"}),
    ("what I use to cut paper", {"scissors"}),
    ("something to drink water from", {"water bottle", "mug", "cup", "glass", "bottle"}),
    ("what I listen to music with", {"headphones", "earphones", "earbuds", "speaker"}),
    ("the thing that changes the TV channel", {"remote control", "remote"}),
    ("what I use to unlock the front door", {"keys", "key"}),
    ("where I write my notes", {"notebook", "notepad", "diary"}),
    ("where did I leave my glasses", {"glasses", "eyeglasses", "spectacles"}),
    ("mera charger kahan hai", {"charger", "charging cable", "power adapter"}),
    ("paani ki bottle dikhao", {"water bottle", "bottle"}),
    ("kuch kaatne ke liye kainchi", {"scissors"}),
    ("find my phone", {"phone", "smartphone", "mobile phone"}),
    ("the thing I type on", {"keyboard", "laptop"}),
    ("what is the meaning of life", set()),  # must resolve to nothing
]


def hit(res, accept: set[str]) -> bool:
    if not accept:
        return res.concept == ""
    return any(label_match_score(c, a) >= 1.0 for c in [res.concept, *res.alternates] for a in accept) or \
        any(label_match_score(res.concept, a) >= 0.7 for a in accept)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    reasoner = make_reasoner()
    rows, tin, tout = [], 0, 0
    for with_labels in (False, True):
        for query, accept in CASES:
            for rep in range(args.repeat):
                row = {"query": query, "with_labels": with_labels, "rep": rep}
                try:
                    res, u = await resolve_concept(reasoner, query, ROOM if with_labels else None)
                    tin += u["input_tokens"]
                    tout += u["output_tokens"]
                    row |= {"concept": res.concept, "alternates": res.alternates, "confidence": res.confidence, "hit": hit(res, accept),
                            "friendly": res.concept == "" or is_sam_friendly(res.concept), "ms": u["latency_ms"]}
                except Exception as e:  # noqa: BLE001
                    row |= {"error": f"{type(e).__name__}: {str(e)[:200]}", "hit": False}
                rows.append(row)
                print(f"{'L' if with_labels else '-'} {query[:42]:42s} -> {row.get('concept', '?')!r:20} {row.get('alternates', '')} "
                      f"{'HIT ' if row['hit'] else 'MISS'} {row.get('ms', 0):5.0f}ms" + (f"  ERR {row['error']}" if row.get("error") else ""))

    cost = tin / 1e6 * SPARK_USD_PER_M_IN + tout / 1e6 * SPARK_USD_PER_M_OUT
    log_cost("resolve_queries", cost, f"{len(rows)} calls, {tin} in / {tout} out tokens")
    print("\n### Semantic resolution (F11)\n")
    print("| known labels given | hits | errors | latency ms p50 / p95 |")
    print("|---|---|---|---|")
    for wl in (False, True):
        sel = [r for r in rows if r["with_labels"] == wl]
        ok = [r for r in sel if "error" not in r]
        s = summarize([r["ms"] for r in ok])
        print(f"| {'yes' if wl else 'no'} | {sum(r['hit'] for r in sel)}/{len(sel)} | {len(sel) - len(ok)} | {s.get('p50')} / {s.get('p95')} |")
    unfriendly = [r["concept"] for r in rows if r.get("friendly") is False]
    print(f"\nconcepts that were not SAM-friendly after cleaning: {unfriendly or 'none'}")
    misses = defaultdict(list)
    for r in rows:
        if not r["hit"]:
            misses[r["query"]].append(r.get("concept") or r.get("error"))
    for q, v in misses.items():
        print(f"miss: {q!r} -> {v}")
    out = write_results("resolve_queries", {"rows": rows})
    print(f"\nraw results: {out}\nrun cost est: ${cost:.4f}; total logged ${total_logged_cost():.3f}")


if __name__ == "__main__":
    asyncio.run(main())
