// Laptop dashboard: a live view of what the phone and any connected agent are doing with the room memory.
// Everything arrives on /ws/events (see app/events.py); /api/scans and /api/changes feed the compare panel.
// Text from the model (object names, answers) is only ever put in with textContent.
import {drawMap, drawPhoto} from './ui.js';
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
    scans = (await r.json()).scans.filter(s => s.n_frames > 0 && s.status !== 'draft');
  } catch { return; }
  for (const [id, pick] of [['sa', scans[1]], ['sb', scans[0]]]) {
    const sel = $(id), keep = sel.value;
    sel.replaceChildren(...scans.map(s => new Option(`${s.room_name ? s.room_name + ' · ' : ''}#${s.id} · ${new Date(s.started_at * 1000).toLocaleTimeString([], {hour12: false})} · ${s.n_objects} objects`, s.id)));
    sel.value = keep && scans.some(s => String(s.id) === keep) ? keep : (pick ? pick.id : '');
  }
}

const GROUPS = [['moved', '↔', 'var(--accent)', 'Moved'], ['moved_low_confidence', '↔?', 'var(--amber)', 'Moved, low confidence'], ['possibly_moved', '≈', 'var(--amber)', 'Possibly moved'], ['removed', '−', 'var(--red)', 'Removed'], ['added', '+', 'var(--green)', 'Added'],
  ['cant_compare', '?', 'var(--dim)', 'Can’t compare']];
const two = kind => kind === 'moved' || kind === 'possibly_moved' || kind === 'moved_low_confidence';
const textOf = e => e.speech || (e.reason ? `${e.label}: ${e.reason}.` : '');
let shown = [], at = -1; // the changes of the last comparison, and which one the viewer shows
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
// [label, sighting] pairs of a change: moved has two photos, the others one
const slots = (kind, e) => two(kind) ? [['BEFORE', e.from.replay], ['AFTER', e.to.replay]] : kind === 'cant_compare' ? [['PHOTO', e.replay]] : [[kind === 'removed' ? 'BEFORE' : 'AFTER', e.replay]];

function card(kind, e, i) {
  const [, icon, color, name] = GROUPS.find(g => g[0] === kind);
  const c = el('div', 'card'); c.tabIndex = 0; c.setAttribute('role', 'button'); c.setAttribute('aria-label', `${name}: ${e.label}, open details`);
  const h = el('h3'), ic = el('i', null, icon); ic.style.background = color; h.append(ic, `${name}: ${e.label}`);
  const pair = el('div', 'pair' + (two(kind) ? '' : ' one'));
  for (const [label, rp] of slots(kind, e)) {
    const w = el('div'), cv = document.createElement('canvas'); w.append(el('span', null, label), cv); pair.append(w);
    if (rp) requestAnimationFrame(() => drawPhoto(cv, rp));
  }
  c.append(h, pair, el('p', null, textOf(e)), el('div', 'open', 'CLICK TO OPEN'));
  c.onclick = () => openViewer(i);
  c.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openViewer(i); } };
  return c;
}

/* ---------- full view of one change ---------- */
function openViewer(i) {
  if (!shown.length) return;
  at = (i + shown.length) % shown.length;
  const {kind, e} = shown[at], [, icon, color, name] = GROUPS.find(g => g[0] === kind);
  $('vi').textContent = icon; $('vi').style.background = color; $('vtitle').textContent = `${name}: ${e.label}`;
  $('vcount').textContent = `${at + 1} / ${shown.length}`; $('vtext').textContent = textOf(e);
  $('vprev').disabled = $('vnext').disabled = shown.length < 2;
  const body = $('vbody'); body.className = 'vb' + (two(kind) ? '' : ' one');
  const frames = slots(kind, e).map(([label, rp]) => {
    const s = el('div', 'vslot'), f = el('div', 'frame');
    s.append(el('span', 'label', label), f);
    if (rp) { const cv = document.createElement('canvas'); f.append(cv); s._draw = () => drawPhoto(cv, rp); }
    else f.append(el('div', 'none', 'no photo for this sighting'));
    return s;
  });
  body.replaceChildren(...frames);
  $('viewer').classList.add('on');
  requestAnimationFrame(() => frames.forEach(s => s._draw && s._draw()));
  $('vback').focus();
}
function closeViewer() { $('viewer').classList.remove('on'); const c = $('cards').children[at]; if (c && c.focus) c.focus({preventScroll: true}); }
$('vback').onclick = closeViewer;
$('vprev').onclick = () => openViewer(at - 1);
$('vnext').onclick = () => openViewer(at + 1);
$('viewer').onclick = ev => { if (ev.target === $('viewer')) closeViewer(); };
addEventListener('keydown', ev => {
  if (!$('viewer').classList.contains('on')) return;
  if (ev.key === 'Escape') closeViewer(); else if (ev.key === 'ArrowLeft') openViewer(at - 1); else if (ev.key === 'ArrowRight') openViewer(at + 1);
});
addEventListener('resize', () => { if ($('viewer').classList.contains('on')) openViewer(at); });

function clearResults(msg) {
  shown = []; at = -1; $('cards').replaceChildren(); $('clr').hidden = true; $('chalign').textContent = ''; $('chalign').className = '';
  $('chsum').textContent = msg || 'Pick two scans to see what moved, appeared or disappeared.';
}
async function compare() {
  const a = $('sa').value, b = $('sb').value;
  if (!a || !b) { clearResults('Needs two finished scans.'); return; }
  $('chsum').textContent = 'comparing…';
  let r;
  try { r = await (await fetch(`/api/changes?before=${a}&after=${b}`)).json(); } catch (e) { clearResults(`failed: ${e.message}`); return; }
  if (r.status !== 'ok') { clearResults(r.message || r.detail || r.status); return; }
  const al = r.alignment || {}, q = al.quality || 'failed';
  $('chalign').textContent = `Alignment ${q}` + (al.inliers ? ` · ${al.inliers} matches from ${al.pairs} frame pairs · start heading differs by ${Math.round((al.offset_used_deg || 0) * 10) / 10}° (±${al.spread_deg ?? 0}°)` : '')
    + (q === 'good' ? ` · a move counts beyond what ${Math.round(al.viewpoint_cm_used || al.viewpoint_cm || 15)} cm of viewpoint change explains`
      : q === 'weak' && al.low_confidence_enabled ? ' · nothing is called moved, new or removed except moves marked low confidence' : ' · nothing is called moved, new or removed');
  $('chalign').className = 'al-' + q;
  $('chsum').textContent = `${r.moved.length} moved, ${(r.moved_low_confidence || []).length} moved (low confidence), ${(r.possibly_moved || []).length} possibly moved, ${r.removed.length} removed, ${r.added.length} added, ${r.unchanged_count} unchanged, ${(r.cant_compare || []).length} can’t compare`;
  shown = GROUPS.flatMap(([k]) => (r[k] || []).map(e => ({kind: k, e})));
  $('cards').replaceChildren(...(shown.length ? shown.map((x, i) => card(x.kind, x.e, i)) : [el('div', 'empty', 'Nothing changed between these two scans.')]));
  $('clr').hidden = false;
  log(Date.now() / 1000, 'srv', `compared scan ${a} with ${b}: ${$('chsum').textContent}`);
}
$('clr').onclick = () => clearResults();
$('cmp').onclick = compare;
clearResults();

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
