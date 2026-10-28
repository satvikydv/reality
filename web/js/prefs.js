// What the phone remembers between visits: whether the intro was seen, whether the permission primer was shown, and the
// settings. Storage can be missing or throw (private windows, blocked site data); then the values live in memory for the
// session, so the app still behaves (the intro shows once per visit) instead of crashing or asking again on every screen.

import { DEFAULT_MODE } from "./overlay-config.js";

export const KFPS = [1, 2, 3];       // keyframes per second at most; the scan also waits for the phone to turn and hold steady
export const LANGS = [["", "Phone default"], ["en-US", "English (US)"], ["en-GB", "English (UK)"], ["en-IN", "English (India)"],
  ["hi-IN", "हिन्दी"], ["es-ES", "Español"], ["fr-FR", "Français"], ["de-DE", "Deutsch"]];
export const DETAILS = [["calm", "Calm"], ["normal", "Normal"], ["debug", "Debug"]]; // how much the AR overlay shows (thresholds: overlay-config.js)
export const DEFAULTS = { onboarded: false, primed: false, kfps: 2, lang: "", ar: true, debug: true, detail: DEFAULT_MODE }; // debug: the HUD is on when the page is opened with ?debug, unless switched off
const BOOL = new Set(["onboarded", "primed", "ar", "debug"]);

export function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => void m.set(k, String(v)), removeItem: (k) => void m.delete(k) };
}

function pickStorage(win) {
  try {
    const s = win?.localStorage;
    if (!s) return memoryStorage();
    const probe = "ros.__probe";
    s.setItem(probe, "1");
    s.removeItem(probe);
    return s;
  } catch { return memoryStorage(); }
}

export class Prefs {
  constructor(storage = pickStorage(globalThis), prefix = "ros.") {
    this.prefix = prefix;
    this.storage = storage;
    this.fallback = memoryStorage(); // used when a write to the real storage fails
  }

  get(key) {
    let raw = null;
    try { raw = this.storage.getItem(this.prefix + key); } catch { /* unreadable: fall through */ }
    if (raw === null) raw = this.fallback.getItem(this.prefix + key);
    if (raw === null) return DEFAULTS[key];
    if (BOOL.has(key)) return raw === "1";
    if (key === "kfps") return KFPS.includes(+raw) ? +raw : DEFAULTS.kfps;
    if (key === "detail") return DETAILS.some(([v]) => v === raw) ? raw : DEFAULTS.detail;
    return raw;
  }

  set(key, value) {
    const raw = BOOL.has(key) ? (value ? "1" : "0") : String(value);
    this.fallback.setItem(this.prefix + key, raw);
    try { this.storage.setItem(this.prefix + key, raw); } catch { /* the in-memory copy still answers */ }
    return value;
  }

  get onboarded() { return this.get("onboarded"); }
  get primed() { return this.get("primed"); }

  // The BCP 47 tag used for speaking and for voice input.
  voiceLang(navigatorLang = globalThis.navigator?.language) { return this.get("lang") || navigatorLang || "en-US"; }

  // Interval between keyframes implied by the keyframes-per-second setting.
  minIntervalMs() { return Math.round(1000 / this.get("kfps")); }
}
