import os

from app.reasoner.base import Reasoner, ReasonerError, Result


def make_reasoner(backend: str | None = None) -> Reasoner:
    """Pick the reasoner from REASONER_BACKEND: meta | glimmer_modal | spark_open_baseten."""
    backend = (backend or os.getenv("REASONER_BACKEND", "meta")).strip()
    if backend == "meta":
        from app.reasoner.meta import MetaApiReasoner

        return MetaApiReasoner()
    if backend in ("glimmer_modal", "spark_open_baseten"):
        from app.reasoner.openai_compat import OpenAICompatReasoner

        return OpenAICompatReasoner()
    raise ValueError(f"unknown REASONER_BACKEND {backend!r}")


__all__ = ["Reasoner", "ReasonerError", "Result", "make_reasoner"]
