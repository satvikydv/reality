// Which directions a set of frames looked at. Port of backend/app/coverage.py: the same definition on the phone and
// the server (tests/js/coverage.test.mjs checks this against golden values from the Python module).
//
// A frame at (yaw, pitch) with horizontal FOV hfov covers yaw +- REACH * hfov at 1 degree resolution and the pitch
// interval pitch +- REACH * vfov. Yaw coverage counts a frame at any pitch; the pitch bands (low / mid / high) say how
// much was looked at near the floor, at eye level and near the ceiling.

export const BIN_DEG = 10;
export const REACH = 0.4;
export const BAND_OVERLAP = 0.4;
export const BANDS = [["low", -90, -20], ["mid", -20, 20], ["high", 20, 90]];

const round = (x) => Math.floor(x + 0.5); // Python's round() sends halves to even; this must match the Python module
export const vfovDeg = (hfov, width, height) => (2 * Math.atan(Math.tan((hfov * Math.PI) / 360) * height / width) * 180) / Math.PI;

function gapsOf(cells) {
  const gaps = [];
  let i = 0;
  while (i < 360) {
    if (cells[i]) { i++; continue; }
    let j = i;
    while (j < 360 && !cells[j]) j++;
    gaps.push([i, j - 1]);
    i = j;
  }
  if (gaps.length > 1 && gaps[0][0] === 0 && gaps[gaps.length - 1][1] === 359) { // one gap across north
    gaps[0] = [gaps[gaps.length - 1][0], gaps[0][1]];
    gaps.pop();
  }
  return gaps;
}

function summary(cells) {
  const n = cells.filter(Boolean).length;
  const bins = [];
  for (let i = 0; i < 360 / BIN_DEG; i++) bins.push(cells.slice(i * BIN_DEG, (i + 1) * BIN_DEG).filter(Boolean).length >= BIN_DEG / 2);
  return { covered_deg: n, fraction: n / 360, gaps: gapsOf(cells), bins };
}

// frames: {yaw, hfov_deg, pitch = 0, width, height}. Same result shape as the Python module.
export function coverage(frames, reach = REACH) {
  const all = new Array(360).fill(false);
  const band = Object.fromEntries(BANDS.map(([n]) => [n, new Array(360).fill(false)]));
  for (const f of frames) {
    const hfov = f.hfov_deg, half = reach * hfov;
    const vh = reach * vfovDeg(hfov, f.width || 1, f.height || 1), p = f.pitch || 0, lo = p - vh, hi = p + vh;
    const hit = BANDS.filter(([, b0, b1]) => Math.min(hi, b1) - Math.max(lo, b0) >= BAND_OVERLAP * Math.min(hi - lo, b1 - b0)).map(([n]) => n);
    for (let d = Math.trunc(-half); d <= Math.trunc(half); d++) {
      const c = ((round(f.yaw + d) % 360) + 360) % 360;
      all[c] = true;
      for (const n of hit) band[n][c] = true;
    }
  }
  return { ...summary(all), bands: Object.fromEntries(BANDS.map(([n]) => [n, summary(band[n])])) };
}
