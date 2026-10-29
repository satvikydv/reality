// Objects inspector (laptop dashboard): what the memory believes about one scan, with large pictures, the decisions behind each object,
// and the tools to correct it: merge two instances, detach a sighting, delete an object, mark a label "not an object" for the room.
// Every correction is recorded by the server (/api/corrections) and kept by every later rebuild. All text goes in as textContent.
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const SOURCE = "dashboard";

async function j(url, opt) {
  const r = await fetch(url, opt);
  let d = null;
  try { d = await r.json(); } catch { /* no body */ }
  if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
  return d;
}
const post = (url, body) => j(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, source: SOURCE }) });
const del = (url) => j(url + (url.includes("?") ? "&" : "?") + `source=${SOURCE}`, { method: "DELETE" });

const S = { rooms: [], roomId: null, scans: [], scanId: null, data: null, label: null, objectId: null, picked: new Set(), view: "object" };
const images = new Map();

function image(url) {
  let im = images.get(url);
  if (!im) { im = new Image(); im.src = url; images.set(url, im); }
  return im;
}

// A sighting: the stored frame with the mask outline and the box drawn on it. `crop` shows just the object, for thumbnails.
export function drawSighting(canvas, s, { crop = false, maxW = 900 } = {}) {
  const im = image(s.image_url || `/world/frame/${s.frame_id}.jpg`);
  const paint = () => {
    const nat = im.naturalWidth ? [im.naturalWidth, im.naturalHeight] : [s.width, s.height];
    const [bx0, by0, bx1, by1] = s.box;
    let sx = 0, sy = 0, sw = nat[0], sh = nat[1];
    if (crop) { const px = (bx1 - bx0) * 0.35, py = (by1 - by0) * 0.35; sx = Math.max(0, bx0 - px); sy = Math.max(0, by0 - py); sw = Math.min(nat[0] - sx, bx1 - bx0 + 2 * px); sh = Math.min(nat[1] - sy, by1 - by0 + 2 * py); }
    const k = Math.min(1, maxW / sw);
    canvas.width = Math.round(sw * k); canvas.height = Math.round(sh * k);
    const x = canvas.getContext("2d");
    if (im.naturalWidth) x.drawImage(im, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    else { x.fillStyle = "#14171a"; x.fillRect(0, 0, canvas.width, canvas.height); }
    const X = (v) => (v - sx) * k, Y = (v) => (v - sy) * k;
    if (s.mask && s.mask.length > 2) {
      x.beginPath(); s.mask.forEach(([a, b], i) => (i ? x.lineTo(X(a), Y(b)) : x.moveTo(X(a), Y(b)))); x.closePath();
      x.fillStyle = "rgba(255,213,79,.28)"; x.fill(); x.lineWidth = crop ? 2 : 3; x.strokeStyle = "#FFD54F"; x.stroke();
    }
    x.setLineDash([8, 6]); x.lineWidth = crop ? 1 : 2; x.strokeStyle = "rgba(255,255,255,.85)"; x.strokeRect(X(bx0), Y(by0), (bx1 - bx0) * k, (by1 - by0) * k); x.setLineDash([]);
  };
  im.complete ? paint() : im.addEventListener("load", paint, { once: true });
  paint();
}

const rep = (thumb) => thumb && { image_url: thumb.image_url, frame_id: thumb.frame_id, box: thumb.box, width: thumb.width, height: thumb.height, mask: null };

function toast(msg) { $("imsg").textContent = msg; }  // the last thing done or refused; #isum keeps the counts

async function loadRooms() {
  const r = await j("/api/rooms");
  S.rooms = r.rooms;
  const sel = $("irooms");
  sel.replaceChildren(...S.rooms.map((x) => new Option(x.name + (x.active ? " (active)" : ""), x.id)));
  if (!S.rooms.length) { $("ilabels").replaceChildren(el("p", "empty", "No rooms yet. Scan a room first.")); return; }
  S.roomId = S.rooms.some((x) => x.id === S.roomId) ? S.roomId : (S.rooms.find((x) => x.active) || S.rooms[0]).id;
  sel.value = String(S.roomId);
  await loadScans();
}

async function loadScans() {
  const r = await j(`/api/rooms/${S.roomId}/scans`);
  S.scans = r.scans;
  const room = S.rooms.find((x) => x.id === S.roomId);
  const sel = $("iscans");
  sel.replaceChildren(...S.scans.map((s) => new Option(`#${s.id} · ${new Date(s.started_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })} · ${s.n_objects} objects · ${s.status}${s.id === room?.scan_id ? " · memory" : ""}`, s.id)));
  S.scanId = S.scans.some((s) => s.id === S.scanId) ? S.scanId : (room?.scan_id || S.scans[0]?.id || null);
  if (S.scanId == null) { S.data = null; $("ilabels").replaceChildren(el("p", "empty", "This room has no scans.")); $("iinst").replaceChildren(); $("idetail").replaceChildren(); $("idups").textContent = "Possible duplicates (0)"; return; }
  sel.value = String(S.scanId);
  await loadScan();
}

async function loadScan(keep = true) {
  S.data = await j(`/api/scans/${S.scanId}/objects`);
  $("idups").textContent = `Possible duplicates (${S.data.possible_duplicates})`;
  $("isum").textContent = `${S.data.instances} objects, ${S.data.sightings} sightings`;
  if (!keep || !S.data.labels.some((g) => g.label === S.label)) S.label = S.data.labels[0]?.label ?? null;
  S.picked = new Set([...S.picked].filter((id) => S.data.labels.some((g) => g.objects.some((o) => o.id === id))));
  renderLabels();
  renderInstances();
  if (S.view === "dups") return showDuplicates();
  if (S.view === "log") return showLog();
  const g = S.data.labels.find((x) => x.label === S.label);
  if (!g) { $("idetail").replaceChildren(el("p", "empty", "Nothing to show in this scan.")); return; }
  if (!g.objects.some((o) => o.id === S.objectId)) S.objectId = g.objects[0].id;
  await showObject(S.objectId);
}

function renderLabels() {
  const box = $("ilabels"), kids = [el("h2", null, "Labels")];
  for (const g of S.data.labels) {
    const r = el("div", "row" + (g.label === S.label ? " on" : ""));
    r.append(el("b", null, g.label), el("small", null, `${g.instances} × · ${g.sightings} sightings · ${Math.round(g.avg_confidence * 100)}%`));
    r.onclick = () => { S.label = g.label; S.objectId = g.objects[0].id; S.view = "object"; renderLabels(); renderInstances(); showObject(S.objectId); };
    kids.push(r);
  }
  if (S.data.hidden_labels.length) {
    kids.push(el("h2", null, "Not an object here"));
    for (const h of S.data.hidden_labels) {
      const r = el("div", "row");
      const b = el("button", "small", "Restore");
      b.onclick = async () => { await del(`/api/rooms/${S.roomId}/not-objects/${encodeURIComponent(h.label)}`); toast(`Restored “${h.label}”`); await loadScan(); };
      r.append(el("b", null, h.label), el("small", null, `${h.sightings} sightings hidden`), b);
      kids.push(r);
    }
  }
  box.replaceChildren(...kids);
}

function renderInstances() {
  const box = $("iinst"), g = S.data.labels.find((x) => x.label === S.label);
  if (!g) { box.replaceChildren(el("h2", null, "Instances"), el("p", "empty", "No labels.")); return; }
  const head = el("div", "ih");
  const notObj = el("button", "small danger", `“${g.label}” is not an object here`);
  notObj.onclick = async () => {
    if (!confirm(`Hide every “${g.label}” in this room, in all its scans, now and in future scans? You can restore it from this list.`)) return;
    const r = await post(`/api/rooms/${S.roomId}/not-objects`, { label: g.label });
    toast(`Hidden “${g.label}” (${r.objects_hidden} objects)`);
    S.label = null; S.objectId = null;
    await loadScan(false);
  };
  const merge = el("button", "small go", `Merge selected (${S.picked.size})`);
  merge.disabled = S.picked.size < 2;
  merge.onclick = () => doMerge([...S.picked]);
  head.append(merge, notObj);
  const kids = [el("h2", null, `${g.label}: ${g.instances} instance${g.instances === 1 ? "" : "s"}`), head];
  for (const o of g.objects) {
    const r = el("div", "row" + (o.id === S.objectId ? " on" : ""));
    const cb = el("input"); cb.type = "checkbox"; cb.checked = S.picked.has(o.id); cb.title = "select to merge";
    cb.onclick = (e) => { e.stopPropagation(); cb.checked ? S.picked.add(o.id) : S.picked.delete(o.id); renderInstances(); };
    const cv = el("canvas"); if (o.thumb) drawSighting(cv, { ...rep(o.thumb) }, { crop: true, maxW: 128 });
    const t = el("div"); t.style.cssText = "flex:1;min-width:0;display:flex;flex-direction:column;gap:3px";
    t.append(el("b", null, `#${o.id} · ${o.sightings} sighting${o.sightings === 1 ? "" : "s"}`), el("small", null, `conf ${Math.round(o.avg_confidence * 100)}% · ${o.yaw}° / ${o.pitch}° ± ${o.uncertainty_deg}°`));
    r.append(cb, cv, t);
    r.onclick = () => { S.objectId = o.id; S.view = "object"; renderInstances(); showObject(o.id); };
    kids.push(r);
  }
  box.replaceChildren(...kids);
}

async function doMerge(ids) {
  try {
    const r = await post("/api/objects/merge", { ids });
    S.picked = new Set(); S.objectId = r.merged_into; S.view = "object";
    toast(`Merged into #${r.merged_into}`);
    await loadScan();
  } catch (e) { toast("Cannot merge: " + e.message); }
}

async function showObject(id) {
  S.view = "object";
  const d = await j(`/api/objects/${id}`);
  const box = $("idetail"), o = d.object;
  const head = el("div", "dh");
  const delb = el("button", "small danger", "Delete this object");
  delb.onclick = async () => {
    if (!confirm(`Delete object #${o.id} (${o.label}) with its ${d.sightings.length} sighting(s)? They are ignored from now on.`)) return;
    await del(`/api/objects/${o.id}`); S.objectId = null; toast(`Deleted #${o.id}`); await loadScan();
  };
  head.append(el("h3", null, `${o.label} #${o.id}`), delb);
  const facts = el("div", "facts");
  for (const [k, v] of [["direction", `${o.yaw}° / ${o.pitch}°`], ["uncertainty", `±${o.uncertainty_deg}° (spread ${d.spread_deg}°, FOV ${d.fov_error_deg}°)`], ["sightings", d.sightings.length], ["mean confidence", Math.round(o.avg_confidence * 100) + "%"], ["scan", `#${d.scan.id} ${d.scan.status} · ${d.scan.room_name}`]]) {
    const s = el("span"); s.append(k + " ", el("b", null, String(v))); facts.append(s);
  }
  const grid = el("div", "sights");
  d.sightings.forEach((s, i) => {
    const c = el("div", "sight"), cv = el("canvas");
    drawSighting(cv, s);
    const m = el("div", "meta");
    m.append(el("b", null, `Sighting #${s.id}`), ` · frame ${s.frame_id}${s.fragment ? " · edge-clipped" : ""}`, document.createElement("br"),
      `SAM confidence `, el("b", null, `${Math.round(s.confidence * 100)}%`), ` · anchor ${s.anchor.yaw}° / ${s.anchor.pitch}°`, document.createElement("br"),
      `camera ${s.camera.yaw}° / ${s.camera.pitch}°, roll ${Math.round(s.camera.roll ?? 0)}°, FOV ${s.camera.hfov_deg}° · box ${s.box.map((v) => Math.round(v)).join(", ")} · mask ${s.mask ? s.mask.length + " pts" : "none"}`);
    const b = el("button", "small", "Detach: make it its own object");
    b.disabled = d.sightings.length < 2;
    b.onclick = async () => { const r = await post(`/api/sightings/${s.id}/detach`, {}); S.objectId = r.object_id; toast(`Detached into #${r.object_id}`); await loadScan(); };
    c.append(cv, m, b);
    grid.append(c);
  });
  const dec = el("div", "dec");
  if (!d.decisions.length) dec.append(el("p", "hide", "No decisions recorded for this object."));
  for (const x of d.decisions) {
    const row = el("div", /manual|detached/.test(x.action) ? "manual" : /veto/.test(x.action) ? "veto" : "");
    row.append(x.text, el("code", null, JSON.stringify(Object.fromEntries(Object.entries(x).filter(([k]) => !["text", "scan"].includes(k))))));
    dec.append(row);
  }
  const corr = el("div", "dec");
  for (const c of d.corrections) corr.append(el("div", "manual", `${new Date(c.ts * 1000).toLocaleString()} · ${c.kind} by ${c.source}`));
  box.replaceChildren(head, facts, el("h2", null, "Sightings"), grid,
    el("h2", null, `Decision log (${d.decisions_source === "log" ? "as the server took them" : "recomputed from the detections"})`), dec,
    ...(d.corrections.length ? [el("h2", null, "Manual corrections"), corr] : []));
}

async function showDuplicates() {
  S.view = "dups";
  const r = await j(`/api/scans/${S.scanId}/duplicates`), box = $("idetail");
  const kids = [el("h2", null, `Possible duplicates (${r.duplicates.length})`), el("p", "hide", "Same label, close in direction, never seen in one frame. Merging is a correction: it is recorded and kept.")];
  if (!r.duplicates.length) kids.push(el("p", "empty", "None found."));
  for (const d of r.duplicates) {
    const row = el("div", "dup");
    for (const t of d.thumbs) { const cv = el("canvas"); if (t) drawSighting(cv, rep(t), { crop: true, maxW: 300 }); row.append(cv); }
    const info = el("div"); info.append(el("b", null, `${d.label}: #${d.a} and #${d.b}`), document.createElement("br"), el("span", "hide", `${d.reason} · score ${d.score}`));
    const b = el("button", "small go", "Merge"); b.onclick = () => doMerge([d.a, d.b]);
    const o = el("button", "small", "Open #" + d.a); o.onclick = () => { S.label = d.label; S.objectId = d.a; S.view = "object"; renderLabels(); renderInstances(); showObject(d.a); };
    const col = el("div"); col.style.cssText = "display:flex;flex-direction:column;gap:6px"; col.append(b, o);
    row.append(info, col);
    kids.push(row);
  }
  box.replaceChildren(...kids);
}

async function showLog() {
  S.view = "log";
  const r = await j(`/api/corrections?scan_id=${S.scanId}`), room = await j(`/api/corrections?room_id=${S.roomId}`);
  const rows = [...new Map([...r.corrections, ...room.corrections].map((c) => [c.id, c])).values()].sort((a, b) => b.id - a.id);
  const kids = [el("h2", null, `Corrections log (${rows.length})`), el("p", "hide", "Every manual correction, newest first, with who made it.")];
  if (!rows.length) kids.push(el("p", "empty", "No corrections yet."));
  for (const c of rows) {
    const row = el("div", "corr");
    const p = c.payload;
    const what = c.kind === "merge" ? `objects ${p.objects.join(" + ")} (${p.label}), ${p.dets.length} sightings` : c.kind === "detach" ? `sighting ${p.det} out of object ${p.from_object} (${p.label})`
      : c.kind === "delete" ? `object ${p.object} (${p.label}), ${p.dets.length} sightings` : `label “${p.label}”${p.objects_hidden != null ? `, ${p.objects_hidden} objects hidden` : ""}`;
    row.append(el("span", null, new Date(c.ts * 1000).toLocaleString()), el("b", null, c.kind), el("span", null, `${what} · by ${c.source}${c.scan_id ? " · scan " + c.scan_id : ""}`));
    kids.push(row);
  }
  $("idetail").replaceChildren(...kids);
}

function open() { $("insp").classList.add("on"); loadRooms().catch((e) => toast("Cannot load: " + e.message)); }
function close() { $("insp").classList.remove("on"); }

$("objbtn").onclick = open;
$("iclose").onclick = close;
$("irooms").onchange = (e) => { S.roomId = +e.target.value; S.scanId = null; S.label = null; S.objectId = null; S.picked = new Set(); S.view = "object"; loadScans().catch((x) => toast(x.message)); };
$("iscans").onchange = (e) => { S.scanId = +e.target.value; S.label = null; S.objectId = null; S.picked = new Set(); S.view = "object"; loadScan(false).catch((x) => toast(x.message)); };
$("idups").onclick = () => showDuplicates().catch((e) => toast(e.message));
$("ilog").onclick = () => showLog().catch((e) => toast(e.message));
addEventListener("keydown", (e) => { if (e.key === "Escape" && $("insp").classList.contains("on")) close(); });
window.__inspector = { S, open, close, loadScan };
