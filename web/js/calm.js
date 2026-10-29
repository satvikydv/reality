// The calm overlay's choices, as pure functions: which objects deserve a label, how duplicates are grouped, where the labels go so they do
// not collide, and which single object gets an outline. Thresholds are in overlay-config.js; drawing is in ar.js.
//
// items: [{o, at}] where o is an object from /api/overlay ({id, label, confidence, uncertainty_deg, age_s, hl}) and `at` is where it
// is on screen ([x, y], or null when it is not in view). view: {w, h, top, bot}: the screen and the bands the HUD keeps free.

const IRREGULAR = { mouse: "mice", knife: "knives", shelf: "shelves", glass: "glasses", box: "boxes", leaf: "leaves" };

export function plural(label) {
  const i = label.lastIndexOf(" "), head = i < 0 ? "" : label.slice(0, i + 1), last = label.slice(i + 1);
  if (IRREGULAR[last]) return head + IRREGULAR[last];
  if (/[^aeiou]y$/.test(last)) return head + last.slice(0, -1) + "ies";
  if (/(s|x|ch|sh)$/.test(last)) return head + last + (last.endsWith("s") && !last.endsWith("ss") ? "" : "es");
  return head + last + "s";
}

export const norm = (l) => String(l || "").trim().toLowerCase();

// confidence x centrality x recency, each in 0..1
export function labelScore(o, at, view, cfg) {
  const cx = view.w / 2, cy = (view.top + (view.h - view.bot)) / 2, half = Math.hypot(view.w / 2, (view.h - view.bot - view.top) / 2) || 1;
  const centrality = Math.max(cfg.centralityFloor, 1 - Math.hypot(at[0] - cx, at[1] - cy) / half);
  const recency = Math.max(cfg.recencyFloor, Math.pow(2, -(o.age_s ?? 0) / cfg.recencyHalfLifeS));
  return (o.confidence ?? 0) * centrality * recency;
}

// Why an object is not labelled, or null when it may be.
export function hiddenReason(o, at, view, cfg) {
  if (!at || at[0] < 0 || at[0] > view.w || at[1] < view.top || at[1] > view.h - view.bot) return "offscreen";
  if (cfg.backgroundLabels.includes(norm(o.label))) return "background";
  if ((o.confidence ?? 0) < cfg.minConfidence) return "lowConfidence";
  if ((o.uncertainty_deg ?? 0) > cfg.maxUncertaintyDeg) return "uncertain";
  return null;
}

const overlaps = (a, b, m = 2) => a.x < b.x + b.w + m && b.x < a.x + a.w + m && a.y < b.y + b.h + m && b.y < a.y + a.h + m;

// Where a label goes: its preferred spot just below the object, else shifted down and up in turn until it clears the labels already
// placed and stays on screen. null when there is no room, in which case the label is left out rather than drawn on top of another.
export function placeLabel(w, at, placed, view, cfg) {
  const h = cfg.chip.height, step = h + cfg.chip.gap;
  const clampX = (x) => Math.max(4, Math.min(view.w - w - 4, x));
  const tries = [0];
  for (let k = 1; k <= cfg.chip.maxShifts; k++) tries.push(k * step, -k * step);
  for (const dy of tries) {
    const r = { x: clampX(at[0] - w / 2), y: at[1] + 18 + dy, w, h };
    if (r.y < view.top || r.y + h > view.h - view.bot) continue;
    if (!placed.some((p) => overlaps(r, p))) return r;
  }
  return null;
}

export function selectLabels(items, view, cfg) {
  const hidden = { offscreen: 0, background: 0, lowConfidence: 0, uncertain: 0, collision: 0, overflow: 0 };
  const cx = view.w / 2, cy = (view.top + (view.h - view.bot)) / 2;
  const target = items.find((i) => i.o.hl && i.at && hiddenReason({ ...i.o, confidence: 1, uncertainty_deg: 0, label: "" }, i.at, view, cfg) === null) || null;
  const ok = [];
  for (const it of items) {
    const why = it === target ? null : hiddenReason(it.o, it.at, view, cfg);
    if (why) { hidden[why]++; continue; }
    ok.push({ ...it, score: it === target ? Infinity : labelScore(it.o, it.at, view, cfg), target: it === target });
  }

  // duplicates: same label, close on screen, become one label with a count
  const parent = ok.map((_, i) => i), root = (i) => (parent[i] === i ? i : (parent[i] = root(parent[i])));
  for (let i = 0; i < ok.length; i++) for (let j = i + 1; j < ok.length; j++) {
    if (norm(ok[i].o.label) !== norm(ok[j].o.label) || ok[i].target || ok[j].target) continue;
    if (Math.hypot(ok[i].at[0] - ok[j].at[0], ok[i].at[1] - ok[j].at[1]) <= cfg.groupRadiusPx) parent[root(j)] = root(i);
  }
  const by = new Map();
  ok.forEach((it, i) => by.set(root(i), [...(by.get(root(i)) || []), it]));
  const groups = [...by.values()].map((m) => {
    const best = m.reduce((a, b) => (b.score > a.score ? b : a));
    const sw = m.reduce((s, x) => s + (isFinite(x.score) ? x.score : 1), 0) || 1;
    const at = m.length === 1 ? best.at : [m.reduce((s, x) => s + x.at[0] * (isFinite(x.score) ? x.score : 1), 0) / sw, m.reduce((s, x) => s + x.at[1] * (isFinite(x.score) ? x.score : 1), 0) / sw];
    const label = norm(best.o.label);
    return { key: `${label}:${best.o.id}`, label, text: m.length > 1 ? `${m.length} ${plural(label)}` : label, count: m.length, at, score: best.score, ids: m.map((x) => x.o.id), target: m.some((x) => x.target), best };
  }).sort((a, b) => (b.score - a.score) || (a.at[0] - b.at[0]) || (a.key < b.key ? -1 : 1));

  const labels = [], placed = [];
  for (const g of groups) {
    if (labels.length >= cfg.maxLabels && !g.target) { hidden.overflow++; continue; }
    const w = cfg.chip.padX * 2 + g.text.length * cfg.chip.charWidth;
    const r = placeLabel(w, g.at, placed, view, cfg);
    if (!r) { hidden.collision++; continue; }
    placed.push(r);
    labels.push({ ...r, text: g.text, label: g.label, count: g.count, ids: g.ids, score: g.score, target: g.target, at: g.at });
  }

  // one outline: the Find / Guide target, else the object under the reticle
  let outlineId = null;
  if (target) outlineId = target.o.id;
  else if (cfg.outlines === "one") {
    let bestD = cfg.reticleRadiusPx;
    for (const it of ok) { const d = Math.hypot(it.at[0] - cx, it.at[1] - cy); if (d < bestD) { bestD = d; outlineId = it.o.id; } }
  }
  return { labels, outlineId, hidden, candidates: items.length, shown: labels.reduce((n, l) => n + l.count, 0) };
}
