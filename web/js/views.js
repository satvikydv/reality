// DOM builders for the pieces screens are made of: room cards, thumbnails, change cards, history rows. They take plain data and
// return elements with classes and data-act attributes only (no inline style, no event handlers): the look is in app.css, and
// what a tap does is decided in app.js from data-act. Model-written text only ever goes in through textContent.
import { drawPhoto } from "./ui.js";
import { ago } from "./explain.js";

export function el(tag, cls, text, attrs) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  if (attrs) for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  return e;
}

export const since = (ts) => (ts ? ago(Math.max(0, Date.now() / 1000 - ts)) : "never");

// Pictures are cropped from the stored keyframe once the element is in the page (the canvas needs a size to draw into).
export function paintThumbs(root) {
  requestAnimationFrame(() => root.querySelectorAll("canvas[data-replay]").forEach((c) => { try { drawPhoto(c, JSON.parse(c.dataset.replay)); } catch { /* no picture: the placeholder stays */ } }));
}

export function thumb(o, { act = "open-object", caption = o.label } = {}) {
  const b = el("button", "thumb", null, { "data-act": act, "data-id": o.id, "data-label": o.label, "data-room": o.room?.id ?? o.room_id ?? "", "aria-label": `${o.label}${o.room ? " in " + o.room.name : ""}` });
  const c = el("canvas");
  if (o.replay) c.dataset.replay = JSON.stringify(o.replay);
  b.append(c, el("span", "cap", caption));
  return b;
}

export function roomCard(r) {
  const b = el("button", "rcard panel" + (r.active ? " active" : ""), null, { "data-act": "open-room", "data-id": r.id });
  const head = el("div", "rc-head");
  head.append(el("h3", null, r.name));
  if (r.active) head.append(el("span", "badge", "ACTIVE"));
  const meta = el("div", "rc-meta");
  if (r.scan_id) meta.append(el("span", null, `${r.n_objects} object${r.n_objects === 1 ? "" : "s"}`), el("span", null, `${r.coverage_pct}% covered`), el("span", null, since(r.last_scanned)));
  else meta.append(el("span", null, "Not scanned yet"));
  if (r.n_drafts) meta.append(el("span", "warn", "unsaved scan"));
  b.append(head, meta);
  return b;
}

export const roomChip = (r, selected, act = "pick-room") => el("button", "chip" + (selected ? " on" : ""), r.name, { "data-act": act, "data-id": r.id, "aria-pressed": String(!!selected) });

export function switcherRow(r) {
  const b = el("button", "srow2" + (r.active ? " active" : ""), null, { "data-act": "activate-room", "data-id": r.id, "aria-pressed": String(!!r.active) });
  const t = el("div");
  t.append(el("b", null, r.name), el("small", null, r.scan_id ? `${r.n_objects} objects · ${since(r.last_scanned)}` : "Not scanned yet"));
  b.append(el("i", "radio"), t);
  return b;
}

// Objects grouped by kind, biggest group first.
export function groupObjects(objects) {
  const m = new Map();
  for (const o of objects) m.set(o.label, [...(m.get(o.label) || []), o]);
  return [...m].map(([label, objs]) => ({ label, count: objs.length, objects: objs })).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

// The review's tiles: the biggest groups, then one tappable "Others" tile holding the rest.
export function summaryTiles(objects, max = 6) {
  const groups = groupObjects(objects);
  if (groups.length <= max) return { tiles: groups, others: null };
  const rest = groups.slice(max - 1);
  return { tiles: groups.slice(0, max - 1), others: { label: "Others", count: rest.reduce((n, g) => n + g.count, 0), groups: rest, objects: rest.flatMap((g) => g.objects) } };
}

export function groupTile(g, open) {
  const b = el("button", "gtile panel" + (open ? " on" : ""), null, { "data-act": "toggle-group", "data-label": g.label, "aria-expanded": String(!!open) });
  const first = g.objects[0];
  const c = el("canvas");
  if (first?.replay) c.dataset.replay = JSON.stringify(first.replay);
  b.append(c, el("b", null, g.label), el("span", "n", String(g.count)));
  return b;
}

export function historyRow(s, { memory = false } = {}) {
  const row = el("div", "hrow" + (s.status === "draft" ? " draft" : ""));
  const t = el("div", "ht");
  const when = new Date(s.started_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  const badges = el("span", "badges");
  if (s.status === "draft") badges.append(el("span", "badge warn", "UNSAVED"));
  if (memory) badges.append(el("span", "badge", "MEMORY"));
  if (s.baseline) badges.append(el("span", "badge", "BASELINE"));
  t.append(el("b", null, when), badges, el("small", null, `${s.n_objects} objects · ${s.n_frames} frames`));
  const acts = el("div", "hacts");
  const a = (label, act, cls) => acts.append(el("button", cls || "mini", label, { "data-act": act, "data-scan": s.id }));
  if (s.status === "draft") { a("Save", "save-scan"); a("Discard", "discard-scan", "mini danger"); }
  else {
    a(s.baseline ? "Clear baseline" : "Set baseline", s.baseline ? "clear-baseline" : "set-baseline");
    a("Delete", "delete-scan", "mini danger");
  }
  row.append(t, acts);
  return row;
}

// One change between two scans: before / after pictures, what happened in words (an angle, never a distance), confirm / dismiss.
export function changeCard(kind, e) {
  const card = el("div", "chgcard panel" + (e.review === "dismissed" ? " dismissed" : e.review === "confirmed" ? " confirmed" : ""), null, { "data-kind": kind });
  const pics = el("div", "pair" + (kind === "moved" ? "" : " one"));
  const slot = (label, rp) => { const s = el("div", "stripe"); if (rp) { const c = el("canvas"); c.dataset.replay = JSON.stringify(rp); s.append(c); } else s.append("—"); s.append(el("i", null, label)); return s; };
  if (kind === "moved") pics.append(slot("BEFORE", e.from?.replay), slot("AFTER", e.to?.replay));
  else pics.append(slot(kind === "removed" ? "BEFORE" : "AFTER", e.replay));
  const label = e.label || e.to?.label || "";
  const words = kind === "moved" ? `The ${label} moved ${e.shift?.words || ""}.` : e.speech;
  const acts = el("div", "hacts");
  for (const [state, text] of [["confirmed", "Confirm"], ["dismissed", "Dismiss"]])
    acts.append(el("button", "mini", text, { "data-act": "review", "data-key": e.key, "data-state": e.review === state ? "" : state, "aria-pressed": String(e.review === state) }));
  card.append(pics, el("p", null, words), acts);
  return card;
}

// One room's answer when searching all rooms. Read-only: opening it asks to switch to that room first.
export function findGroup(g) {
  const card = el("div", "fgroup panel" + (g.found ? " found" : ""));
  const head = el("div", "rc-head");
  head.append(el("h3", null, g.room_name));
  if (g.active) head.append(el("span", "badge", "ACTIVE"));
  card.append(head);
  if (g.found) {
    const b = el("button", "fopen", null, { "data-act": "open-group", "data-room": g.room_id });
    const c = el("canvas");
    if (g.replay) c.dataset.replay = JSON.stringify(g.replay);
    const t = el("div");
    t.append(el("b", null, g.object?.label || ""), el("span", null, g.speech));
    b.append(c, t);
    card.append(b);
  } else {
    const k = g.checked || {};
    card.append(el("p", "dim", k.scans ? `Not seen. The last scan covered ${Math.round((k.covered_deg || 0) / 3.6)}% of this room.` : "Not scanned yet."));
  }
  return card;
}
