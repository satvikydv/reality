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
  rm.onclick = async () => { await send("DELETE", `/api/care/watch/${it.id}`); await load(); };
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
  try { d = await j("/api/care"); } catch (e) { msg("Cannot load: " + e.message); return; }
  const on = d.consent.on;
  $("careconsent").textContent = on ? `Sharing is ON since ${new Date(d.consent.since * 1000).toLocaleString()}` : "Sharing is OFF. The person turns it on in Settings on their phone.";
  $("careconsent").className = "badge2 " + (on ? "on" : "off");
  $("carealerts").replaceChildren(...(d.alerts || []).map((a) => el("div", "calert " + a.code, `${a.label}: ${a.message}`)));
  $("careitems").replaceChildren(...(d.items.length ? d.items.map((it) => card(it, !on)) : [el("p", "empty", "Nothing on the list yet. Add the things you want to keep an eye on: pills, glasses, keys.")]));
  $("careask").disabled = !on;
  $("careasked").hidden = !on;
}

async function loadRooms() {
  const r = await j("/api/rooms");
  rooms = r.rooms;
  $("careroom").replaceChildren(new Option("Usual room (optional)", ""), ...rooms.map((x) => new Option(x.name, x.id)));
}

async function add() {
  const label = $("carelabel").value.trim();
  if (!label) return msg("Type what to watch, for example “pills”.");
  try {
    await send("POST", "/api/care/watch", { label, usual_room_id: $("careroom").value ? +$("careroom").value : null });
    $("carelabel").value = ""; msg("Added."); await load();
  } catch (e) { msg(e.message); }
}

async function ask() {
  const q = $("careq").value.trim();
  if (!q) return;
  $("careans").replaceChildren(el("p", "hide", "Looking…"));
  try {
    const d = await send("POST", "/api/care/ask", { question: q });
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
