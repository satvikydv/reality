// Camera capture: getUserMedia -> downscaled JPEG blobs.
import { CAPTURE_REQUEST, CameraGuard } from "./camera-lock.js";

// Starts the rear camera in the fixed capture mode, pins zoom, and returns a guard that reports when the resolution,
// zoom or lens changes (see camera-lock.js): the field of view is only valid for the state locked here.
export async function startCamera(video, facing = "environment") {
  if (!window.isSecureContext) throw new Error("Insecure origin: camera needs HTTPS (or localhost)");
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("getUserMedia unavailable in this browser");
  if (video.srcObject) video.srcObject.getTracks().forEach((t) => t.stop());
  const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing }, ...CAPTURE_REQUEST }, audio: false });
  video.srcObject = stream;
  await video.play();
  const track = stream.getVideoTracks()[0];
  const guard = new CameraGuard(track, video);
  await guard.lock();
  return { stream, settings: track.getSettings(), guard };
}

const canvas = document.createElement("canvas");

// Draw the current video frame scaled so the long side is <= maxSide, return JPEG blob.
export function grabJpeg(video, maxSide, quality) {
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw) return Promise.resolve(null);
  const k = Math.min(1, maxSide / Math.max(vw, vh));
  canvas.width = Math.round(vw * k);
  canvas.height = Math.round(vh * k);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((res) => canvas.toBlob(res, "image/jpeg", quality));
}
