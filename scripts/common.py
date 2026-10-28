"""Shared helpers for the M0 measurement scripts."""
from __future__ import annotations

import base64
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
COST_LOG = DATA_DIR / "cost_log.jsonl"

# Make `import app...` work from scripts.
sys.path.insert(0, str(ROOT / "backend"))

load_dotenv(ROOT / ".env")

API_BASE = os.getenv("META_API_BASE", "https://api.meta.ai/v1")
SPARK_MODEL = os.getenv("SPARK_MODEL", "muse-spark-1.3")
SAM_MODEL = os.getenv("SAM_MODEL", "sam-3.1")

from app.pricing import SAM_USD_PER_IMAGE, SPARK_USD_PER_M_IN, SPARK_USD_PER_M_OUT  # noqa: E402,F401

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def require_key() -> str:
    key = os.getenv("MODEL_API_KEY", "").strip()
    if not key:
        sys.exit("MODEL_API_KEY not set. Copy .env.example to .env and fill it in.")
    return key


def make_client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=require_key(), base_url=API_BASE, max_retries=0, timeout=60)


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(np.floor(k)), int(np.ceil(k))
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(pct(values, 50), 1),
        "p95": round(pct(values, 95), 1),
        "mean": round(statistics.fmean(values), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
    }


@dataclass
class Frame:
    name: str
    jpeg: bytes
    width: int
    height: int

    @property
    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode()


def load_frames(path: Path, max_side: int | None, quality: int = 80, limit: int | None = None) -> list[Frame]:
    """Load images from a file or directory, resize so the long side is <= max_side, re-encode as JPEG."""
    files = [path] if path.is_file() else sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if limit:
        files = files[:limit]
    if not files:
        sys.exit(f"No images found in {path}. Capture some with the phone page (Save sample) first.")
    frames = []
    for f in files:
        img = cv2.imdecode(np.fromfile(str(f), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"skip unreadable image {f}", file=sys.stderr)
            continue
        h, w = img.shape[:2]
        if max_side and max(h, w) > max_side:
            s = max_side / max(h, w)
            img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        frames.append(Frame(f.name, buf.tobytes(), w, h))
    return frames


def box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1]) + max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


# ---------------------------------------------------------------- SAM (client lives in app.sam_client)

from app.sam_client import SamCall, SamObject, sam_call as _sam_call  # noqa: E402,F401


async def sam_call(client, frame: Frame, prompt: str) -> SamCall:
    return await _sam_call(client, frame.data_url, prompt)


def log_cost(script: str, usd: float, detail: str = "") -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with COST_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "script": script, "usd": round(usd, 4), "detail": detail}) + "\n")


def total_logged_cost() -> float:
    if not COST_LOG.exists():
        return 0.0
    return sum(json.loads(l)["usd"] for l in COST_LOG.read_text(encoding="utf-8").splitlines() if l.strip())


def write_results(name: str, payload: dict) -> Path:
    out = DATA_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p
