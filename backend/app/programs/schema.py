"""Perception-program schema (PRD section 8).

Predicates are a tagged tree (`op` discriminator) so the model's output validates against a
strict JSON schema and the interpreter can dispatch without `eval`.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

Cmp = Literal["==", "!=", ">=", "<=", ">", "<"]


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Exists(_Node):
    op: Literal["exists"]
    concept: str


class Absent(_Node):
    op: Literal["absent"]
    concept: str


class Count(_Node):
    op: Literal["count"]
    concept: str
    cmp: Cmp
    n: int


class Overlaps(_Node):
    op: Literal["overlaps"]
    a: str
    b: str
    min_iou: float


class ContainedIn(_Node):
    op: Literal["contained_in"]
    a: str
    b: str
    min_frac: float


class Near(_Node):
    op: Literal["near"]
    a: str
    b: str
    max_px: float


class Moved(_Node):
    op: Literal["moved"]
    concept: str
    min_px: float
    window_s: float


class Judge(_Node):
    op: Literal["judge"]
    concept: str
    question: str
    min_confidence: float


class All(_Node):
    op: Literal["all"]
    items: list["Predicate"]


class AnyOf(_Node):
    op: Literal["any"]
    items: list["Predicate"]


class Not(_Node):
    op: Literal["not"]
    item: "Predicate"


Predicate = Annotated[
    Union[Exists, Absent, Count, Overlaps, ContainedIn, Near, Moved, Judge, All, AnyOf, Not],
    Field(discriminator="op"),
]

All.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()


class MistakeHint(_Node):
    when: Predicate
    say: str


class Step(_Node):
    id: str
    instruction: str
    predicate: Predicate
    mistake_hints: list[MistakeHint]


class Plan(_Node):
    task: str
    steps: list[Step] = Field(min_length=1)


def leaf_concepts(p) -> list[str]:
    """All SAM concepts a predicate tree refers to."""
    out: list[str] = []
    if isinstance(p, (All, AnyOf)):
        for c in p.items:
            out += leaf_concepts(c)
    elif isinstance(p, Not):
        out += leaf_concepts(p.item)
    else:
        for f in ("concept", "a", "b"):
            v = getattr(p, f, None)
            if isinstance(v, str):
                out.append(v)
    return out


def predicate_ops(p) -> list[str]:
    out = [p.op]
    if isinstance(p, (All, AnyOf)):
        for c in p.items:
            out += predicate_ops(c)
    elif isinstance(p, Not):
        out += predicate_ops(p.item)
    return out
