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
  rooms: [{ id: 1, name: "Kitchen" }, { id: 2, name: "Bedroom" }, { id: 3, name: "Living Room" }],
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
    c.append(photo(best.replay));
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
  $("careconsent").textContent = on ? `Sharing is ON since ${new Date(d.consent.since * 1000).toLocaleString()}` : "Sharing is OFF. The person turns it on in Settings on their phone.";
  $("careconsent").className = "badge2 " + (on ? "on" : "off");
  $("carealerts").replaceChildren(...(d.alerts || []).map((a) => el("div", "calert " + a.code, `${a.label}: ${a.message}`)));
  $("careitems").replaceChildren(...(d.items.length ? d.items.map((it) => card(it, !on)) : [el("p", "empty", "Nothing on the list yet. Add the things you want to keep an eye on: pills, glasses, keys.")]));
  $("careask").disabled = !on;
  $("careasked").hidden = !on;
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
    if (d.seen && d.seen[0]) kids.push(photo(d.seen[0].replay));
    $("careans").replaceChildren(...kids);
  } catch (e) { $("careans").replaceChildren(el("p", "hide", e.message)); }
}

function open() { $("care").classList.add("on"); msg(""); loadRooms().then(load).catch((e) => msg("Cannot load: " + e.message)); clearInterval(timer); timer = setInterval(load, 8000); }
function close() { $("care").classList.remove("on"); clearInterval(timer); timer = null; }

$("carebtn").onclick = open;
$("careclose").onclick = close;
$("careadd").onclick = add;
$("carelabel").onkeydown = (e) => e.key === "Enter" && add();
$("careask").onclick = ask;
$("careq").onkeydown = (e) => e.key === "Enter" && ask();
addEventListener("keydown", (e) => { if (e.key === "Escape" && $("care").classList.contains("on")) close(); });
window.__care = { open, close, load };
