// Every call the phone makes to the server's HTTP API, in one place. Each returns fetchJson's result
// {ok, status, data, error, timedOut}: the screens turn that into loading / error / offline / slow states, never an exception.
import { fetchJson } from "./connection.js";

const q = (o) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
  return p.length ? `?${p.join("&")}` : "";
};

export function makeApi(call = fetchJson) {
  const get = (url, timeoutMs = 15000) => call(url, { timeoutMs });
  const send = (method, url, body, timeoutMs = 20000) => call(url, { method, body, timeoutMs });
  return {
    status: () => get("/api/status", 10000),
    rooms: () => get("/api/rooms"),
    room: (id) => get(`/api/rooms/${id}`),
    createRoom: (name, active = false) => send("POST", "/api/rooms", { name, active }),
    patchRoom: (id, patch) => send("PATCH", `/api/rooms/${id}`, patch),
    deleteRoom: (id) => send("DELETE", `/api/rooms/${id}`, undefined, 60000),
    roomScans: (id) => get(`/api/rooms/${id}/scans`),
    setBaseline: (id, scanId) => send("POST", `/api/rooms/${id}/baseline`, { scan_id: scanId }),
    saveScan: (id, scanId) => send("POST", `/api/rooms/${id}/scans/${scanId}/save`),
    discardScan: (id, scanId) => send("POST", `/api/rooms/${id}/scans/${scanId}/discard`),
    deleteScan: (id, scanId) => send("DELETE", `/api/rooms/${id}/scans/${scanId}`),
    changes: (id, before, after) => call(`/api/rooms/${id}/changes${q({ before, after })}`, { timeoutMs: 30000 }),
    review: (id, before, after, key, state) => send("POST", `/api/rooms/${id}/changes/review`, { before, after, key, state }),
    search: (text, roomId) => get(`/api/search${q({ q: text, room_id: roomId })}`, 10000),
    guide: (objectId, roomId) => send("POST", "/api/guide", { object_id: objectId, room_id: roomId }),
    overlay: (roomId, scanId) => get(`/api/overlay${q({ room_id: roomId, scan_id: scanId })}`, 10000),
    deleteAll: () => send("DELETE", "/api/data", undefined, 120000),
    // the objects review: what a scan believes, and corrections (each one is recorded by the server, source "phone")
    scanObjects: (scanId) => get(`/api/scans/${scanId}/objects`),
    duplicates: (scanId) => get(`/api/scans/${scanId}/duplicates`),
    object: (id) => get(`/api/objects/${id}`),
    mergeObjects: (ids) => send("POST", "/api/objects/merge", { ids, source: "phone" }),
    detachSighting: (id) => send("POST", `/api/sightings/${id}/detach?source=phone`),
    deleteObject: (id) => send("DELETE", `/api/objects/${id}?source=phone`),
    markNotObject: (roomId, label) => send("POST", `/api/rooms/${roomId}/not-objects`, { label, source: "phone" }),
    unmarkNotObject: (roomId, label) => send("DELETE", `/api/rooms/${roomId}/not-objects/${encodeURIComponent(label)}?source=phone`),
  };
}

// The words for a failed call, for the error state of a screen.
export function describeFailure(r, what = "do that") {
  if (r.timedOut) return { kind: "slow", text: `The server did not answer in time, so Reality OS could not ${what}. Try again in a moment.` };
  if (!r.status) return { kind: "offline", text: "The server is not reachable. Reality OS keeps trying to reconnect by itself." };
  const d = r.data?.detail || r.error;
  return { kind: "error", text: d ? String(d) : `The server answered with an error (${r.status}).` };
}
