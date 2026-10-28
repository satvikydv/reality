// Asking for the camera and the motion sensors, and turning a refusal into words a person can act on.
// Only called from a tap on the permission primer's button (never on page load).

// err: a DOMException from getUserMedia. Returns {kind, title, body, retry}: retry says whether asking again can help.
export function classifyCameraError(err) {
  const name = err?.name || "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return { kind: "denied", retry: true, title: "Camera access was blocked",
      body: "Reality OS needs the camera to see the room. Tap Try again and choose Allow. If the browser does not ask again, open the site settings (the lock or tune icon next to the address) and set Camera to Allow, then come back." };
  }
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") {
    return { kind: "nocamera", retry: true, title: "No camera found", body: "This device did not offer a camera. Try another phone or browser." };
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return { kind: "busy", retry: true, title: "The camera is busy", body: "Another app or tab is using the camera. Close it, then tap Try again." };
  }
  if (name === "SecurityError" || /insecure|https/i.test(err?.message || "")) {
    return { kind: "insecure", retry: false, title: "The camera needs a secure connection", body: "Open Reality OS over HTTPS (or on localhost), then try again." };
  }
  if (/unavailable|not supported/i.test(err?.message || "")) {
    return { kind: "unsupported", retry: false, title: "This browser cannot use the camera", body: "Try Chrome on Android or Safari on iPhone." };
  }
  return { kind: "other", retry: true, title: "The camera did not start", body: `${name || "Error"}: ${err?.message || "unknown problem"}. Tap Try again.` };
}

export const MOTION_DENIED = { kind: "motion", retry: true, title: "Motion access was blocked",
  body: "Motion sensors tell Reality OS which way you are facing, so scans cannot work without them. Tap Try again and choose Allow. On iPhone, if it does not ask again, quit the browser, reopen the page and try once more." };
export const MOTION_MISSING = { kind: "motion", retry: false, title: "No motion sensors found",
  body: "This device or browser does not report which way it is pointing, which scanning needs. Try a phone." };

async function askMotion(win) {
  const D = win.DeviceOrientationEvent;
  if (D && typeof D.requestPermission === "function") { // iOS: must be asked inside the tap, before anything is awaited
    try {
      return (await D.requestPermission()) === "granted" ? { ok: true, state: "granted" } : { ok: false, state: "denied", problem: MOTION_DENIED };
    } catch { return { ok: false, state: "denied", problem: MOTION_DENIED }; }
  }
  return D || win.AbsoluteOrientationSensor || win.RelativeOrientationSensor ? { ok: true, state: "not-needed" } : { ok: false, state: "unsupported", problem: MOTION_MISSING };
}

async function askCamera(nav, secure) {
  if (!secure) return { ok: false, state: "insecure", problem: classifyCameraError({ name: "SecurityError", message: "Insecure origin" }) };
  if (!nav.mediaDevices?.getUserMedia) return { ok: false, state: "unsupported", problem: classifyCameraError({ message: "getUserMedia unavailable" }) };
  try {
    const stream = await nav.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
    stream.getTracks().forEach((t) => t.stop()); // only asking; the real start happens when a screen needs the camera
    return { ok: true, state: "granted" };
  } catch (e) { return { ok: false, state: "denied", problem: classifyCameraError(e) }; }
}

// Returns {ok, camera, motion, problem}; problem is the first thing to fix, ready to show.
export async function requestAccess({ nav = globalThis.navigator, win = globalThis, secure = globalThis.isSecureContext } = {}) {
  const motion = await askMotion(win);
  const camera = await askCamera(nav, secure);
  return { ok: camera.ok && motion.ok, camera, motion, problem: camera.problem || motion.problem || null };
}
