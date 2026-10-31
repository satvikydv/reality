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
  if (r.scan_id) { const bar = el("div", "bar"), fill = el("i"); fill.style.width = `${Math.max(0, Math.min(100, r.coverage_pct))}%`; bar.append(fill); b.append(bar); } // style set by the DOM, never from text
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

// The objects review: a label, an instance with a pick box, a sighting.
export function labelRow(g) {
  const b = el("button", "lrow panel", null, { "data-act": "open-label", "data-label": g.label });
  b.append(el("b", null, g.label), el("small", null, `${g.instances} × · ${g.sightings} sightings · ${Math.round(g.avg_confidence * 100)}%`));
  return b;
}

export function instanceRow(o, picked) {
  const row = el("div", "irow panel");
  const pick = el("button", "pick", picked ? "✓" : "", { "data-act": "pick-instance", "data-id": o.id, "aria-pressed": String(!!picked), "aria-label": `Select ${o.label} ${o.id} to merge` });
  const open = el("button", "irow-open", null, { "data-act": "open-instance", "data-id": o.id });
  open.style.cssText = "display:flex;align-items:center;gap:12px;flex:1;min-width:0;text-align:left;color:inherit";
  const c = el("canvas");
  if (o.thumb) c.dataset.replay = JSON.stringify(o.thumb);
  const t = el("div", "t");
  t.append(el("b", null, `#${o.id} · ${o.sightings} sighting${o.sightings === 1 ? "" : "s"}`), el("small", null, `confidence ${Math.round(o.avg_confidence * 100)}% · ${Math.round(o.yaw)}° / ${Math.round(o.pitch)}°`));
  open.append(c, t);
  row.append(pick, open);
  return row;
}

export function sightingCard(s, canDetach) {
  const card = el("div", "srow3 panel");
  const c = el("canvas");
  c.dataset.replay = JSON.stringify({ frame_id: s.frame_id, box: s.box, width: s.width, height: s.height, image_url: s.image_url });
  const m = el("div", "m");
  m.append(el("b", null, `Sighting #${s.id}`), ` · SAM confidence `, el("b", null, `${Math.round(s.confidence * 100)}%`), document.createElement("br"),
    `camera ${Math.round(s.camera.yaw)}° / ${Math.round(s.camera.pitch)}° · object ${Math.round(s.anchor.yaw)}° / ${Math.round(s.anchor.pitch)}°${s.fragment ? " · edge-clipped" : ""}`);
  const b = el("button", "mini", "Detach: make it its own object", { "data-act": "detach-sighting", "data-id": s.id });
  b.disabled = !canDetach;
  card.append(c, m, b);
  return card;
}

// A note left in the room: its text, who left it, when, what it is on; Done and Delete.
export function noteRow(n) {
  const row = el("div", "nrow"), t = el("div", "nt");
  t.append(el("b", null, n.text), el("small", null, [n.author, n.label && `on ${n.label}`, since(n.created_at)].filter(Boolean).join(" · ")));
  const acts = el("div", "hacts");
  acts.append(el("button", "mini", "Done", { "data-act": "note-done", "data-id": n.id }), el("button", "mini danger", "Delete", { "data-act": "note-delete", "data-id": n.id }));
  row.append(t, acts);
  return row;
}

export const whenText = (ts) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

// An earlier saved scan to rewind to.
export function thenRow(s) {
  const b = el("button", "srow2", null, { "data-act": "pick-then", "data-scan": s.id }), t = el("div");
  t.append(el("b", null, whenText(s.started_at)), el("small", null, `${s.n_objects} objects · ${s.n_frames} frames${s.baseline ? " · baseline" : ""}`));
  b.append(el("i", "radio"), t);
  return b;
}

