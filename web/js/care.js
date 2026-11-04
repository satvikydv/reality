// Caregiver view (laptop dashboard): where a person's important things were last seen, with the photo and the time, and what needs attention.
// It shows what the last scan of each room saw, never live tracking, and says so. Until the person turns sharing on (Settings on their phone)
// the server returns the list without places or photos, and this view says why. All text goes in as textContent.
import { drawSighting } from "./inspector.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

async function j(url, opt) {
  const r = await fetch(url, opt);
  let d = null;
  try { d = await r.json(); } catch { /* no body */ }
  if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
  return d;
}
const send = (method, url, body) => j(url, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });

// /dashboard?demo: the caregiver view with made-up data in the shape of GET /api/care, so the story can be shown without a server.
const DEMO = new URLSearchParams(location.search).has("demo");
const now = () => Date.now() / 1000;
const demoState = {
  rooms: [{ id: 1, name: "Kitchen", scan_id: 11 }, { id: 2, name: "Bedroom", scan_id: 12 }, { id: 3, name: "Living Room", scan_id: 13 }],
  objects: { 11: [["pills", 40, -20], ["mug", 10, -15], ["stove", -30, -25], ["kettle", -45, -10]], 12: [["bed", 0, -30], ["lamp", 60, 5], ["glasses", 55, -10]],
             13: [["sofa", 0, -20], ["tv", 90, 5], ["lamp", -70, 0], ["lamp", 120, 0]] },
  notes: { 1: [{ id: 1, text: "Turn the stove off after cooking", author: "Maya", label: "stove", yaw: 330, pitch: -25, created_at: now() - 7200 }], 2: [], 3: [] },
  nextNote: 2,
  items: [
    { id: 1, label: "pills", usual_room: { id: 2, name: "Bedroom" }, status: "alert",
      seen: [{ room: { id: 1, name: "Kitchen" }, age_seconds: 7200, where: "The pills were last seen about 40° to the right of where the scan started, near the stove.", replay: null }],
      alerts: [{ code: "unusual_place", message: "Last seen in Kitchen, but it usually lives in Bedroom." }] },
    { id: 2, label: "glasses", usual_room: null, status: "ok", seen: [{ room: { id: 3, name: "Living Room" }, age_seconds: 1500, where: "The glasses were last seen near the lamp.", replay: null }], alerts: [] },
    { id: 3, label: "keys", usual_room: null, status: "alert", seen: [],
      alerts: [{ code: "not_seen", message: "Not seen in the last scan of any room (covered: Kitchen 92%, Bedroom 70%, Living Room 85%). It may be in a part nobody scanned." }] }],
  next: 4,
};
async function demoApi(method, url, body) {
  if (url === "/api/rooms") return { rooms: demoState.rooms };
  let m = url.match(/^\/api\/scans\/(\d+)\/objects$/);
  if (m) {
    const objs = (demoState.objects[m[1]] || []).map(([label, yaw, pitch], i) => ({ id: +m[1] * 100 + i, label, yaw, pitch, first_seen: i, thumb: null }));
    const labels = [...new Set(objs.map((o) => o.label))].map((label) => ({ label, objects: objs.filter((o) => o.label === label) }));
    return { labels };
  }
  if ((m = url.match(/^\/api\/rooms\/(\d+)\/notes/))) {
    const list = (demoState.notes[m[1]] ||= []);
    if (method === "POST") { const n = { id: demoState.nextNote++, ...body, created_at: now() }; list.unshift(n); return n; }
    return { notes: list.filter((n) => !n.done_at) };
  }
  if ((m = url.match(/^\/api\/notes\/(\d+)$/))) {
    for (const list of Object.values(demoState.notes)) {
      const i = list.findIndex((n) => n.id === +m[1]);
      if (i < 0) continue;
      if (method === "DELETE") { list.splice(i, 1); return { deleted: +m[1] }; }
      if (body.text != null) list[i].text = body.text.trim();
      if (body.done) list[i].done_at = now();
      return list[i];
    }
  }
  if (url === "/api/care") return { status: "ok", consent: { on: true, since: now() - 86400 }, items: demoState.items, as_of: "the last scan of each room",
    alerts: demoState.items.flatMap((i) => i.alerts.map((a) => ({ item_id: i.id, label: i.label, ...a }))) };
  if (url === "/api/care/watch" && method === "POST") {
    demoState.items.push({ id: demoState.next++, label: body.label, usual_room: null, status: "alert", seen: [],
      alerts: [{ code: "not_seen", message: "Not seen in the last scan of any room (demo)." }] });
    return { status: "ok" };
  }
  if (url.startsWith("/api/care/watch/") && method === "DELETE") { demoState.items = demoState.items.filter((i) => `/api/care/watch/${i.id}` !== url); return { status: "ok" }; }
  if (url === "/api/care/ask") {
    const it = demoState.items.find((i) => body.question.toLowerCase().includes(i.label.replace(/s$/, "")));
    const b = it?.seen[0];
    return b ? { status: "found", answer: `${b.where.replace(/\.$/, "")}, in ${b.room.name}, today. This is what the last scan saw, not live. (demo)`, seen: it.seen }
      : { status: "not_seen", message: "Not in the last scan of any room. It may be somewhere nobody scanned. (demo)", seen: [] };
  }
  throw new Error("not in the demo");
}
const get = (url) => (DEMO ? demoApi("GET", url) : j(url));
const call = (method, url, body) => (DEMO ? demoApi(method, url, body) : send(method, url, body));

