"""Semantic find (F11): a functional-language query -> short noun concept -> World Model lookup.

    "the thing I charge my laptop with"  ->  concept "charger", alternates ["charging cable", "power adapter"]

The resolver never invents locations: it only names the object type. `semantic_find` then asks the
World Model; if the object is not in memory it reports `needs_live_look` so the caller can run a SAM
concept prompt on live or recent frames (not built yet).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.concepts import is_sam_friendly
from app.reasoner.base import Reasoner
from app.world_model import WorldModel, normalize_label

SYSTEM = """You help find everyday objects in a room. The user describes an object by what it does, where it is used, or its name. They may mix English and Hindi.
Reply with the single object type they are looking for, as one short noun phrase in English (1 to 3 words), for example "charger", "remote control", "water bottle".
Rules:
- concept: a physical object type that could be seen in a camera image. No colors, no spatial words, no commas, no negation.
- alternates: up to 3 other noun phrases for the same thing (synonyms or close types), most likely first.
- confidence: 0 to 1, how sure you are that concept is what they mean.
- If a list of known labels is given and one of them fits, use exactly that label as the concept.
- If the request names no findable physical object, return concept "" and confidence 0."""


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept: str
    alternates: list[str]
    confidence: float = Field(ge=0, le=1)


SCHEMA = Resolution.model_json_schema()


def clean(res: Resolution) -> Resolution:
    """Keep only SAM-friendly phrases; promote the first friendly alternate if the concept is not."""
    # Judge the raw phrase: normalising first would strip the commas that make it unfriendly.
    cands = [normalize_label(c) for c in [res.concept, *res.alternates] if c and is_sam_friendly(c)]
    cands = [c for c in dict.fromkeys(cands) if c]
    if not cands:
        return Resolution(concept="", alternates=[], confidence=0.0)
    return Resolution(concept=cands[0], alternates=cands[1:4], confidence=res.confidence if cands[0] == normalize_label(res.concept) else min(res.confidence, 0.5))


async def resolve_concept(reasoner: Reasoner, query: str, known_labels: list[str] | None = None) -> tuple[Resolution, dict]:
    """Returns (resolution, usage) where usage = {input_tokens, output_tokens, latency_ms}."""
    text = f"Request: {query.strip()}"
    if known_labels:
        text += "\nKnown labels in this room: " + ", ".join(sorted(set(known_labels)))
    r = await reasoner.respond([{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                               schema=SCHEMA, effort="low", max_output_tokens=2000)
    res = clean(Resolution.model_validate(r.parsed))
    return res, {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "latency_ms": r.latency_ms}


async def semantic_find_rooms(reasoner: Reasoner, wm: WorldModel, query: str, scans: dict[int, int]) -> dict:
    """Find across rooms, read-only: one concept lookup, then each room's own memory. `scans` maps room id -> the scan that
    is that room's memory. Result: resolution, found {room id: where_is dict or None}, skipped_llm, usage."""
    labels = [o["label"] for sid in scans.values() for o in wm.list_objects(sid)]
    q = normalize_label(query)
    if q and any(normalize_label(l) == q for l in labels):
        res, usage, skipped = Resolution(concept=q, alternates=[], confidence=1.0), {}, True
    else:
        res, usage = await resolve_concept(reasoner, query, labels)
        skipped = False
    found = {rid: (wm.where_is(res.concept, res.alternates, sid) if res.concept else None) for rid, sid in scans.items()}
    return {"resolution": res.model_dump(), "found": found, "skipped_llm": skipped, "usage": usage}


async def semantic_find(reasoner: Reasoner, wm: WorldModel, query: str, scan_id: int | None = None) -> dict:
    """Full F11 lookup. Result keys: resolution, found (where_is dict or None), needs_live_look, skipped_llm, usage."""
    labels = [o["label"] for o in wm.list_objects(scan_id)]
    q = normalize_label(query)
    if q and any(normalize_label(l) == q for l in labels):  # the user typed a known label: no model call needed
        res, usage, skipped = Resolution(concept=q, alternates=[], confidence=1.0), {}, True
    else:
        res, usage = await resolve_concept(reasoner, query, labels)
        skipped = False
    found = wm.where_is(res.concept, res.alternates, scan_id) if res.concept else None
    return {"resolution": res.model_dump(), "found": found, "skipped_llm": skipped, "usage": usage,
            "needs_live_look": bool(res.concept) and found is None}
