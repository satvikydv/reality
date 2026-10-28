"""sam-3.1 client: concept prompts and box prompts over the Responses stream.

Boxes come back in pixel coordinates of the image that was sent (x1, y1, x2, y2, right and bottom
half-open). Callers that resize frames must anchor with the resized width and height.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import numpy as np

SAM_MODEL = os.getenv("SAM_MODEL", "sam-3.1")


@dataclass
class SamObject:
    object_id: str
    box: tuple[float, float, float, float] | None  # x1,y1,x2,y2 (half-open right/bottom), image px
    confidence: float | None
    mask_area_px: int | None
    polygon: list[list[float]] | None = None  # simplified outline of the mask, image px of the sent image


def raster_polygon(raster: np.ndarray, left: float, top: float, scale_x: float = 1.0, scale_y: float = 1.0,
                   max_points: int = 32) -> list[list[float]] | None:
    """Outline of the largest blob in a 0/1 mask, simplified to at most `max_points` vertices and placed in image
    pixels (`left`, `top` is the mask's corner; scales map mask pixels to image pixels). None for an empty mask."""
    import cv2

    contours, _ = cv2.findContours((raster > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < 4:
        return None
    eps = 0.01 * cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, eps, True)
    while len(approx) > max_points:
        eps *= 1.4
        approx = cv2.approxPolyDP(c, eps, True)
    return [[round(left + (float(x) + 0.5) * scale_x, 1), round(top + (float(y) + 0.5) * scale_y, 1)] for [[x, y]] in approx]


def mask_info(mask, bounds) -> tuple[int, list[list[float]] | None]:
    """(area in mask pixels, outline in image pixels) of a parsed SAM mask placed at `bounds` in the sent image."""
    from meta_sam_parser import decode_mask_to_raster

    raster = np.frombuffer(decode_mask_to_raster(mask), dtype=np.uint8)
    mw, mh = int(mask.width), int(mask.height)
    poly = raster_polygon(raster.reshape(mh, mw), bounds.left, bounds.top, (bounds.right - bounds.left) / mw, (bounds.bottom - bounds.top) / mh)
    return int(raster.sum()), poly


@dataclass
class SamCall:
    objects: list[SamObject]
    total_ms: float
    first_event_ms: float | None
    outcome: str
    n_diagnostics: int
    error: str | None = None


def box_prompt(width: int, height: int, boxes: list[tuple[float, float, float, float]]) -> str:
    """Prompt text that asks SAM to segment the objects inside up to 16 boxes."""
    from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt

    objs = [PromptObject(i + 1, [FramePrompt(0, PromptBox(max(0, int(b[0])), max(0, int(b[1])), min(width, int(b[2]) + 1), min(height, int(b[3]) + 1)))])
            for i, b in enumerate(boxes[:16])]
    return build_object_prompt(size=(width, height), objects=objs)


async def sam_call(client, data_url: str, prompt: str) -> SamCall:
    """One sam-3.1 request (concept text or a box prompt). Streams and parses with meta-sam-parser.
    Never raises: failures come back as outcome "error" with the message in `error`."""
    from meta_sam_parser import image_segmentation_format, parse_responses_stream

    t0 = time.perf_counter()
    first = None
    raw = None
    try:
        raw = await client.responses.create(
            model=SAM_MODEL,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": data_url}]}],
            stream=True,
            metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
        )

        async def tap():
            nonlocal first
            async for ev in raw:
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
                yield ev

        parsed = parse_responses_stream(tap(), image_segmentation_format())
        async for _ in parsed:
            pass
        result = await parsed.final_result()
    except Exception as e:  # noqa: BLE001 - callers record the failure and continue
        return SamCall([], (time.perf_counter() - t0) * 1000, first, "error", 0, f"{type(e).__name__}: {e}")
    finally:
        if raw is not None:
            try:
                await raw.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
    total = (time.perf_counter() - t0) * 1000

    objs: dict[str, SamObject] = {}
    for rec in result.records:
        if rec.kind == "text":
            continue
        o = objs.setdefault(rec.object_id, SamObject(rec.object_id, None, None, None))
        if rec.confidence is not None:
            o.confidence = rec.confidence
        if rec.kind == "box":
            o.box = (rec.left, rec.top, rec.right, rec.bottom)
        elif rec.kind == "mask":
            b = rec.bounds
            if o.box is None:
                o.box = (b.left, b.top, b.right, b.bottom)
            try:
                o.mask_area_px, o.polygon = mask_info(rec.mask, b)
            except Exception:  # noqa: BLE001 - the box alone is still a usable detection
                o.mask_area_px = None
    return SamCall(list(objs.values()), total, first, result.outcome.status, len(result.diagnostics))


def make_client():
    """AsyncOpenAI client for the Meta Model API (no SDK retries; callers handle failures)."""
    from openai import AsyncOpenAI

    key = os.getenv("MODEL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MODEL_API_KEY not set")
    return AsyncOpenAI(api_key=key, base_url=os.getenv("META_API_BASE", "https://api.meta.ai/v1"), max_retries=0, timeout=30)


def make_segmenter(client):
    """The async callable the scan pipeline uses: (data_url, concept) -> SamCall."""
    async def segment(data_url: str, concept: str) -> SamCall:
        return await sam_call(client, data_url, concept)
    return segment