const ago = (s) => (s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`);
const msg = (t) => { $("caremsg").textContent = t; };
let rooms = [], timer = null;
const SUGGEST = ["pills", "glasses", "keys", "wallet", "phone"];

function photo(replay) {
  const cv = el("canvas");
  if (replay) drawSighting(cv, { image_url: replay.image_url, frame_id: replay.frame_id, box: replay.box, width: replay.width, height: replay.height, mask: null }, { maxW: 480 });
  return cv;
}

function card(it, hidden) {
  const c = el("div", "citem" + (it.status === "alert" ? " alert" : ""));
  const head = el("div", "chead");
  head.append(el("b", null, it.label));
  const rm = el("button", "small", "Remove");
  rm.setAttribute("aria-label", `Stop watching ${it.label}`);
  rm.onclick = async () => { await call("DELETE", `/api/care/watch/${it.id}`); await load(); };
  head.append(rm);
  c.append(head);
  if (hidden) { c.append(el("p", "hide", "Places and photos are hidden until sharing is on.")); return c; }
  const best = it.seen[0];
  if (best) {
    if (best.replay) c.append(photo(best.replay));
    c.append(el("div", "where", `${best.room.name} · ${ago(best.age_seconds)}`));
    if (best.where) c.append(el("div", "hide", best.where));
    if (it.seen.length > 1) c.append(el("div", "hide", "Also seen in " + it.seen.slice(1).map((s) => `${s.room.name} (${ago(s.age_seconds)})`).join(", ")));
  }
  for (const a of it.alerts) c.append(el("div", "calert " + a.code, a.message));
  if (it.usual_room) c.append(el("div", "hide", `Usually in ${it.usual_room.name}`));
  return c;
}

async function load() {
  let d;
  try { d = await get("/api/care"); } catch (e) { msg("Cannot load the caregiver view: " + e.message + ". Check that the server is running."); return; }
  const on = d.consent.on;
  $("careconsent").textContent = on ? `Sharing is ON since ${new Date(d.consent.since * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}` : "Sharing is OFF. The person turns it on in Settings on their phone.";
  $("careconsent").className = "badge2 " + (on ? "on" : "off");
  const alerts = d.alerts || [];
  // "all clear" is only said when places could be checked: with sharing off nothing is known, which is not the same as nothing wrong
  $("caresum").textContent = !d.items.length ? "" : !on ? `${d.items.length} watched · sharing is off` : `${alerts.length ? `${alerts.length} need${alerts.length === 1 ? "s" : ""} attention` : "All clear"} · ${d.items.length} watched`;
  $("carealerts").replaceChildren(...(alerts.length ? alerts.map((a) => el("div", "calert " + a.code, `${a.label}: ${a.message}`))
    : !d.items.length ? [] : !on ? [el("div", "calert", "Sharing is off, so nothing can be checked until the person turns it on in Settings on their phone.")]
    : [el("div", "calert clear", "All clear: everything on the list was seen in the last scan of a room, where it usually is.")]));
  const have = new Set(d.items.map((i) => i.label.toLowerCase()));
  $("caresug").replaceChildren(...SUGGEST.filter((l) => !have.has(l)).map((l) => { const b = el("button", "small", "+ " + l); b.onclick = () => { $("carelabel").value = l; add(); }; return b; }));
  $("careitems").replaceChildren(...(d.items.length ? d.items.map((it) => card(it, !on)) : [el("p", "empty", "Nothing on the list yet. Add the things you want to keep an eye on: pills, glasses, keys.")]));
  $("careask").disabled = !on;
  $("careasked").hidden = !on;
  $("cnoff").hidden = on;
  $("cnon").hidden = !on;
  if (on && !notesReady) { notesReady = true; notesRooms(); }
  else if (on && !editing && !document.activeElement?.closest?.("#cnotes")) loadNotes(); // keep the list fresh, never under someone typing
}

/* Notes for them: pick a room, pick something in it, leave a note on it. The note hangs at that object's direction, so it shows over
   the camera on their phone when they look at it (the phone's overlay refreshes every few seconds). Same notes API as the phone. */
let notesReady = false, nRoom = null, nObj = null, editing = false;
function nameObjects(labels) { // "lamp 1", "lamp 2" when a room has more than one, in the order the scan first saw them
  const out = [];
  for (const g of labels) {
    const objs = [...g.objects].sort((a, b) => (a.first_seen ?? 0) - (b.first_seen ?? 0) || a.id - b.id);
    objs.forEach((o, i) => out.push({ ...o, name: objs.length > 1 ? `${g.label} ${i + 1}` : g.label }));
  }
  return out;
}
function notesRooms() {
  const scanned = rooms.filter((r) => r.scan_id);
  const sel = $("cnroom");
  sel.replaceChildren(...(scanned.length ? scanned.map((r) => new Option(r.name, r.id)) : [new Option("No room has been scanned yet", "")]));
  sel.disabled = !scanned.length;
  nRoom = scanned.find((r) => r.id === nRoom)?.id ?? scanned.find((r) => r.active)?.id ?? scanned[0]?.id ?? null;
  if (nRoom != null) sel.value = String(nRoom);
  loadObjects();
  loadNotes();
}
async function loadObjects() {
  nObj = null; $("cncompose").hidden = true;
  const r = rooms.find((x) => x.id === nRoom);
  if (!r?.scan_id) { $("cnobjs").replaceChildren(el("p", "hide", "Scan this room on the phone first.")); return; }
  let d;
  try { d = await get(`/api/scans/${r.scan_id}/objects`); } catch (e) { $("cnobjs").replaceChildren(el("p", "hide", "Cannot load the room's things: " + e.message)); return; }
  const objs = nameObjects(d.labels || []);
  $("cnobjs").replaceChildren(...(objs.length ? objs.map((o) => {
    const b = el("button", "cnobj");
    b.setAttribute("role", "option"); b.setAttribute("aria-selected", "false"); b.dataset.id = o.id;
    b.append(o.thumb ? photo(o.thumb) : el("div", "cnph"), el("span", null, o.name)); // no photo: a quiet placeholder, never an empty box
    b.onclick = () => {
      nObj = o;
      for (const x of $("cnobjs").children) x.setAttribute("aria-selected", String(x === b));
      $("cnat").textContent = `On the ${o.name} in ${r.name}`;
      $("cncompose").hidden = false; $("cntext").focus();
    };
    return b;
  }) : [el("p", "hide", "The last scan of this room found nothing to leave a note on.")]));
}
async function loadNotes() {
  if (nRoom == null) { $("cnlist").replaceChildren(); return; }
  let d;
  try { d = await get(`/api/rooms/${nRoom}/notes`); } catch (e) { $("cnlist").replaceChildren(el("p", "hide", "Cannot load the notes: " + e.message)); return; }
  $("cnlisthead").textContent = d.notes.length ? `In this room (${d.notes.length})` : "In this room";
  $("cnlist").replaceChildren(...(d.notes.length ? d.notes.map(noteCard) : [el("p", "hide", "No notes in this room yet.")]));
}
function noteCard(n) {
  const c = el("div", "cnote");
  const p = el("p", null, n.text);
  const meta = el("div", "m", [n.label ? `on the ${n.label}` : "in the direction it was left", n.author ? `from ${n.author}` : null, ago(now() - n.created_at)].filter(Boolean).join(" · "));
  const acts = el("div", "acts");
  const edit = el("button", "small", "Edit"), done = el("button", "small", "Done"), del = el("button", "small del", "Delete");
  edit.onclick = () => {
    editing = true;
    const t = el("textarea"); t.maxLength = 280; t.rows = 3; t.value = n.text; t.setAttribute("aria-label", "Note");
    const save = el("button", "small go", "Save"), cancel = el("button", "small", "Cancel");
    save.onclick = async () => { try { await call("PATCH", `/api/notes/${n.id}`, { text: t.value }); msg("Note changed."); } catch (e) { return msg(e.message); } editing = false; loadNotes(); };
    cancel.onclick = () => { editing = false; loadNotes(); };
    t.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save.onclick(); } if (e.key === "Escape") cancel.onclick(); };
    p.replaceWith(t); acts.replaceChildren(save, cancel); t.focus();
  };
  done.onclick = async () => { try { await call("PATCH", `/api/notes/${n.id}`, { done: true }); msg("Marked done. It no longer shows on their phone."); } catch (e) { return msg(e.message); } loadNotes(); };
  del.onclick = async () => {
    if (!confirm(`Delete the note “${n.text}”? It disappears from their phone too.`)) return;
    try { await call("DELETE", `/api/notes/${n.id}`); msg("Note deleted."); } catch (e) { return msg(e.message); }
    loadNotes();
  };
  acts.append(edit, done, del);
  c.append(p, meta, acts);
  return c;
}
async function addNote() {
  const text = $("cntext").value.trim(), author = $("cnfrom").value.trim();
  if (!nObj) return msg("Pick the thing to leave the note on.");
  if (!text) return msg("Write the note first.");
  try { localStorage.setItem("care.author", author); } catch { /* private window: the name is not remembered */ }
  try {
    await call("POST", `/api/rooms/${nRoom}/notes`, { text, yaw: nObj.yaw, pitch: nObj.pitch, label: nObj.name, author: author || "Caregiver" });
  } catch (e) { return msg(e.message); }
  $("cntext").value = ""; msg(`Note left on the ${nObj.name}. It shows on their phone when they look there.`);
  loadNotes();
}

async function loadRooms() {
  const r = await get("/api/rooms");
  rooms = r.rooms;
  $("careroom").replaceChildren(new Option("Usual room (optional)", ""), ...rooms.map((x) => new Option(x.name, x.id)));
}

async function add() {
  const label = $("carelabel").value.trim();
  if (!label) return msg("Type what to watch, for example “pills”.");
  try {
    await call("POST", "/api/care/watch", { label, usual_room_id: $("careroom").value ? +$("careroom").value : null });
    $("carelabel").value = ""; msg("Added."); await load();
  } catch (e) { msg(e.message); }
}

async function ask() {
  const q = $("careq").value.trim();
  if (!q) return;
  $("careans").replaceChildren(el("p", "hide", "Looking…"));
  try {
    const d = await call("POST", "/api/care/ask", { question: q });
    const kids = [el("p", "ans", d.answer || d.message)];
    if (d.seen && d.seen[0] && d.seen[0].replay) kids.push(photo(d.seen[0].replay));
    $("careans").replaceChildren(...kids);
  } catch (e) { $("careans").replaceChildren(el("p", "hide", e.message)); }
}

function open() { $("care").classList.add("on"); msg(""); loadRooms().then(load).catch((e) => msg("Cannot load: " + e.message)); clearInterval(timer); timer = setInterval(load, 8000); }
function close() { $("care").classList.remove("on"); clearInterval(timer); timer = null; }

$("carebtn").onclick = open;
$("careclose").onclick = close;
$("careadd").onclick = add;
$("carelabel").onkeydown = (e) => { if (e.key === "Enter") add(); }; // returning false would cancel every other key
$("careask").onclick = ask;
$("careq").onkeydown = (e) => { if (e.key === "Enter") ask(); };
$("cnroom").onchange = () => { nRoom = +$("cnroom").value; loadObjects(); loadNotes(); };
$("cnadd").onclick = addNote;
$("cntext").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addNote(); } };
try { $("cnfrom").value = localStorage.getItem("care.author") || ""; } catch { /* no storage */ }
addEventListener("keydown", (e) => { if (e.key === "Escape" && $("care").classList.contains("on")) close(); });
window.__care = { open, close, load };
