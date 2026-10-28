// Laptop dashboard: a live view of what the phone and any connected agent are doing with the room memory.
// Everything arrives on /ws/events (see app/events.py); /api/scans and /api/changes feed the compare panel.
// Text from the model (object names, answers) is only ever put in with textContent.
import {drawMap, drawReplay} from './ui.js';
const $ = id => document.getElementById(id), P = new URLSearchParams(location.search);
const S = {frames: 0, objects: [], seconds: 0, cost: 0, covDeg: 0, live: false, t0: 0, hl: null, calls: []};
const born = new Map(); // object id -> when it first appeared on the map (ping animation)
const clock = ts => new Date((ts || Date.now() / 1000) * 1000).toLocaleTimeString([], {hour12: false});

function log(ts, who, text, cls = '') {
  const d = document.createElement('div');
  d.className = `l ${who} ${cls}`;
  const t = document.createElement('time'); t.textContent = clock(ts);
  const w = document.createElement('span'); w.className = 't'; w.textContent = {phone: 'PHONE', agent: 'AGENT', srv: 'SERVER'}[who];
  const m = document.createElement('span'); m.className = 'm'; m.textContent = text;
  d.append(t, w, m);
  const box = $('log'); box.prepend(d);
  while (box.children.length > 80) box.lastChild.remove();
}

const photoCache = new Map();
function drawPhoto(canvas, rp) {
  const url = rp.image_url || `/world/frame/${rp.frame_id}.jpg`;
  let im = photoCache.get(url);
  if (!im) { im = new Image(); im.src = url; photoCache.set(url, im); }
  const paint = () => drawReplay(canvas, im.naturalWidth ? im : null, rp, 1);
  im.complete ? paint() : im.addEventListener('load', paint, {once: true});
  paint();
}

const fmtArgs = a => Object.entries(a || {}).filter(([, v]) => v != null && v !== '').map(([k, v]) => typeof v === 'string' ? JSON.stringify(v) : `${k}=${v}`).join(', ');

function showAgent(c) {
  $('ag').classList.add('on');
  $('aglabel').textContent = 'Agent · external AI via the Reality API';
  $('aq').textContent = `${c.tool}(${fmtArgs(c.args)})`;
  $('aa').textContent = c.answer || (c.status ? `status: ${c.status}` : '');
  $('agmeta').textContent = `${c.status || ''} · ${c.ms ?? '?'} ms · ${clock(c.ts)}`;
  const cv = $('agphoto');
  if (c.sighting) { cv.style.display = 'block'; drawPhoto(cv, c.sighting); } else cv.style.display = 'none';
  const prev = $('prev'); prev.replaceChildren();
  S.calls.slice(1, 5).forEach(p => {
    const d = document.createElement('div'), b = document.createElement('b');
    b.textContent = `${p.tool}(${fmtArgs(p.args)})`; d.append(b, ` → ${p.status || ''}`); prev.append(d);
  });
}

