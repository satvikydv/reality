// Is the server there, and does the model API accept the key? Polls /api/status, backs off while the server is away,
// and turns the answer into the words and the enabled buttons of the Home screen. Pure apart from the injected fetch/timers.

export const STATES = ["loading", "offline", "error", "ready"];

// What the Home screen shows for one poll result.
// r: null before the first answer, else {ok, status (the /api/status body), error, httpStatus}.
export function describe(r) {
  const off = { scan: false, find: false, changes: false };
  if (!r) return { state: "loading", tone: "wait", label: "Checking the server…", detail: "", api: null, can: off };
  if (!r.ok && !r.httpStatus) return { state: "offline", tone: "bad", label: "Server offline", detail: "Reconnecting automatically. Make sure the computer is on and the page is served.", api: null, can: off };
  if (!r.ok) return { state: "error", tone: "bad", label: "Server error", detail: `The server answered with an error (${r.httpStatus}). ${r.error || ""}`.trim(), api: null, can: off };
  const api = r.status.api || { state: "unreachable", message: "" };
  const keyBad = api.state === "missing" || api.state === "invalid";
  if (api.state === "checking") return { state: "ready", tone: "wait", label: "Connected · checking API key…", detail: "", api, can: { scan: true, find: true, changes: true } };
  const label = { ok: "Connected · API key valid", set: "Connected · API key set", missing: "API key missing", invalid: "API key rejected", unreachable: "Connected · model API not reachable" }[api.state] || "Connected";
  return { state: "ready", tone: keyBad ? "bad" : api.state === "unreachable" ? "warn" : "ok", label, detail: keyBad || api.state === "unreachable" ? api.message : "",
    api, can: { scan: !keyBad, find: !keyBad, changes: true } };
}

export class StatusPoller {
  constructor({ fetchFn = (...a) => fetch(...a), url = "/api/status", onUpdate = () => {}, intervalMs = 15000, minRetryMs = 2000, maxRetryMs = 10000,
    setTimer = (f, ms) => setTimeout(f, ms), clearTimer = (t) => clearTimeout(t) } = {}) {
    Object.assign(this, { fetchFn, url, onUpdate, intervalMs, minRetryMs, maxRetryMs, setTimer, clearTimer });
    this.timer = null;
    this.running = false;
    this.failures = 0;
    this.last = null;
    this._seq = 0;
  }

  start() { if (this.running) return; this.running = true; this.poll(); }
  stop() { this.running = false; this.clearTimer(this.timer); this.timer = null; this._seq++; }

  // The delay before the next poll: the normal interval when things work, 2 s, 4 s, 8 s, 10 s... while the server is away.
  nextDelay() {
    if (this.failures) return Math.min(this.maxRetryMs, this.minRetryMs * 2 ** (this.failures - 1));
    return this.last?.status?.api?.state === "checking" ? this.minRetryMs : this.intervalMs; // the server is still checking the key: ask again soon
  }

  retryNow({ refresh = false } = {}) { this.clearTimer(this.timer); return this.poll(refresh); }

  async poll(refresh = false) {
    const seq = ++this._seq;
    let r;
    try {
      const res = await this.fetchFn(refresh ? `${this.url}?refresh=true` : this.url);
      if (res.ok) r = { ok: true, status: await res.json() };
      else r = { ok: false, httpStatus: res.status, error: await res.text().then((t) => t.slice(0, 160)).catch(() => "") };
    } catch (e) { r = { ok: false, error: String(e?.message || e) }; } // no answer at all: the server is away
    if (seq !== this._seq || !this.running) return r; // stopped, or a newer poll took over
    this.failures = r.ok ? 0 : this.failures + 1;
    this.last = r;
    this.onUpdate(r, describe(r));
    this.clearTimer(this.timer);
    this.timer = this.setTimer(() => this.poll(), this.nextDelay());
    return r;
  }
}

// One request with a time limit, folded into {ok, status, data, error, timedOut}: the page shows a state for each outcome
// (server away, server error, too slow) instead of letting an exception or a spinner hang. Never throws.
export async function fetchJson(url, { method = "GET", body = undefined, timeoutMs = 30000, fetchFn = (...a) => fetch(...a), setTimer = (f, ms) => setTimeout(f, ms), clearTimer = (t) => clearTimeout(t) } = {}) {
  const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
  let timedOut = false;
  const t = setTimer(() => { timedOut = true; ctl?.abort(); }, timeoutMs);
  try {
    const init = { method, signal: ctl?.signal };
    if (body !== undefined) { init.body = JSON.stringify(body); init.headers = { "content-type": "application/json" }; }
    const res = await fetchFn(url, init);
    let data = null, text = "";
    try { text = await res.text(); data = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
    if (res.ok) return { ok: true, status: res.status, data };
    return { ok: false, status: res.status, data, error: (data?.detail || text || "").toString().slice(0, 200) };
  } catch (e) {
    return { ok: false, status: 0, data: null, timedOut, error: timedOut ? "no answer in time" : String(e?.message || e) };
  } finally { clearTimer(t); }
}
