// The one hint shown while scanning, chosen from what the phone and the scan know right now. Pure: same inputs, same words.
// Order matters: things that stop frames from being useful come first, then posture, then what is left to cover.

export const LIMITS = { slow: 30, tiltUp: -28, tiltDown: 40, rollMax: 25 };

// i: {speed (deg/s or null), pitch, roll (deg or null), landscape (bool), fraction (0..1), gap ({dir, deg} or null),
//     policy (the keyframe policy's own hint text), link (hint text from the connection)}
export function coachHint(i) {
  const policy = i.policy || "";
  if (/sensor/i.test(policy)) return { text: "Waiting for the motion sensors", tone: "warn" };
  if (/link busy/i.test(policy)) return { text: "Hold on, catching up with the server", tone: "info" };
  if ((i.speed ?? 0) > LIMITS.slow || /slow down/i.test(policy)) return { text: "Rotate slower", tone: "warn" };
  if (/slower, then hold/i.test(policy)) return { text: "Slow down, then hold steady", tone: "warn" };
  if (i.landscape || Math.abs(i.roll ?? 0) > LIMITS.rollMax) return { text: "Hold the phone upright", tone: "warn" };
  if (i.pitch < LIMITS.tiltUp) return { text: "Tilt up a little", tone: "warn" };
  if (i.pitch > LIMITS.tiltDown) return { text: "Tilt down a little", tone: "warn" };
  if (/hold still/i.test(policy)) return { text: "Hold still for a moment to start", tone: "info" };
  const f = i.fraction ?? 0;
  if (i.gap && f > 0.5 && f < 0.97) return { text: `Area not covered yet: ${Math.round(i.gap.deg)}° to the ${i.gap.dir}`, tone: "info" };
  if (f < 0.05) return { text: "Turn slowly to the left", tone: "info" };
  if (f > 0.9) return { text: "Almost there", tone: "ok" };
  if (f > 0.6) return { text: "Hold steady, keep turning", tone: "info" };
  return { text: "Keep turning", tone: "info" };
}

// How sure the page can be that the room is lit well enough: mean brightness of a small sample, 0..255.
export const DARK_BELOW = 55;
export function lightVerdict(meanLuma) {
  if (meanLuma == null || Number.isNaN(meanLuma)) return { ok: null, text: "Checking the light…" };
  if (meanLuma < DARK_BELOW) return { ok: false, text: "It looks dark. Turn on a light so objects can be recognised." };
  return { ok: true, text: "The light looks fine." };
}

// Mean luma (Rec. 601) of RGBA pixel data, sampling every `step`-th pixel.
export function meanLuma(rgba, step = 4) {
  let sum = 0, n = 0;
  for (let p = 0; p + 2 < rgba.length; p += 4 * step) { sum += 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]; n++; }
  return n ? sum / n : null;
}

// Where a long wait for the server stands. The scan page shows `text` and, once `level` is "stalled" or "late", offers retry.
export const PROCESS_STALL_MS = 30000;  // no progress at all for this long
export const PROCESS_LATE_MS = 150000;  // the server gives up waiting for frames after 120 s, so by now something is wrong

// st: {sent, done, rejected}, elapsedMs: since Done was tapped, sinceProgressMs: since the last progress event.
export function processing(st, elapsedMs, sinceProgressMs) {
  const total = Math.max(0, st.sent || 0), finished = Math.min(total, (st.done || 0) + (st.rejected || 0));
  const fraction = total ? finished / total : 0;
  const base = { total, finished, fraction };
  if (elapsedMs > PROCESS_LATE_MS) return { ...base, level: "late", text: "This is taking much longer than it should." };
  if (sinceProgressMs > PROCESS_STALL_MS) return { ...base, level: "stalled", text: "No answer from the server for a while." };
  if (elapsedMs > 15000 && total && finished < total) return { ...base, level: "slow", text: "Still reading frames. This can take a minute." };
  return { ...base, level: "ok", text: total ? `Reading frames: ${finished} of ${total}` : "Starting…" };
}
