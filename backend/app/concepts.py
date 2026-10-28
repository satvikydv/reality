"""Rules for concepts we hand to SAM: one short noun phrase naming one visible object type."""
from __future__ import annotations

import re

_BAD = re.compile(r"[,;:/]|\b(not|no|without|next to|on top of|left|right|above|below|behind|near|and|or)\b")


def is_sam_friendly(concept: str) -> bool:
    c = concept.strip().lower()
    return 1 <= len(c.split()) <= 3 and not _BAD.search(c)