export function handle(m) {
  const t = m.type, ts = m.ts;
  if (t === 'scan_started') {
    Object.assign(S, {frames: 0, objects: [], seconds: 0, cost: 0, covDeg: 0, live: true, t0: performance.now(), hl: null});
    born.clear(); $('phase').textContent = 'Scanning';
    log(ts, 'phone', `scan ${m.scan_id} started`);
  } else if (t === 'world_update') {
    S.objects = m.objects; S.covDeg = m.coverage_deg ?? S.covDeg;
    for (const o of m.objects) if (!born.has(o.id)) born.set(o.id, performance.now());
  } else if (t === 'frame_done') {
    if (m.stored) S.frames++;
    S.cost += m.usd || 0;
    const names = (m.names || []).join(', ') || 'nothing listed';
    log(ts, 'phone', m.stored ? `frame ${m.seq}: ${names} · ${m.kept} kept · ${((m.spark_ms + m.sam_ms) / 1000).toFixed(1)} s` : `frame ${m.seq} NOT stored: ${(m.errors || []).join('; ')}`, m.stored ? '' : 'bad');
  } else if (t === 'frame_rejected' || t === 'frame_error') {
    log(ts, 'phone', `frame ${m.seq}: ${m.reason || m.error}`, 'bad');
  } else if (t === 'scan_summary') {
    S.live = false; S.objects = m.objects; S.covDeg = m.coverage.covered_deg; S.frames = m.frames.stored;
    S.seconds = m.seconds; S.cost = m.cost_usd;
    $('phase').textContent = 'Scan complete';
    log(ts, 'phone', m.speech, 'good');
    loadScans();
  } else if (t === 'find_result') {
    S.hl = m.object ? m.object.id : null;
    log(ts, m.source === 'agent' ? 'agent' : 'phone', `find “${m.query}” → ${m.speech}`);
  } else if (t === 'agent_call') {
    S.calls.unshift(m); S.calls.length = Math.min(S.calls.length, 20);
    if (m.object_id != null) S.hl = m.object_id;
    showAgent(m);
    log(ts, 'agent', `${m.tool}(${fmtArgs(m.args)}) → ${m.status || ''}${m.answer ? ': ' + m.answer : ''}`, m.status === 'error' ? 'bad' : '');
  } else if (t === 'guide_request') {
    log(ts, 'agent', `sent the phone to the ${m.result?.concept || 'object'}: ${m.result?.speech || ''}`);
  } else if (t === 'error') {
    log(ts, 'srv', m.message || 'server error', 'bad');
  }
}

/* ---------- compare scans ---------- */
let scans = [];
async function loadScans() {
  try {
    const r = await fetch('/api/scans'); if (!r.ok) return;
    scans = (await r.json()).scans.filter(s => s.n_frames > 0);
  } catch { return; }
  for (const [id, pick] of [['sa', scans[1]], ['sb', scans[0]]]) {
    const sel = $(id), keep = sel.value;
    sel.replaceChildren(...scans.map(s => new Option(`#${s.id} · ${new Date(s.started_at * 1000).toLocaleTimeString([], {hour12: false})} · ${s.n_objects} objects`, s.id)));
    sel.value = keep && scans.some(s => String(s.id) === keep) ? keep : (pick ? pick.id : '');
  }
}

const GROUPS = [['moved', '↔', 'var(--amber)', 'Moved'], ['removed', '−', 'var(--red)', 'Removed'], ['added', '+', 'var(--green)', 'Added']];
function card(kind, e) {
  const [, icon, color, name] = GROUPS.find(g => g[0] === kind);
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3'), i = document.createElement('i');
  i.textContent = icon; i.style.background = color; h.append(i, `${name}: ${e.label}`);
  const p = document.createElement('p'); p.textContent = e.speech || ''; p.title = e.speech || '';
  const pair = document.createElement('div'); pair.className = 'pair';
  const slot = (label, rp) => {
    const w = document.createElement('div'), s = document.createElement('span'), cv = document.createElement('canvas');
    s.textContent = label; w.append(s, cv); pair.append(w);
    if (rp) requestAnimationFrame(() => drawPhoto(cv, rp));
  };
  if (kind === 'moved') { slot('BEFORE', e.from.replay); slot('AFTER', e.to.replay); }
  else if (kind === 'removed') { pair.classList.add('one'); slot('BEFORE', e.replay); }
  else { pair.classList.add('one'); slot('AFTER', e.replay); }
  c.append(h, pair, p);
  return c;
}

async function compare() {
  const a = $('sa').value, b = $('sb').value;
  if (!a || !b) { $('chsum').textContent = 'Needs two finished scans.'; return; }
  $('chsum').textContent = 'comparing…';
  let r;
  try { r = await (await fetch(`/api/changes?before=${a}&after=${b}`)).json(); } catch (e) { $('chsum').textContent = `failed: ${e.message}`; return; }
  if (r.status !== 'ok') { $('chsum').textContent = r.message || r.detail || r.status; $('cards').replaceChildren(); return; }
  const unv = r.unverified.length ? ` · ${r.unverified.length} could not be checked (not scanned both times)` : '';
  $('chsum').textContent = `${r.moved.length} moved, ${r.removed.length} removed, ${r.added.length} added, ${r.unchanged_count} unchanged${unv}`;
  $('cards').replaceChildren(...GROUPS.flatMap(([k]) => r[k].map(e => card(k, e))));
  log(Date.now() / 1000, 'srv', `compared scan ${a} with ${b}: ${$('chsum').textContent}`);
}
$('cmp').onclick = compare;
$('chsum').textContent = 'Pick two scans to see what moved, appeared or disappeared.';

