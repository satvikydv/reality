"""Rules for concepts we hand to SAM: one short noun phrase naming one visible object type."""
from __future__ import annotations

import re

_BAD = re.compile(r"[,;:/]|\b(not|no|without|next to|on top of|left|right|above|below|behind|near|and|or)\b")


def is_sam_friendly(concept: str) -> bool:
    c = concept.strip().lower()
    return 1 <= len(c.split()) <= 3 and not _BAD.search(c)


# Names Spark uses interchangeably between frames; the World Model merges only identical labels.
ALIASES = {"smartphone": "phone", "mobile phone": "phone", "cell phone": "phone", "cellphone": "phone", "computer mouse": "mouse",
           "laptop computer": "laptop", "notebook computer": "laptop", "eyeglasses": "glasses", "spectacles": "glasses"}


def canonical_name(label: str) -> str:
    return ALIASES.get(label, label)