export function historyRow(s, { memory = false } = {}) {
  const row = el("div", "hrow" + (s.status === "draft" ? " draft" : ""));
  const t = el("div", "ht");
  const when = new Date(s.started_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  const badges = el("span", "badges");
  if (s.status === "draft") badges.append(el("span", "badge warn", "UNSAVED"));
  if (memory) badges.append(el("span", "badge memory", "MEMORY"));
  if (s.baseline) badges.append(el("span", "badge baseline", "BASELINE"));
  t.append(el("b", null, when), badges, el("small", null, `${s.n_objects} objects · ${s.n_frames} frames`));
  const acts = el("div", "hacts");
  const a = (label, act, cls) => acts.append(el("button", cls || "mini", label, { "data-act": act, "data-scan": s.id }));
  a("Objects", "inspect-scan");
  if (s.status === "draft") { a("Save", "save-scan"); a("Discard", "discard-scan", "mini danger"); }
  else {
    a(s.baseline ? "Clear baseline" : "Set baseline", s.baseline ? "clear-baseline" : "set-baseline");
    a("Delete", "delete-scan", "mini danger");
  }
  row.append(t, acts);
  return row;
}

// One change between two scans: before / after pictures, what happened in words (an angle, never a distance), confirm / dismiss.
// How well the two scans lined up, in words: the reason nothing may be called moved when it is not good.
export function alignWords(al) {
  const q = al.quality || "failed";
  if (q === "failed") return "The pictures of the two scans could not be matched, so nothing is called moved, new or removed.";
  if (q === "weak") return `The scans lined up only roughly (${al.inliers} matching points from ${al.pairs} frame pair${al.pairs === 1 ? "" : "s"}), so nothing is called moved, new or removed`
    + (al.low_confidence_enabled ? ", except a move far beyond what standing somewhere else explains, marked low confidence." : ".");
  const off = Math.abs(Math.round((al.offset_used_deg ?? al.offset_deg ?? 0) * 10) / 10);
  const vp = Math.round(al.viewpoint_cm_used ?? al.viewpoint_cm ?? 15);
  return `${al.inliers} matching points from ${al.pairs} frame pairs. The second scan's start heading differs by ${off}° (±${al.offset_sem_deg ?? al.spread_deg ?? 0}°). A move counts only beyond what ${vp} cm of viewpoint change could explain`
    + (al.viewpoint_cm_measured && vp > (al.viewpoint_cm ?? 15) ? " (measured from the objects that stayed put)." : ".");
}

export function alignCard(al) {
  const q = al.quality || "failed", card = el("div", "grp panel align " + q), h = el("h2");
  h.append(el("span", "ic", q === "good" ? "✓" : q === "weak" ? "~" : "!"), `Alignment: ${q}`);
  card.append(h, el("p", "dim", alignWords(al)));
  return card;
}

export function changeCard(kind, e) {
  const two = kind === "moved" || kind === "possibly_moved" || kind === "moved_low_confidence";
  const card = el("div", "chgcard panel" + (e.review === "dismissed" ? " dismissed" : e.review === "confirmed" ? " confirmed" : ""), null, { "data-kind": kind });
  const pics = el("div", "pair" + (two ? "" : " one"));
  const slot = (label, rp) => { const s = el("div", "stripe"); if (rp) { const c = el("canvas"); c.dataset.replay = JSON.stringify(rp); s.append(c); } else s.append("—"); s.append(el("i", null, label)); return s; };
  if (two) pics.append(slot("BEFORE", e.from?.replay), slot("AFTER", e.to?.replay));
  else pics.append(slot(kind === "removed" ? "BEFORE" : "AFTER", e.replay));
  const label = e.label || e.to?.label || "";
  const words = kind === "moved" ? `The ${label} moved ${e.shift?.words || ""}.`
    : kind === "moved_low_confidence" ? `The ${label} seems to have moved ${e.shift?.words || ""}, far more than standing somewhere else explains. The two scans lined up only roughly, so check it.`
    : kind === "possibly_moved" ? `The ${label} may have moved ${e.shift?.words || ""}. Standing in a slightly different spot could explain that (it allows ${e.tolerance_deg}° for this object).` : e.speech;
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
