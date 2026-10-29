"""Which directions a set of frames looked at: ONE definition for the server and the phone.

web/js/coverage.js is a line-for-line port; tests/js/coverage.test.mjs checks it against golden values produced by
this module (scripts/golden_coverage.py), so the ring on the phone and the summary from the server cannot disagree.

A frame at (yaw, pitch) with horizontal FOV `hfov` covers yaw +- REACH * hfov (a conservative 80% of the image) at
1 degree resolution, and the pitch interval pitch +- REACH * vfov. The yaw coverage counts a frame at any pitch.
Pitch bands (low / mid / high) say how much of the room was looked at near the floor, at eye level and near the ceiling;
a band counts as covered by a frame when the frame's pitch interval overlaps it by 40% of the shorter of the two.
"""
from __future__ import annotations

import math

BIN_DEG = 10                 # the phone's ring has 360 / BIN_DEG segments
REACH = 0.4                  # share of the FOV, each side of the optical axis, that counts as seen
BAND_OVERLAP = 0.4
BANDS = (("low", -90.0, -20.0), ("mid", -20.0, 20.0), ("high", 20.0, 90.0))


def _round(x: float) -> int:
    return math.floor(x + 0.5)  # not round(): Python rounds halves to even, JavaScript rounds them up


def vfov_deg(hfov_deg: float, width: float, height: float) -> float:
    return math.degrees(2 * math.atan(math.tan(math.radians(hfov_deg) / 2) * height / width))


def _gaps(cells: list[bool]) -> list[list[int]]:
    gaps, i = [], 0
    while i < 360:
        if cells[i]:
            i += 1
            continue
        j = i
        while j < 360 and not cells[j]:
            j += 1
        gaps.append([i, j - 1])
        i = j
    if len(gaps) > 1 and gaps[0][0] == 0 and gaps[-1][1] == 359:  # one gap across north
        gaps[0] = [gaps[-1][0], gaps[0][1]]
        gaps.pop()
    return gaps


def _bins(cells: list[bool]) -> list[bool]:
    return [sum(cells[i * BIN_DEG:(i + 1) * BIN_DEG]) >= BIN_DEG // 2 for i in range(360 // BIN_DEG)]


def _summary(cells: list[bool]) -> dict:
    n = sum(cells)
    return {"covered_deg": n, "fraction": n / 360, "gaps": _gaps(cells), "bins": _bins(cells)}


def coverage(frames: list[dict], reach: float = REACH) -> dict:
    """frames: dicts with yaw, hfov_deg and optionally pitch (default 0), width, height (default: square, so vfov = hfov).
    Returns covered_deg, fraction, gaps, bins (the ring) and bands {low, mid, high: the same for that pitch band}."""
    all_cells = [False] * 360
    band_cells = {name: [False] * 360 for name, _, _ in BANDS}
    for f in frames:
        hfov = f["hfov_deg"]
        half = reach * hfov
        vh = reach * vfov_deg(hfov, f.get("width") or 1, f.get("height") or 1)
        p = f.get("pitch") or 0.0
        lo, hi = p - vh, p + vh
        hit = [name for name, b0, b1 in BANDS if min(hi, b1) - max(lo, b0) >= BAND_OVERLAP * min(hi - lo, b1 - b0)]
        for d in range(math.trunc(-half), math.trunc(half) + 1):
            c = _round(f["yaw"] + d) % 360
            all_cells[c] = True
            for name in hit:
                band_cells[name][c] = True
    out = _summary(all_cells)
    out["bands"] = {name: _summary(cells) for name, cells in band_cells.items()}
    return out
