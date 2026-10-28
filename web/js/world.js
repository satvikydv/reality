// The remembered objects the overlay draws, kept fresh from /api/overlay. The server is the one source of truth for
// the FOV (hfov_deg) so what the phone projects uses the same camera model the server anchored with.
export class WorldLayer {
  constructor({ url = "/api/overlay", intervalMs = 2500, fetchFn = (...a) => fetch(...a), now = () => performance.now() } = {}) {
    Object.assign(this, { url, intervalMs, fetchFn, now });
    this.objects = [];       // as served, plus .born (when first seen here) and .hl (highlighted)
    this.hfov = 60;
    this.calibrated = false;
    this.scanId = null;
    this.timer = null;
    this.highlightId = null;
    this.known = new Map();  // id -> born
  }

  async refresh() {
    let r;
    try { r = await (await this.fetchFn(this.url)).json(); } catch { return false; }
    this.apply(r);
    return true;
  }

  apply(r) {
    this.hfov = r.hfov_deg ?? this.hfov;
    this.calibrated = !!r.fov_calibrated;
    this.scanId = r.scan_id ?? null;
    const t = this.now(), seen = new Set();
    this.objects = (r.objects || []).map((o) => {
      seen.add(o.id);
      if (!this.known.has(o.id)) this.known.set(o.id, this.known.size ? t : -1e9); // the first load is not an arrival
      return { ...o, born: this.known.get(o.id), hl: o.id === this.highlightId };
    });
    for (const id of this.known.keys()) if (!seen.has(id)) this.known.delete(id);
  }

  highlight(id) { this.highlightId = id ?? null; for (const o of this.objects) o.hl = o.id === this.highlightId; }
  labels(max = 3) { return [...new Set(this.objects.map((o) => o.label))].slice(0, max); }

  start() { this.stop(); this.refresh(); this.timer = setInterval(() => this.refresh(), this.intervalMs); }
  stop() { clearInterval(this.timer); this.timer = null; }
}
