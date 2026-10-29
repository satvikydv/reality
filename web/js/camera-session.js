// The camera's lifetime. It starts only when a screen asks for it (acquire), and stops when that screen is left, when the
// page is hidden or closed, or when the browser takes it away. While it is on, the screen is kept awake and portrait is
// requested where the browser allows it. Pure logic over injected pieces, so the rules can be tested without a phone.

export const RELEASED = "Released"; // error name for an acquire that was cancelled by release() before it finished

// Stop every track of these streams, each exactly once (the same stream is often reachable both from the video and from us).
function stopStream(...streams) {
  const seen = new Set();
  for (const s of streams) {
    let tracks = [];
    try { tracks = s?.getTracks?.() ?? []; } catch { /* gone already */ }
    for (const t of tracks) if (!seen.has(t)) { seen.add(t); try { t.stop(); } catch { /* already stopped */ } }
  }
}

export class CameraSession {
  // video: the <video> element. startCamera(video, facing) -> {stream, guard, settings}. orient: OrientationTracker (start/stop).
  constructor({ video, startCamera, orient = null, doc = globalThis.document, win = globalThis, nav = globalThis.navigator,
    scr = globalThis.screen, onState = () => {}, onHidden = () => {}, onEnded = () => {} }) {
    Object.assign(this, { video, startCamera, orient, doc, win, nav, scr, onState, onHidden, onEnded });
    this.state = { camera: "off", wake: false, portrait: false, reason: null };
    this.result = null;
    this._gen = 0;
    this._pending = null;
    this._sentinel = null;
    this._vis = () => { if (this.doc?.hidden && this.state.camera !== "off") { this.release("hidden"); this.onHidden(); } };
    this._hide = () => { if (this.state.camera !== "off") this.release("hidden"); };
    this.doc?.addEventListener?.("visibilitychange", this._vis);
    this.win?.addEventListener?.("pagehide", this._hide);
  }

  get active() { return this.state.camera === "on"; }
  get busy() { return this.state.camera !== "off"; }

  _set(patch) { this.state = { ...this.state, ...patch }; this.onState(this.state); }

  // Start the camera (and the orientation sensors) unless already on. Resolves with startCamera's result.
  acquire(facing = "environment") {
    if (this.active) return Promise.resolve(this.result);
    if (this._pending) return this._pending;
    const gen = ++this._gen;
    this._set({ camera: "starting", reason: null });
    this._pending = (async () => {
      try {
        // Sensors first, without waiting: on iOS their permission prompt is only allowed inside the tap that got us here.
        const sensors = this.orient ? Promise.resolve().then(() => this.orient.start()).catch(() => {}) : null;
        const r = await this.startCamera(this.video, facing);
        if (gen !== this._gen) { // released while starting: the stream that arrived late must not stay on the screen or on
          stopStream(r?.stream);
          if (this.video && this.video.srcObject === r?.stream) this.video.srcObject = null; // (a newer camera's stream is left alone)
          throw Object.assign(new Error("camera released while starting"), { name: RELEASED });
        }
        this.result = r;
        r.stream?.getVideoTracks?.()[0]?.addEventListener?.("ended", () => { if (gen === this._gen) { this.release("ended"); this.onEnded(); } });
        await sensors; // a refusal is not an error here: the page reads the permission state from the tracker
        if (gen !== this._gen) { // released while the sensors were starting: they may have started after release() stopped them
          try { this.orient?.stop(); } catch { /* ignore */ }
          throw Object.assign(new Error("camera released while starting"), { name: RELEASED });
        }
        this._set({ camera: "on" });
        this._keepAwake(gen);
        this._lockPortrait(gen);
        return r;
      } catch (e) {
        if (gen === this._gen) {
          this._stopVideo();
          try { this.orient?.stop(); } catch { /* ignore */ }
          this._set({ camera: "off", reason: "failed" });
        }
        throw e.name === RELEASED || gen === this._gen ? e : Object.assign(new Error("camera released while starting"), { name: RELEASED });
      } finally {
        if (gen === this._gen) this._pending = null;
      }
    })();
    return this._pending;
  }

  _stopVideo() {
    const s = this.video?.srcObject;
    if (s) { stopStream(s); this.video.srcObject = null; }
  }

  _stopAll() {
    stopStream(this.video?.srcObject, this.result?.stream);
    if (this.video) this.video.srcObject = null;
  }

  // Stop every track, the sensors, the wake lock and the orientation lock. Safe to call at any time, also while starting.
  release(reason = "stopped") {
    this._gen++;
    this._pending = null;
    this._stopAll();
    this.result = null;
    try { this.orient?.stop(); } catch { /* ignore */ }
    this._letSleep();
    try { this.scr?.orientation?.unlock?.(); } catch { /* not locked */ }
    this._set({ camera: "off", wake: false, portrait: false, reason });
  }

  async _keepAwake(gen) {
    try {
      if (!this.nav?.wakeLock) return;
      const s = await this.nav.wakeLock.request("screen");
      if (gen !== this._gen) { s.release?.(); return; }
      this._sentinel = s;
      s.addEventListener?.("release", () => { if (this._sentinel === s) { this._sentinel = null; this._set({ wake: false }); } });
      this._set({ wake: true });
    } catch { /* refused (low battery, unsupported): the screen may dim, scanning still works */ }
  }

  _letSleep() {
    const s = this._sentinel;
    this._sentinel = null;
    try { s?.release?.(); } catch { /* already released */ }
  }

  async _lockPortrait(gen) {
    try {
      const o = this.scr?.orientation;
      if (typeof o?.lock !== "function") return;
      await o.lock("portrait");
      if (gen === this._gen) this._set({ portrait: true });
    } catch { /* most browsers only allow this in fullscreen or an installed app; the camera guard still stops a scan if the phone is rotated */ }
  }

  dispose() {
    this.release("disposed");
    this.doc?.removeEventListener?.("visibilitychange", this._vis);
    this.win?.removeEventListener?.("pagehide", this._hide);
  }
}
