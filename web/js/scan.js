// Room scan controller (F10): watches the camera heading, sends a keyframe whenever the phone has turned
// far enough and is steady, tracks coverage, and folds the server's progress events into one state object.
import { Coverage, KeyframePolicy } from "/js/keyframe.js";
import { CameraSettingsChanged } from "/js/camera-lock.js";

const MAX_IN_FLIGHT = 24; // frames sent but not yet answered; beyond this we ask the user to wait
const MAX_BACKLOG = 1024 * 1024; // bytes still in the socket buffer
const AUTO_FINISH = 0.97; // coverage fraction that ends the scan by itself

export class ScanController {
  // deps: video, orient (OrientationTracker), sock (FrameSocket), grabJpeg(video, side, quality), camera() -> object, onChange(state),
  // fov() -> the horizontal field of view in degrees (the one the server uses), guard (CameraGuard, optional)
  constructor({ video, orient, sock, grabJpeg, camera, onChange, fov = () => 60, guard = null }) {
    Object.assign(this, { video, orient, sock, grabJpeg, camera, onChange, fov, guard });
    this.policy = new KeyframePolicy();
    this.coverage = new Coverage();
    this.timer = null;
    this.busy = false;
    this.reset();
  }

  reset() {
    this.policy.reset();
    this.coverage.reset();
    this.st = { phase: "idle", sent: 0, queued: 0, done: 0, stored: 0, rejected: 0, objects: 0, hint: "", status: "", yaw: null, summary: null, error: null, names: [] };
  }

  get phase() { return this.st.phase; }
  get fraction() { return this.coverage.fraction; }

  _emit() { this.onChange(this.st, this.coverage); }

  start() {
    if (this.st.phase === "scanning" || this.st.phase === "starting") return;
    this.reset();
    if (!this.sock.sendJson({ type: "scan_start", name: new Date().toISOString() })) {
      this.st.phase = "error";
      this.st.error = "not connected to the server";
      return this._emit();
    }
    this.st.phase = "starting";
    this.st.status = "starting…";
    this.timer = setInterval(() => this._tick(), 100);
    this._emit();
  }

  done() {
    if (this.st.phase !== "scanning") return;
    this.st.phase = "finishing";
    this.st.status = `processing ${this.st.sent - this.st.done - this.st.rejected} frame(s)…`;
    this.sock.sendJson({ type: "scan_end" });
    this._emit();
  }

  abort(message) {
    clearInterval(this.timer);
    this.st.phase = "error";
    this.st.error = message;
    this._emit();
  }

  onEvent(ev) {
    const st = this.st;
    switch (ev.type) {
      case "scan_started": st.phase = "scanning"; st.status = "scanning: pan slowly"; break;
      case "frame_queued": st.queued = ev.queued; break;
      case "frame_rejected": st.rejected++; st.hint = ev.reason; break;
      case "frame_done":
        st.done++;
        if (ev.stored) st.stored++;
        st.objects = ev.objects;
        st.names = ev.names;
        if (ev.errors?.length) st.hint = `frame ${ev.seq}: ${ev.errors[0]}`;
        break;
      case "frame_error": st.done++; st.hint = `frame ${ev.seq}: ${ev.error}`; break;
      case "scan_summary":
        clearInterval(this.timer);
        st.phase = "done";
        st.summary = ev;
        st.status = ev.speech;
        break;
      case "error":
        clearInterval(this.timer);
        st.phase = "error";
        st.error = ev.message;
        break;
      default: return;
    }
    this._emit();
  }

  _pose() {
    const s = this.orient.latest.absolute || this.orient.latest.relative;
    if (!s) return null;
    return { yaw: s.yaw, pitch: s.pitch, t: s.t, age: performance.timeOrigin + performance.now() - s.t };
  }

  async _tick() {
    const st = this.st;
    const pose = this._pose();
    if (pose) st.yaw = pose.yaw;
    if (st.phase !== "scanning") return this._emit();
    if (!pose) { st.hint = "waiting for orientation sensors"; return this._emit(); }
    if (pose.age > 500) { st.hint = "orientation sensor stalled"; return this._emit(); }
    const d = this.policy.update(pose);
    st.hint = d.hint || "";
    if (!st.hint) {
      const gap = this.coverage.nextGap(pose.yaw);
      if (gap && this.coverage.fraction > 0.3) st.hint = `gap ${gap.deg} deg to the ${gap.dir}`;
    }
    if (this.guard) { // the field of view only holds for the locked camera state: a changed camera ends the scan
      const changed = this.guard.diff();
      if (changed.length) return this.abort(new CameraSettingsChanged(changed).message);
    }
    if (d.capture && !this.busy) {
      if (this.sock.backlog > MAX_BACKLOG || st.sent - st.done - st.rejected > MAX_IN_FLIGHT) {
        this.policy.undo();
        st.hint = "link busy, wait a moment";
      } else {
        this.busy = true;
        try {
          const blob = await this.grabJpeg(this.video, 960, 0.85);
          const changed = this.guard ? this.guard.diff() : [];
          if (changed.length) { this.policy.undo(); this.abort(new CameraSettingsChanged(changed).message); } // the frame was grabbed in the new state: do not send it
          else if (blob) {
            const meta = { orientation: this.orient.snapshot(), camera: this.camera() };
            await this.sock.send(blob, { scan: true, meta });
            st.sent++;
            this.coverage.mark(pose.yaw, pose.pitch, { hfov: this.fov(), width: this.video.videoWidth || 540, height: this.video.videoHeight || 960 });
          } else this.policy.undo();
        } catch (e) {
          this.policy.undo();
          st.hint = `capture failed: ${e.message}`;
        } finally { this.busy = false; }
      }
    }
    if (this.coverage.fraction >= AUTO_FINISH) this.done();
    this._emit();
  }
}