/* ---------- render loop ---------- */
function frame() {
  if (S.live) S.seconds = (performance.now() - S.t0) / 1000;
  $('cf').textContent = S.frames; $('co').textContent = S.objects.length; $('cs').textContent = Math.round(S.seconds);
  $('cu').textContent = '$' + S.cost.toFixed(3); $('cp').textContent = Math.round(S.covDeg / 3.6) + '%';
  const now = performance.now();
  drawMap($('mapc'), S.objects.map(o => ({...o, age: Math.min(1, (now - (born.get(o.id) ?? 0)) / 700), hl: o.id === S.hl})), {big: 1, pad: 64});
  requestAnimationFrame(frame);
}
frame();

/* ---------- live feed ---------- */
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/events`);
  ws.onopen = () => { $('conn').classList.add('ok'); $('connt').textContent = 'live'; loadScans(); };
  ws.onmessage = e => { try { handle(JSON.parse(e.data)); } catch (err) { console.warn(err); } };
  ws.onclose = () => { $('conn').classList.remove('ok'); $('connt').textContent = 'disconnected, retrying'; setTimeout(connect, 1500); };
}

/* ---------- /dashboard?demo: scripted events, no backend ---------- */
function demo() {
  $('conn').classList.add('ok'); $('connt').textContent = 'demo data';
  const scene = [['laptop', 350, 5], ['mouse', 355, -6], ['cable', 60, -25], ['water bottle', 95, -5], ['charger', 300, -10], ['mug', 120, 2],
                 ['keys', 215, -15], ['phone', 10, -12], ['lamp', 330, 35], ['monitor', 345, 20]];
  const out = []; let shown = 0, seq = 0, covDeg = 0;
  handle({type: 'scan_started', scan_id: 3, ts: Date.now() / 1000});
  const iv = setInterval(() => {
    seq++; covDeg = Math.min(354, covDeg + 28);
    if (shown < scene.length) out.push({id: ++shown, label: scene[shown - 1][0], yaw: scene[shown - 1][1], pitch: scene[shown - 1][2], n_obs: 2});
    handle({type: 'world_update', scan_id: 3, coverage_deg: covDeg, objects: out.slice()});
    handle({type: 'frame_done', seq, stored: true, names: out.slice(-2).map(o => o.label), kept: 2, objects: out.length, spark_ms: 3100, sam_ms: 2400, usd: 0.014, errors: []});
    if (covDeg >= 354) { clearInterval(iv); finish(); }
  }, 450);
  function finish() {
    handle({type: 'scan_summary', scan_id: 3, speech: 'I found a laptop, a mouse, a cable, a water bottle, a charger, a mug, keys, a phone, a lamp and a monitor.', coverage: {covered_deg: 354, gaps: []},
            frames: {stored: seq}, seconds: 31, cost_usd: 0.17, objects: out});
    setTimeout(() => handle({type: 'find_result', source: 'phone', query: 'the thing I charge my laptop with', concept: 'charger', found: true, object: out[4], speech: 'The charger was last seen about 60 degrees to the left of where the scan started, below the laptop.', replay: {frame_id: 1, box: [180, 190, 330, 300], width: 640, height: 480}}), 1500);
    setTimeout(() => handle({type: 'agent_call', tool: 'where_is', args: {query: 'where are my keys?'}, status: 'found', ms: 2310, object_id: 7, answer: 'The keys were last seen about 145 degrees to the right of where the scan started, below the phone.', sighting: {frame_id: 2, box: [220, 160, 360, 270], width: 640, height: 480}}), 3500);
    setTimeout(() => handle({type: 'agent_call', tool: 'what_changed', args: {}, status: 'ok', ms: 640, answer: 'The mug moved. A water bottle is new.'}), 5200);
  }
}
if (P.has('demo')) demo(); else connect();
