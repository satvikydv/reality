// World-locked overlay math on the phone. Port of the reference in backend/app/anchoring.py
// (homography, warp_points, footprint_outline); tests/js/project.test.mjs checks both agree.
//
// Only rotation is known (no translation, no depth), so an object is a direction plus an outline, never a
// distance. A stored mask outline is carried into the live view with the rotation-only homography
// H = K_cur * B_cur^T * B_cap * K_cap^-1; without a mask the object's angular footprint is projected instead.
import { cameraBasis, directionToPixel } from "./guide.js";

// An object whose sightings disagree by more than this (degrees) is not drawn: a wrong box is worse than none.
export const MAX_UNCERTAINTY_DEG = 12;
// Opacity by age since last seen: full for HOLD_S, then linearly down to FLOOR at FLOOR_AGE_S, never lower.
export const FADE = { HOLD_S: 30, FLOOR_AGE_S: 1800, FLOOR: 0.25 };
// An outline larger than this many view-widths is an artefact of a near-grazing projection; drop it.
const MAX_OUTLINE_SPAN = 4;

export function fadeAlpha(ageS) {
  if (!(ageS > FADE.HOLD_S)) return 1;
  const t = Math.min(1, (ageS - FADE.HOLD_S) / (FADE.FLOOR_AGE_S - FADE.HOLD_S));
  return 1 - t * (1 - FADE.FLOOR);
}

export const isReliable = (o, max = MAX_UNCERTAINTY_DEG) => (o.uncertainty_deg ?? 0) <= max;

const matmul = (a, b) => a.map((_, i) => [0, 1, 2].map((j) => a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j]));
const basisMatrix = (yaw, pitch, roll) => {
  const { right, up, fwd } = cameraBasis(yaw, pitch, roll);
  return [0, 1, 2].map((i) => [right[i], up[i], fwd[i]]); // columns right, up, forward: earth = B * camera
};
const focal = (width, hfov) => width / 2 / Math.tan((hfov * Math.PI) / 360);
const K = (w, h, hfov) => { const f = focal(w, hfov); return [[f, 0, w / 2], [0, -f, h / 2], [0, 0, 1]]; };
const Kinv = (w, h, hfov) => { const f = focal(w, hfov); return [[1 / f, 0, -w / (2 * f)], [0, -1 / f, h / (2 * f)], [0, 0, 1]]; };

// cap, cur: {yaw, pitch, roll, width, height, hfov}. Where a capture-image pixel lands in the current view.
export function homography(cap, cur) {
  const bCap = basisMatrix(cap.yaw, cap.pitch, cap.roll ?? 0);
  const bCur = basisMatrix(cur.yaw, cur.pitch, cur.roll ?? 0);
  const bCurT = [0, 1, 2].map((i) => [bCur[0][i], bCur[1][i], bCur[2][i]]);
  return matmul(matmul(K(cur.width, cur.height, cur.hfov), matmul(bCurT, bCap)), Kinv(cap.width, cap.height, cap.hfov));
}

// Pixels through a homography; null when any point falls behind the camera.
export function warpPoints(h, pts) {
  const out = [];
  for (const [u, v] of pts) {
    const x = h[0][0] * u + h[0][1] * v + h[0][2], y = h[1][0] * u + h[1][1] * v + h[1][2], w = h[2][0] * u + h[2][1] * v + h[2][2];
    if (w <= 1e-6) return null;
    out.push([x / w, y / w]);
  }
  return out;
}

// ext = [yaw0, dyawLo, dyawHi, pitchLo, pitchHi]: the object's angular footprint, projected edge by edge.
export function footprintOutline(ext, cur, perEdge = 4) {
  const [yaw0, lo, hi, plo, phi] = ext;
  const n = Math.max(1, perEdge), at = (i) => i / n;
  const bottom = [], right = [], top = [], left = [];
  for (let i = 0; i < n; i++) {
    bottom.push([lo + (hi - lo) * at(i), plo]); right.push([hi, plo + (phi - plo) * at(i)]);
    top.push([hi - (hi - lo) * at(i), phi]); left.push([lo, phi - (phi - plo) * at(i)]);
  }
  const edge = [...bottom, ...right, ...top, ...left]; // once around the rectangle, so the outline is a simple polygon
  const out = [];
  for (const [dy, p] of edge) {
    const px = directionToPixel(yaw0 + dy, p, cur.width, cur.height, cur.yaw, cur.pitch, cur.roll ?? 0, cur.hfov);
    if (px === null) return null;
    out.push(px);
  }
  return out;
}

// How the camera image is laid on a w x h screen with object-fit: cover.
export function coverFit(videoW, videoH, w, h) {
  const s = Math.max(w / videoW, h / videoH);
  return { s, ox: (w - videoW * s) / 2, oy: (h - videoH * s) / 2 };
}
const toScreen = (p, fit) => [fit.ox + p[0] * fit.s, fit.oy + p[1] * fit.s];

const span = (pts) => {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  return { x0, x1, y0, y1, w: x1 - x0, h: y1 - y0 };
};

// o: an object from /api/overlay. cur: the live camera {yaw, pitch, roll, width, height, hfov} in video pixels.
// Returns null when the object should not be drawn, else {anchor, outline, source, alpha} in screen pixels.
export function projectObject(o, cur, fit, { maxUncertainty = MAX_UNCERTAINTY_DEG } = {}) {
  if (!isReliable(o, maxUncertainty)) return null;
  const a = directionToPixel(o.yaw, o.pitch, cur.width, cur.height, cur.yaw, cur.pitch, cur.roll ?? 0, cur.hfov);
  const big = MAX_OUTLINE_SPAN * cur.width;
  let outline = null, source = null;
  if (o.mask && o.mask.poly && o.mask.poly.length >= 3) {
    const w = warpPoints(homography(o.mask, cur), o.mask.poly);
    if (w && span(w).w < big && span(w).h < big) { outline = w; source = "mask"; }
  }
  if (!outline && o.footprint) {
    const f = footprintOutline(o.footprint, cur);
    if (f && span(f).w < big && span(f).h < big) { outline = f; source = "footprint"; }
  }
  if (!a && !outline) return null;
  return { anchor: a && toScreen(a, fit), outline: outline && outline.map((p) => toScreen(p, fit)), source, alpha: fadeAlpha(o.age_s ?? 0) };
}
