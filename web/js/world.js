// The remembered objects the overlay draws, kept fresh from /api/overlay. The server is the one source of truth for
// the FOV (hfov_deg) so what the phone projects uses the same camera model the server anchored with.
export class WorldLayer {
  constructor({ url = "/api/overlay", intervalMs = 2500, fetchFn = (...a) => fetch(...a), now = () => performance.now() } = {}) {
    Object.assign(this, { url, intervalMs, fetchFn, now });
    this.objects = [];       // as served, plus .born (when first seen here) and .hl (highlighted)
    this.hfov = 60;
    this.calibrated = false;
    this.calibration = null; // {width, height, zoom}: the camera state the FOV was measured in
    this.scanId = null;
    this.timer = null;
    this.highlightId = null;
    this.known = new Map();  // id -> born
    this.notes = [];         // notes left in the room, as served: {id, text, author, yaw, pitch, label, created_at}
    this.asked = new Map();  // object id -> why it answers the question just asked (empty: no question is being shown)
    this.ghosts = [];        // what an earlier scan had that the room's memory does not: {label, yaw, pitch, kind, unsure}
    this.ghostWhen = null;   // when that earlier scan was made (seconds)
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
    this.calibration = r.calibration ?? null;
    this.scanId = r.scan_id ?? null;
    this.notes = r.notes || [];
    const t = this.now(), seen = new Set();
    this.objects = (r.objects || []).map((o) => {
      seen.add(o.id);
      if (!this.known.has(o.id)) this.known.set(o.id, this.known.size ? t : -1e9); // the first load is not an arrival
      return { ...o, born: this.known.get(o.id), hl: o.id === this.highlightId };
    });
    for (const id of this.known.keys()) if (!seen.has(id)) this.known.delete(id);
  }

  setAsk(matches) { this.asked = new Map((matches || []).map((m) => [m.object_id, m.reason || ""])); }
  setGhosts(ghosts, when = null) { this.ghosts = ghosts || []; this.ghostWhen = this.ghosts.length ? when : null; }
  highlight(id) { this.highlightId = id ?? null; for (const o of this.objects) o.hl = o.id === this.highlightId; }
  labels(max = 3) { return [...new Set(this.objects.map((o) => o.label))].slice(0, max); }

  start() { this.stop(); this.refresh(); this.timer = setInterval(() => this.refresh(), this.intervalMs); }
  stop() { clearInterval(this.timer); this.timer = null; }
}
