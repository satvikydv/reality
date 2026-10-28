// How many labels and outlines the overlay draws on a stored scan, before (Normal, the old behaviour) and after (Calm), over a grid of
// viewpoints, using the same projection and selection code the phone runs.
//
//   python scripts/export_overlay.py --db data/world.sqlite --scan 4 --out overlay_scan4.json
//   node scripts/measure_overlay.mjs overlay_scan4.json
import { readFileSync } from "node:fs";
import { coverFit, projectObject } from "../web/js/project.js";
import { selectLabels } from "../web/js/calm.js";
import { OVERLAY } from "../web/js/overlay-config.js";

const file = process.argv[2];
if (!file) { console.error("usage: node scripts/measure_overlay.mjs overlay.json"); process.exit(1); }
const data = JSON.parse(readFileSync(file, "utf8")), objects = data.objects;

const SCREEN = { w: 390, h: 844, top: 120, bot: 200 };           // a portrait phone with the HUD bands kept free
const VIDEO = { width: 1080, height: 1920 };
const fit = coverFit(VIDEO.width, VIDEO.height, SCREEN.w, SCREEN.h);
const CHIP = OVERLAY.normal.chip;
const overlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const stat = (v) => { const s = [...v].sort((a, b) => a - b); return { mean: v.reduce((x, y) => x + y, 0) / v.length, p95: s[Math.floor(0.95 * (s.length - 1))], max: s[s.length - 1] }; };

const before = { labels: [], outlines: [], overlaps: [] }, after = { labels: [], outlines: [], overlaps: [], objects: [] };
const reasons = { offscreen: 0, background: 0, lowConfidence: 0, uncertain: 0, collision: 0, overflow: 0 };
let views = 0;
for (let pitch = -15; pitch <= 15; pitch += 15) for (let yaw = 0; yaw < 360; yaw += 10) {
  views++;
  const cur = { yaw, pitch, roll: 0, width: VIDEO.width, height: VIDEO.height, hfov: data.hfov_deg };
  // Normal: every reliable object that is on screen gets a label (placed under it, as before) and an outline
  const drawn = [];
  for (const o of objects) {
    const pr = projectObject(o, cur, fit);
    if (!pr) continue;
    const pts = pr.outline || (pr.anchor ? [pr.anchor] : []);
    if (!pts.length) continue;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const inView = Math.max(...xs) > 0 && Math.min(...xs) < SCREEN.w && Math.max(...ys) > SCREEN.top && Math.min(...ys) < SCREEN.h - SCREEN.bot;
    if (!inView) continue;
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, y = Math.min(Math.max(...ys) + 6, SCREEN.h - SCREEN.bot - 30);
    const w = CHIP.padX * 2 + o.label.length * CHIP.charWidth;
    drawn.push({ x: cx - w / 2, y: Math.max(SCREEN.top, y), w, h: CHIP.height, outline: !!pr.outline });
  }
  before.labels.push(drawn.length);
  before.outlines.push(drawn.filter((d) => d.outline).length);
  let ov = 0;
  for (let i = 0; i < drawn.length; i++) for (let j = i + 1; j < drawn.length; j++) if (overlap(drawn[i], drawn[j])) ov++;
  before.overlaps.push(ov);

  // Calm
  const items = objects.map((o) => { const pr = projectObject(o, cur, fit, { maxUncertainty: 999, outline: false }); return { o, at: pr && pr.anchor }; });
  const r = selectLabels(items, SCREEN, OVERLAY.calm);
  after.labels.push(r.labels.length);
  after.outlines.push(r.outlineId == null ? 0 : 1);
  after.objects.push(r.shown);
  let ov2 = 0;
  for (let i = 0; i < r.labels.length; i++) for (let j = i + 1; j < r.labels.length; j++) if (overlap(r.labels[i], r.labels[j])) ov2++;
  after.overlaps.push(ov2);
  for (const k of Object.keys(reasons)) reasons[k] += r.hidden[k];
}

const row = (name, b, a) => `| ${name} | ${b.mean.toFixed(1)} / ${b.p95} / ${b.max} | ${a.mean.toFixed(1)} / ${a.p95} / ${a.max} |`;
const labelsOf = (objs) => new Set(objs.map((o) => o.label)).size;
console.log(`Scan ${data.scan_id}: ${objects.length} objects, ${labelsOf(objects)} kinds, ${views} viewpoints (yaw every 10 degrees, pitch -15, 0, 15), FOV ${data.hfov_deg} degrees, screen ${SCREEN.w}x${SCREEN.h}.\n`);
console.log("| Per viewpoint (mean / p95 / max) | Before (Normal) | After (Calm) |\n|---|---|---|");
console.log(row("Labels drawn", stat(before.labels), stat(after.labels)));
console.log(row("Outlines drawn", stat(before.outlines), stat(after.outlines)));
console.log(row("Overlapping label pairs", stat(before.overlaps), stat(after.overlaps)));
console.log(`| Objects a label stands for (mean) | ${stat(before.labels).mean.toFixed(1)} | ${stat(after.objects).mean.toFixed(1)} |`);
console.log(`\nViewpoints with more than 5 labels: before ${before.labels.filter((n) => n > 5).length} of ${views}, after ${after.labels.filter((n) => n > 5).length}.`);
console.log(`Viewpoints with an overlap: before ${before.overlaps.filter((n) => n > 0).length} of ${views}, after ${after.overlaps.filter((n) => n > 0).length}.`);
console.log(`Calm left out, summed over all viewpoints: ${Object.entries(reasons).map(([k, v]) => `${k} ${v}`).join(", ")}.`);
const byLabel = {};
for (const o of objects) byLabel[o.label] = (byLabel[o.label] || 0) + 1;
console.log(`Objects per label in the scan: ${Object.entries(byLabel).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(", ")}.`);
