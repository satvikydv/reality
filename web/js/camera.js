// Camera capture: getUserMedia -> downscaled JPEG blobs.
export async function startCamera(video, facing = "environment") {
  if (!window.isSecureContext) throw new Error("Insecure origin: camera needs HTTPS (or localhost)");
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("getUserMedia unavailable in this browser");
  if (video.srcObject) video.srcObject.getTracks().forEach((t) => t.stop());
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    audio: false,
  });
  video.srcObject = stream;
  await video.play();
  const s = stream.getVideoTracks()[0].getSettings();
  return { stream, settings: s };
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
