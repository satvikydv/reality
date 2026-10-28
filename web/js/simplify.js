// Mask outlines for the calm overlay: reduce a SAM polygon to its corners, then round it, so it reads as one clean shape instead of
// a jagged edge. Pure functions over closed polygons ([[x, y], ...], the last point not repeated).

const dist2 = (p, a, b) => { // squared distance from p to the segment a-b
  const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
  const x = a[0] + t * dx - p[0], y = a[1] + t * dy - p[1];
  return x * x + y * y;
};

function rdp(pts, eps2) { // Ramer-Douglas-Peucker on an open polyline
  if (pts.length < 3) return pts;
  let idx = -1, max = eps2;
  for (let i = 1; i < pts.length - 1; i++) { const d = dist2(pts[i], pts[0], pts[pts.length - 1]); if (d > max) { max = d; idx = i; } }
  if (idx < 0) return [pts[0], pts[pts.length - 1]];
  return [...rdp(pts.slice(0, idx + 1), eps2).slice(0, -1), ...rdp(pts.slice(idx), eps2)];
}

// Keep the points that matter: every point further than `eps` pixels from the straight line between its kept neighbours is a corner.
export function simplifyPolygon(poly, eps = 3) {
  if (!poly || poly.length < 4) return poly ? poly.slice() : [];
  // a closed ring has no natural start: split it at the point furthest from the first, so no corner is lost at the seam
  let far = 0, best = -1;
  for (let i = 1; i < poly.length; i++) { const d = (poly[i][0] - poly[0][0]) ** 2 + (poly[i][1] - poly[0][1]) ** 2; if (d > best) { best = d; far = i; } }
  const a = poly.slice(0, far + 1), b = [...poly.slice(far), poly[0]];
  const out = [...rdp(a, eps * eps).slice(0, -1), ...rdp(b, eps * eps).slice(0, -1)];
  return out.length >= 3 ? out : poly.slice(0, 3);
}

// Chaikin corner cutting: each edge is replaced by two points a quarter and three quarters along it, which rounds every corner.
export function smoothPolygon(poly, iterations = 1) {
  let p = poly;
  for (let k = 0; k < iterations && p.length >= 3; k++) {
    const q = [];
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b = p[(i + 1) % p.length];
      q.push([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]], [0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]]);
    }
    p = q;
  }
  return p;
}

export const polygonArea = (p) => Math.abs(p.reduce((s, a, i) => { const b = p[(i + 1) % p.length]; return s + a[0] * b[1] - b[0] * a[1]; }, 0)) / 2;

export const prepareOutline = (poly, cfg) => (!poly || poly.length < 3 ? poly : smoothPolygon(simplifyPolygon(poly, cfg.simplifyPx), cfg.smoothIterations));

// The same mask is simplified once, not every frame.
const cache = new Map();
export function cachedOutline(id, poly, cfg) {
  const key = `${id}:${poly?.length}:${cfg.simplifyPx}:${cfg.smoothIterations}`;
  let v = cache.get(key);
  if (!v) { v = prepareOutline(poly, cfg); cache.set(key, v); if (cache.size > 200) cache.delete(cache.keys().next().value); }
  return v;
}
