import {drawRing,drawReplay} from './ui.js';
import {drawAR,drawBearing,nd} from './ar.js';
import {guide} from './guide.js';
import {WorldLayer} from './world.js';
import {diffCalibration} from './camera-lock.js';
import {Prefs,KFPS,LANGS,DETAILS} from './prefs.js';
import {Onboarding,CARDS,PRIMER,cameraGate,showIntroOnStart} from './onboarding.js';
import {requestAccess,classifyCameraError,MOTION_DENIED} from './permissions.js';
import {CameraSession,RELEASED} from './camera-session.js';
import {coachHint,lightVerdict,meanLuma,processing} from './coach.js';
import {StatusPoller,describe} from './connection.js';
import {explainNotFound,ago} from './explain.js';
import {SCREENS,NAV,ids,sectionId,isCamera,navTab,hasState,applyState} from './screens.js';
import {createStore,INITIAL,roomById,activeRoom,roomLabel,freeNameChips} from './store.js';
import {makeApi,describeFailure} from './api.js';
import {el as mk,paintThumbs,thumb,roomCard,roomChip,switcherRow,summaryTiles,groupTile,historyRow,changeCard,findGroup,since,labelRow,instanceRow,sightingCard} from './views.js';
import {fixture} from './fixtures.js';
const $=id=>document.getElementById(id),P=new URLSearchParams(location.search);
const body=document.body;let demo=P.has('demo'),scrn='home';
const prefs=new Prefs(),store=createStore(INITIAL),api=makeApi(),S=()=>store.get();
// Nothing in this file touches the camera, the motion sensors or the microphone while the page loads. They start only from a
// tap that asks for a camera screen (Scan, Explore, Guide me), through CameraSession.acquire(). Screens are listed in screens.js;
// what each shows in each state is rendered by RENDER[screen](state,data), for the real app and for demo mode alike.
const cur={yaw:0,tyaw:0,pitch:0,tpitch:0,roll:0,bins:Array(36).fill(false),bands:null,frac:0,pulse:0,sent:0,shutter:9,gap:null};
let summary=null,shownSummary=null,find=null,img=null,rt=0,arrowA=0,M={top:150,bot:200,tapeY:70};
const world=new WorldLayer({intervalMs:2000});
let arOn=prefs.get('ar');
const buzz=ms=>{try{navigator.vibrate&&navigator.vibrate(ms)}catch{}};
// Every spoken reply is also shown as text. Screens that already print the reply do not need the bar.
const SELF_CAPTIONED=new Set(['result','summary','changes','guide']);let capT=null;
function caption(t){const c=$('cap');if(!t||SELF_CAPTIONED.has(scrn)){c.style.display='none';return}$('captext').textContent=t;c.style.display='block';clearTimeout(capT);capT=setTimeout(()=>c.style.display='none',9000)}
const speak=t=>{caption(t);try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(t);u.lang=prefs.voiceLang();u.rate=1;speechSynthesis.speak(u)}catch{}};
const toast=(m,ms=3500)=>{const e=$('toast');e.textContent=m;e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display='none',ms)};
const section=id=>$(sectionId(id)),setState=(id,state)=>{if(!hasState(id,state))console.warn('unknown state',id,state);applyState(section(id),state)};
const txt=(id,t)=>{$(id).textContent=t};
function measure(){ // where the HUD may draw: below the top bar, above the bottom controls
  const hud=$('hud').getBoundingClientRect(),tp=document.querySelector(`#${sectionId(scrn)} .top`),bot=$(sectionId(scrn))?.querySelector('.bot')?.getBoundingClientRect();
  M.tapeY=tp?tp.getBoundingClientRect().bottom+4:70;M.top=M.tapeY+80;M.bot=bot?hud.height-bot.top+14:200}
addEventListener('resize',measure);

/* ---------- navigation (driven by the registry) ---------- */
const hist=[];
function go(id,{back=false}={}){
  const was=scrn;if(!SCREENS[id])throw new Error('no screen '+id);
  if(was!==id&&!back)hist.push(was);while(hist.length>20)hist.shift();
  scrn=id;body.dataset.screen=id;
  for(const sid of ids())if(!SCREENS[sid].sheet)section(sid).classList.toggle('on',sid===id);
  closeSwitcher();
  const tab=navTab(id);body.classList.toggle('nav-on',!!tab);body.classList.toggle('cam',isCamera(id));
  for(const b of $('nav').children)b.dataset.nav===tab?b.setAttribute('aria-current','page'):b.removeAttribute('aria-current');
  if(was!==id&&isCamera(was)&&!isCamera(id))stopCamera('left the '+was+' screen'); // the camera is on only while a camera screen is showing
  if(was==='guide'&&id!=='guide')stopGuide();
  if(was==='summary'&&id!=='summary')stopProc();
  if(id==='result')rt=0;
  syncWorld();renderSwitchers();requestAnimationFrame(measure)}
function goBack(){ // the screen before this one, skipping camera screens, the review and the first-run screens
  let to=null;while(hist.length&&!to){const c=hist.pop();if(c!==scrn&&!isCamera(c)&&!['summary','primer','welcome'].includes(c))to=c}
  reopen(to||'home')}
function reopen(id){ // open a screen again through its loader, so what it shows is current
  const OPEN={home:openHome,find:()=>openFind(),changes:()=>openChanges(),settings:openSettings,room:()=>openRoom(S().viewRoomId),roomsettings:()=>openRoomSettings(S().viewRoomId),wizard:()=>openWizard(),result:()=>go('result'),labels:()=>openLabels(insp.scanId),instances:()=>openInstances(insp.label),sightings:()=>openSightings(insp.objectId)};
  (OPEN[id]||openHome)()}
const showsWorld=()=>arOn&&isCamera(scrn);
function worldRoom(){return scrn==='scan'?S().scan?.roomId:scrn==='explore'?S().viewRoomId:S().activeId}
function syncWorld(){
  if(demo)return;
  const rid=worldRoom();world.url=rid==null?'/api/overlay':`/api/overlay?room_id=${rid}`;
  if(showsWorld()||scrn==='result')world.start();else world.stop()} // keeps /api/overlay fresh only while it is on screen

/* ---------- bottom bar and the room switcher ---------- */
const ICON={home:'<path d="M3 11l9-8 9 8M5 10v10h14V10"/>',find:'<circle cx="11" cy="11" r="7"/><path d="M16 16l5 5"/>',scan:'<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/>',changes:'<path d="M4 8h13l-3-3M20 16H7l3 3"/>',settings:'<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>'};
$('nav').replaceChildren(...NAV.map(([id,label])=>{const b=mk('button',null,null,{'data-act':'nav','data-nav':id});b.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[id]}</svg>`;b.append(mk('span',null,label));return b}));
function renderSwitchers(){const a=activeRoom(S());for(const b of document.querySelectorAll('.roomsw')){b.classList.toggle('none',!a);b.querySelector('.rn').textContent=a?a.name:'No room'}}
function openSwitcher(){const rs=S().rooms;$('swlist').replaceChildren(...rs.map(switcherRow));setState('switcher',rs.length?'default':'empty');section('switcher').classList.add('on')}
function closeSwitcher(){section('switcher').classList.remove('on');section('quick').classList.remove('on')} // both sheets
async function setActive(id){ // the user's explicit choice: the default room for Find, Explore and the Reality API
  if(demo){store.set({activeId:id,rooms:S().rooms.map(r=>({...r,active:r.id===id}))});return true}
  const r=await api.patchRoom(id,{active:true});
  if(!r.ok){toast(describeFailure(r,'switch room').text);return false}
  await loadRooms();toast('Active room: '+roomLabel(S()));return true}
store.on((st,changed)=>{if(changed.includes('rooms')||changed.includes('activeId'))renderSwitchers()});
async function loadRooms(){
  if(demo)return{ok:true,data:null}; // demo mode never talks to a server
  const r=await api.rooms();
  if(r.ok)store.set({rooms:r.data.rooms,recent:r.data.recent,activeId:r.data.active_id,roomsLoaded:true});
  return r}

/* ---------- ScanController callback ---------- */
function gapInfo(b){const n=36;let best=null;for(let i=0;i<n;i++){if(b[i]||!b[(i+n-1)%n])continue;let j=0;while(j<n&&!b[(i+j)%n])j++;if(j<n&&(!best||j>best.len))best={start:i,len:j}}
  return best&&{...best,mid:(best.start+best.len/2)*10,size:best.len*10}}
let ready=false; // the scan screen is showing its checklist; nothing is being scanned yet
export function onChange(st,cov){
  if(cov){cur.bins=cov.bins;cur.frac=cov.fraction;cur.bands=cov.bands||null;cur.gap=cur.frac>.12?gapInfo(cur.bins):null}
  if(st.sent>cur.sent){cur.sent=st.sent;cur.pulse=1;cur.shutter=0;$('flash').classList.remove('go');void $('flash').offsetWidth;$('flash').classList.add('go');buzz(12)}
  if(st.sent<cur.sent)cur.sent=st.sent;
  const ph=st.phase,f=cur.frac;
  if(ph==='starting'||ph==='scanning'){if(scrn!=='scan'){if(demo&&scrn!=='home')return;go('scan')}setState('scan','live')}
  if(ph==='finishing'){if(scrn!=='summary')go('summary');setState('summary','processing');startProc()}
  if(ph==='done'&&scrn==='scan')go('summary');
  if(ph==='idle'&&scrn==='scan'&&!ready)openHome();
  if(ph==='error'){toast(st.error||'Error',st.error&&/Camera settings changed/.test(st.error)?60000:5000);if(scrn==='scan'||scrn==='summary')openHome();if(poll)poll.retryNow()}
  $('pct').innerHTML=Math.round(f*100)+'<i>%</i>';
  $('fcount').textContent=st.sent+' frames';$('nfr').textContent=st.sent;
  $('nob').textContent=st.objects??0;
  $('nnm').textContent=(st.names&&st.names.length)?st.names.join(' · '):'—';
  const h=coachHint({speed:ctl?ctl.policy.speed():null,pitch:cur.pitch,roll:cur.roll,landscape:innerWidth>innerHeight,fraction:f,gap:ctl?ctl.coverage.nextGap(cur.yaw):null,policy:st.hint||''});
  const sl=$('scanline');sl.textContent=h.text;sl.className=h.tone;
  $('hint').textContent=ph==='scanning'?(/^(frame|capture|no orientation|no scan|queue)/i.test(st.hint||'')?'Problem: '+st.hint:(st.status||'')):'';
  if(st.summary&&st.summary!==shownSummary){shownSummary=st.summary;
    if(st.aborted){toast('The scan was stopped early. What it found is kept as an unsaved scan in the room.');loadRooms()} // no jump to a review the user did not ask for
    else handleEvent({type:'scan_summary',...st.summary})}
}

/* ---------- server events ---------- */
export function handleEvent(m){
  const t=m.type||m.event;
  if(t==='scan_summary'){summary=m;RENDER.summary('review',{summary:m});world.refresh()}
  if(t==='find_result')RENDER.result(null,m);
  if(t==='guide_request'){ // another app asked this phone to guide to an object: show it, but the camera starts only from a tap
    RENDER.result(null,m.result);toast('Another app sent you to this object. Tap Guide me to follow it.')}
  if(t==='error')toast(m.message||'Server error');
}
const dirTxt=(y,p)=>{y=nd(y);return{yaw:Math.round(Math.abs(y))+'° '+(y<0?'L':'R'),pit:Math.round(Math.abs(p||0))+'° '+((p||0)>=0?'↑':'↓')}};

/* ---------- the camera: starts from a tap, stops when the screen is left ---------- */
let ctl=null,sock=null,cam=null,orient=null,session=null,poll=null,facing='environment',streaming=null,guard=null,camAlarm=false,sockUp=false;
const camMeta=()=>({facing,video_w:$('v').videoWidth,video_h:$('v').videoHeight,screen_angle:window.screen.orientation?.angle??null,settings:guard?{...guard.current(),zoomLocked:guard.zoomLocked}:null});
const active=()=>ctl&&['starting','scanning','finishing'].includes(ctl.phase);
function onCamState(st){
  body.classList.toggle('live',st.camera==='on');
  for(const b of document.querySelectorAll('.camstop')){b.classList.toggle('starting',st.camera==='starting');b.classList.toggle('off',st.camera==='off');
    b.querySelector('.cl').textContent=st.camera==='starting'?'Starting camera…':st.camera==='off'?'Camera off':'Camera on'}}
function stopCamera(reason){clearInterval(lightT);if(demo||!session)return;session.release(reason);guard=null;if(ctl)ctl.guard=null;camAlarm=false}
function onHidden(){ // the tab was hidden: the camera is already off. A running scan stops and keeps what it sent.
  if(active())ctl.abort('The app went to the background, so the scan stopped. What it found so far is kept.');
  else if(isCamera(scrn)){openHome();toast('The camera was turned off because the page was hidden.')}}
function onCamEnded(){
  if(active())ctl.abort('The camera stopped (another app may be using it), so the scan stopped. What it found so far is kept.');
  else if(isCamera(scrn)){openHome();toast('The camera stopped. Another app may be using it.')}}
async function useCamera(screen){ // the tap that got us here is the user's permission to start it
  go(screen);if(demo)return true;
  try{const r=await session.acquire(facing);guard=r.guard;ctl.guard=guard;camAlarm=false;
    if(orient.permission==='denied')throw Object.assign(new Error('Motion access was denied'),{name:'MotionDenied'});
    measure();return true}
  catch(e){if(e.name===RELEASED)return false;stopCamera('failed');failCamera(e,screen);return false}}
function failCamera(e,action){pendingAction=action;openPrimer(e.name==='MotionDenied'?MOTION_DENIED:classifyCameraError(e))}
function watchCamera(){ // zoom, resolution or lens changed: the FOV no longer applies
  setInterval(()=>{const d=guard?[...guard.diff(),...diffCalibration(world.calibration,guard.current())]:[];
    if(d.length&&!camAlarm){camAlarm=true;renderConn();toast('Camera settings changed ('+d.map(x=>x.key+' '+x.was+' → '+x.now).join(', ')+'). Overlay and scans are off until the camera is restarted.',60000);
      if(active())ctl.abort('Camera settings changed ('+d.map(x=>x.key).join(', ')+'). The scan was stopped.')}},1000)}

/* ---------- first run: intro, then the permission primer ---------- */
let ob=null,pendingAction=null,primerCanAsk=true;
function openIntro(replay=false){ob=new Onboarding(prefs,{replay});RENDER.welcome();go('welcome')}
function openPrimer(problem=null){RENDER.primer(problem?'denied':'default',{problem});go('primer')}
const wantCamera=action=>{ // Scan, Explore, Guide me
  if(!demo&&cameraGate(prefs)==='primer'){pendingAction=action;return openPrimer()}
  return runAction(action)};
function runAction(a){return a==='scan'?openScan():a==='explore'?useCamera('explore'):openGuide()}

/* ---------- connection ---------- */
let pollView=describe(null),homeSlow=false;
function renderConn(){
  const d=pollView;let label=d.label,tone=d.tone;const wsOk=demo||!sock||sockUp;
  if(camAlarm){label='Camera changed';tone='bad'}else if(d.state==='ready'&&!wsOk){label='Reconnecting…';tone='warn'}
  txt('stat',label);$('dotc').className='dot'+(tone==='ok'?'':tone==='bad'?' bad':tone==='warn'?' warn':' wait');
  if(scrn==='home'&&!demo&&S().roomsLoaded===false)RENDER.home(homeState(),homeFailure())}
function homeFailure(){const d=pollView;return{message:d.state==='offline'?'The server is not reachable. Reality OS keeps trying to reconnect by itself.':d.detail||'The server answered with an error.'}}
function homeState(){const d=pollView;if(!S().roomsLoaded)return d.state==='offline'?'offline':d.state==='error'?'error':homeSlow?'slow':'loading';return S().rooms.length?'default':'empty'}
function onStatus(r,d){const was=pollView.state;pollView=d;renderConn();if(was!=='ready'&&d.state==='ready'&&!S().roomsLoaded&&scrn==='home')openHome()}
function onSockState(s){
  sockUp=s==='connected';renderConn();
  if(!sockUp&&active())ctl.abort('Connection lost, the scan stopped. Objects found so far are kept.');
  if(!sockUp&&findPending){findDone();askState('offline','Lost the connection to the server. It reconnects by itself, then try again.',{label:'Try again',run:()=>doFind(lastQ)})}
  if(sockUp&&poll)poll.retryNow()}

/* ---------- screens: RENDER[screen](state,data) shows a state of a screen. Loaders fetch the data, demo mode uses fixtures. ---------- */
const RENDER={};
const plural=(n,w)=>`${n} ${w}${n===1?'':'s'}`;
const roomStatsLine=r=>r.scan_id?`${r.n_objects} objects · ${r.coverage_pct}% covered · ${since(r.last_scanned)}`:'Not scanned yet';

/* Home: rooms, recent objects, search */
let searchT=null;
RENDER.home=(state,d={})=>{
  if(d.rooms)store.set({rooms:d.rooms,recent:d.recent||[],activeId:d.activeId??null,roomsLoaded:true});
  setState('home',state);
  if(state==='error'||state==='offline'||state==='slow')txt('hmsg',d.message||'');
  if(state==='default'||state==='empty'){
    const st=S(),f=st.filterRoomId;
    $('hchips').replaceChildren(mk('button','chip'+(f==null?' on':''),'All rooms',{'data-act':'filter-room','data-id':'','aria-pressed':String(f==null)}),...st.rooms.map(r=>roomChip(r,r.id===f,'filter-room')));
    $('hrooms').replaceChildren(...st.rooms.filter(r=>f==null||r.id===f).map(roomCard));
    const rec=st.recent.filter(o=>f==null||o.room?.id===f);
    $('hrecent').replaceChildren(...rec.map(o=>thumb(o)));$('hrecent').hidden=!rec.length;$('hrecenthead').hidden=!rec.length;
    paintThumbs($('hmain'));
    if(!$('hsearch').value.trim()){$('hresults').hidden=true;$('hmain').hidden=state!=='default'}}
  renderSwitchers()};
async function openHome(){
  go('home');
  const had=S().roomsLoaded;RENDER.home(had?homeState():'loading',homeFailure());
  homeSlow=false;const slow=setTimeout(()=>{homeSlow=true;if(scrn==='home'&&!S().roomsLoaded)RENDER.home('slow',homeFailure())},6000);
  const r=await loadRooms();clearTimeout(slow);homeSlow=false;if(scrn!=='home')return;
  if(r.ok)RENDER.home(S().rooms.length?'default':'empty');
  else{const f=describeFailure(r,'load your rooms');RENDER.home(f.kind==='slow'?'slow':f.kind==='offline'?'offline':'error',{message:f.text})}}
async function runSearch(){
  const q=$('hsearch').value.trim(),box=$('hresults');
  if(!q){box.hidden=true;$('hmain').hidden=false;return}
  $('hmain').hidden=true;box.hidden=false;box.replaceChildren(mk('div','skel'));
  const r=demo?{ok:true,data:{groups:demoSearch(q)}}:await api.search(q,S().filterRoomId);
  if($('hsearch').value.trim()!==q)return;
  if(!r.ok)return box.replaceChildren(mk('p','dim',describeFailure(r,'search').text));
  if(!r.data.groups.length)return box.replaceChildren(mk('p','dim',`No object called “${q}” in ${S().filterRoomId?roomLabel(S(),S().filterRoomId):'any room'}’s memory.`));
  box.replaceChildren(...r.data.groups.flatMap(g=>{const row=mk('div','thumbs');row.append(...g.objects.map(o=>thumb({...o,room:g.room})));return[mk('h2','sec',g.room.name+(g.room.active?' · active':'')),row]}));paintThumbs(box)}
$('hsearch').addEventListener('input',()=>{clearTimeout(searchT);searchT=setTimeout(runSearch,250)});
function demoSearch(q){return[{room:{id:1,name:'Living Room',active:true},objects:fixture('home','default').recent.filter(o=>o.label.includes(q.toLowerCase()))}].filter(g=>g.objects.length)}
// Open an object from search or Recent. Find results and guidance live in the active room, so another room asks to switch first.
async function openObject(label,roomId){
  if(roomId!==S().activeId){
    const ok=await confirmDlg({title:`Switch to ${roomLabel(S(),roomId)}?`,body:`“${label}” is in ${roomLabel(S(),roomId)}. Rooms are kept apart, so Reality OS needs to make it your active room to open this.`,yes:'Switch room',no:'Not now'});
    if(!ok)return;if(!await setActive(roomId))return}
  openFind({roomId});doFind(label)}

/* Scan wizard: which room, then the live scan */
let wz={selectedId:null,name:'',active:false};
RENDER.wizard=(state,d={})=>{
  if(d.rooms)store.set({rooms:d.rooms,activeId:d.activeId??null,roomsLoaded:true});
  const st=S();wz={selectedId:d.selectedId??null,name:d.name??'',active:wz.active};
  $('wzname').value=wz.name;
  $('wzrooms').replaceChildren(...st.rooms.map(r=>roomChip(r,r.id===wz.selectedId,'pick-room')));
  $('wzchips').replaceChildren(...freeNameChips(st).map(n=>mk('button','chip',n,{'data-act':'name-chip','data-name':n})));
  $('wzactive').setAttribute('aria-pressed',String(wz.active));
  $('wzerr').textContent=d.message||'';
  setState('wizard',state||(st.rooms.length?'rescan':'new'))};
function openWizard({roomId=null,fresh=false}={}){ // scanning: rescan a room (the active one by default), or name a new one
  const st=S();wz.active=fresh||!st.activeId;
  RENDER.wizard(fresh||!st.rooms.length?'new':'rescan',{rooms:st.rooms,activeId:st.activeId,selectedId:fresh?null:(roomId??st.activeId),name:''});go('wizard')}
function wizardRefresh(state){const st=S();$('wzrooms').replaceChildren(...st.rooms.map(r=>roomChip(r,r.id===wz.selectedId,'pick-room')));setState('wizard',state||(wz.name||!st.rooms.length?'new':'rescan'));$('wzactive').setAttribute('aria-pressed',String(wz.active))}
$('wzname').addEventListener('input',e=>{wz.name=e.target.value;if(wz.name.trim())wz.selectedId=null;wizardRefresh()});
async function wizardGo(){
  const name=$('wzname').value.trim();let roomId=wz.selectedId;
  if(name){
    if(demo)roomId=99;
    else{const r=await api.createRoom(name,wz.active);
      if(!r.ok){$('wzerr').textContent=describeFailure(r,'create the room').text;return setState('wizard','error')}
      roomId=r.data.id;await loadRooms()}}
  else if(roomId==null){$('wzerr').textContent='Pick a room to scan, or type a name for a new one.';return setState('wizard','error')}
  else if(wz.active&&roomId!==S().activeId)await setActive(roomId);
  store.set({scan:{roomId,name:name||roomLabel(S(),roomId)}});wantCamera('scan')}

/* Live scan: checklist, then the coverage compass */
const lc=document.createElement('canvas');lc.width=32;lc.height=24;let lightT=null;
function sampleLight(){const v=$('v');if(!v.videoWidth)return null;const x=lc.getContext('2d',{willReadFrequently:true});x.drawImage(v,0,0,32,24);return meanLuma(x.getImageData(0,0,32,24).data)}
function startLight(){clearInterval(lightT);
  const tick=()=>{const lv=lightVerdict(demo?120:sampleLight()),r=$('chklight');txt('lighttext',lv.text);r.className='chk'+(lv.ok===true?' ok':lv.ok===false?' bad':'');r.firstChild.textContent=lv.ok===true?'✓':lv.ok===false?'!':'·'};
  tick();lightT=setInterval(tick,1000)}
RENDER.scan=(state,d={})=>{txt('scanroom',d.room?.name||roomLabel(S(),S().scan?.roomId));setState('scan',state)};
async function openScan(){
  summary=null;shownSummary=null;ready=true;$('chkpeople').setAttribute('aria-pressed','false');txt('lighttext','Checking the light…');
  RENDER.scan('ready');if(!await useCamera('scan'))return;startLight()}
function startScan(){
  if(demo){ready=false;return demoScan()}
  if(!session.active)return toast('The camera is not running yet');
  ctl.policy.minIntervalMs=prefs.minIntervalMs();ready=false;clearInterval(lightT);ctl.start(S().scan.roomId)}
async function cancelScan(){
  if(ready){ready=false;stopCamera('cancelled');return openHome()} // nothing was started: just close the camera
  const yes=await confirmDlg({title:'Discard this scan?',body:'Everything captured so far is deleted. You can scan again right away.',yes:'Discard',no:'Keep scanning',items:[plural(ctl?.st.sent||0,'photo')+' from this scan',plural(ctl?.st.objects||0,'object')]});
  if(!yes)return;
  if(demo){clearInterval(st);return openHome()}
  if(ctl.phase==='scanning'||ctl.phase==='starting')ctl.cancel();else openHome()}

/* Scan summary: reading frames, then review (Save / Discard / View in room) */
let procT=null,openTile=null;
function startProc(){stopProc();updateProc();procT=setInterval(updateProc,500)}
function stopProc(){clearInterval(procT);procT=null}
function updateProc(){
  if(!ctl||demo)return;const s=ctl.st,now=performance.now(),p=processing(s,now-s.doneAt,now-s.progressAt);
  RENDER.summary(p.level==='stalled'||p.level==='late'?'stalled':'processing',{progress:p})}
RENDER.summary=(state,d={})=>{
  if(state==='review'||state==='empty'){
    const m=d.summary;summary=m;stopProc();openTile=null;
    const frames=typeof m.frames==='object'?m.frames.stored:m.frames,cover=typeof m.coverage==='object'?m.coverage.covered_deg/360:m.coverage;
    if(!m.objects.length)state='empty';
    txt('sumtext',m.speech||'');txt('sumpill',(m.room_name?m.room_name+' · ':'')+'Scan complete');
    const {tiles,others}=summaryTiles(m.objects);
    const oth=others&&(()=>{const b=groupTile(others,false);b.classList.add('others');b.replaceChildren(mk('b',null,'Others'),mk('span','n',String(others.count)));return b})();
    $('sumtiles').replaceChildren(...tiles.map(g=>groupTile(g,false)),...(oth?[oth]:[]));
    $('sumdetail').hidden=true;paintThumbs($('sumtiles'));
    txt('mf',frames);txt('ms',Math.round(m.seconds));txt('mc',Math.round((cover||0)*100)+'%');txt('mu',m.cost_usd==null?'–':'$'+m.cost_usd.toFixed(3));
    $('sumsave').disabled=state==='empty';
    if(scrn!=='summary'){go('summary');speak(m.speech);buzz([20,40,20])}}
  else{const p=d.progress||{fraction:0,text:'Starting…',level:'ok'};txt('proctxt',p.text);$('procbar').style.width=Math.round(p.fraction*100)+'%';txt('procpct',Math.round(p.fraction*100)+'%');if(scrn!=='summary')go('summary')}
  setState('summary',state)};
function toggleTile(label){ // tap a tile: its objects open below; tap again to close. "Others" holds the rest.
  if(!summary)return;const {others}=summaryTiles(summary.objects),isOthers=label==='Others'&&others;
  const objs=isOthers?others.objects:summary.objects.filter(o=>o.label===label);
  if(openTile===label){openTile=null;$('sumdetail').hidden=true}
  else{openTile=label;$('sumdetail').replaceChildren(...objs.map(o=>thumb(o,{act:'noop'})));$('sumdetail').hidden=false;paintThumbs($('sumdetail'))}
  for(const b of $('sumtiles').children)b.classList.toggle('on',!!openTile&&(b.dataset.label===openTile||(b.classList.contains('others')&&openTile==='Others')))}
async function saveReview(){
  const m=summary;if(!m)return;
  if(demo){toast('Saved (demo)');return openRoom(m.room_id||1)}
  const r=await api.saveScan(m.room_id,m.scan_id);
  if(!r.ok)return toast(describeFailure(r,'save the scan').text);
  await loadRooms();toast(`Saved. ${m.room_name||'The room'} now remembers this scan.`);openRoom(m.room_id)}
async function discardReview(){
  const m=summary;if(!m)return;
  if(!await confirmDlg({title:'Discard this scan?',body:'It is deleted and the room keeps its current memory.',yes:'Discard',no:'Keep it',items:summary?[plural(summary.objects.length,'object'),plural(typeof summary.frames==='object'?summary.frames.stored:summary.frames||0,'photo')]:[]}))return;
  if(!demo){const r=await api.discardScan(m.room_id,m.scan_id);if(!r.ok)return toast(describeFailure(r,'discard the scan').text);await loadRooms()}
  toast('Scan discarded');openHome()}
// "Check the server": the scan may have finished while its summary never arrived (the link dropped). Ask what it stored.
async function recoverSummary(){
  const sc=S().scan,id=ctl?.st.scanId;if(!sc)return toast('Nothing to recover.');
  const h=await api.roomScans(sc.roomId);
  if(!h.ok)return toast(describeFailure(h,'check the server').text);
  const s=h.data.scans.find(x=>x.id===id)||h.data.scans[0];
  if(!s||(id!=null&&s.id<id)||!s.ended_at)return toast('The server has not finished this scan yet. Keep waiting, or go home and look again later.');
  const o=await api.overlay(sc.roomId,s.id);if(!o.ok)return toast('Could not load the scan’s objects.');
  const n=o.data.objects.length;
  RENDER.summary('review',{summary:{speech:n?`I found ${n} object${n===1?'':'s'}.`:'I did not find any objects.',coverage:null,frames:s.n_frames,seconds:s.ended_at-s.started_at,objects:o.data.objects,scan_id:s.id,room_id:sc.roomId,room_name:roomLabel(S(),sc.roomId)}})}

/* Room details */
RENDER.room=(state,d={})=>{
  const room=d.room;setState('room',state);
  if(state==='error')txt('rerr',d.message||'');
  if(state==='loading'||state==='error'||!room){renderSwitchers();return}
  txt('rname',room.name);$('ractive').hidden=!room.active;$('ractbtn').hidden=room.active;
  const info=[['Objects',room.n_objects],['Covered',room.coverage_pct+'%'],['Last scan',room.last_scanned?since(room.last_scanned):'never'],['Saved scans',room.n_scans]];
  $('rinfo').replaceChildren(...info.map(([k,v])=>{const x=mk('div');x.append(mk('span','label',k),mk('b',null,v));return x}));
  $('rrecent').replaceChildren(...(d.recent_objects||[]).map(o=>thumb({...o,room:{id:room.id,name:room.name}})));paintThumbs($('rrecent'));
  const scans=d.scans||[],mem=scans.find(s=>s.status==='saved');
  $('rhist').replaceChildren(...scans.map(s=>historyRow(s,{memory:mem&&s.id===mem.id})));$('rhisthead').hidden=!scans.length};
async function openRoom(id){
  store.set({viewRoomId:id,findRoomId:null});go('room');
  const known=roomById(S(),id);RENDER.room('loading',{room:known});
  if(demo){const k=known?.n_scans?'default':'empty',f=fixture('room',k);RENDER.room(k,{...f,room:known||f.room});return}
  const [a,b]=await Promise.all([api.room(id),api.roomScans(id)]);
  if(scrn!=='room'||S().viewRoomId!==id)return;
  if(!a.ok||!b.ok)return RENDER.room('error',{message:describeFailure(a.ok?b:a,'load this room').text});
  RENDER.room(a.data.room.scan_id?'default':'empty',{room:a.data.room,recent_objects:a.data.recent_objects,scans:b.data.scans})}
async function scanAction(kind,scanId){
  const rid=S().viewRoomId,room=roomLabel(S(),rid);
  const ask={'delete-scan':{title:'Delete this scan?',body:`It is removed from ${room}’s history, with its photos. If it is the latest scan, ${room} goes back to the one before.`,yes:'Delete',no:'Keep it'},
    'discard-scan':{title:'Discard this unsaved scan?',body:'It is deleted and the room keeps its current memory.',yes:'Discard',no:'Keep it'}}[kind];
  if(ask&&!await confirmDlg(ask))return;
  if(demo){toast('Done (demo)');return}
  const call={'save-scan':()=>api.saveScan(rid,scanId),'discard-scan':()=>api.discardScan(rid,scanId),'delete-scan':()=>api.deleteScan(rid,scanId),
    'set-baseline':()=>api.setBaseline(rid,scanId),'clear-baseline':()=>api.setBaseline(rid,null)}[kind];
  const r=await call();if(!r.ok)return toast(describeFailure(r,'do that').text);
  await loadRooms();openRoom(rid)}

/* Find (in the active room, or in all rooms) */
let findPending=false,findTimers=[],lastQ='',askRun=null,lastGroups=[];
const findRoom=()=>roomById(S(),S().findRoomId??S().activeId);
function askState(kind,msg,act){ // kind: busy | slow | error | offline
  txt('askmsg',msg);$('askspin').style.display=kind==='busy'||kind==='slow'?'':'none';
  const a=$('askact');a.hidden=!act;if(act){a.textContent=act.label;askRun=act.run}
  setState('find',kind)}
function idleFind(){
  const sc=S().scope,r=findRoom();
  if(sc==='room'&&!r)return setState('find','no-room');
  if(sc==='room'&&!r.scan_id){txt('fempty',`${r.name} has not been scanned yet`);return setState('find','room-empty')}
  setState('find','idle')}
RENDER.find=(state,d={})=>{
  if(d.groups)lastGroups=d.groups;
  if(d.scope)store.set({scope:d.scope});
  for(const b of $('fscope').children)b.setAttribute('aria-pressed',String(b.dataset.v===S().scope));
  fillQuick(d.labels||[]);
  $('q').placeholder=S().scope==='all'?'Find something in any room…':`Find something in ${findRoom()?.name||'a room'}…`;
  if(d.query)$('q').value=d.query;
  if(state==='room-empty')txt('fempty',`${findRoom()?.name||'This room'} has not been scanned yet`);
  if(state==='all-results'||state==='all-none'){$('fgroups').replaceChildren(...(d.groups||[]).map(findGroup));paintThumbs($('fgroups'))}
  if(['busy','slow','error','offline'].includes(state))askState(state,d.message||'',state==='busy'||state==='slow'?{label:'Cancel',run:()=>{findDone();idleFind()}}:{label:'Try again',run:()=>doFind(lastQ)});
  else setState('find',state)};
async function openFind({roomId=null,scope=null}={}){
  store.set({findRoomId:roomId,scope:scope||S().scope});go('find');findDone();
  RENDER.find('idle',{labels:[],scope:S().scope});idleFind();
  const r=findRoom();
  if(r?.scan_id&&!demo){const d=await api.room(r.id);if(d.ok&&scrn==='find')fillQuick(Object.keys(d.data.labels).slice(0,12))}}
// Quick ask: what the room contains, as tap-to-ask names in a bottom sheet over Find (not a row of chips on the screen)
function fillQuick(labels){$('quicklist').replaceChildren(...labels.map(l=>mk('button','chip qrow',`Where is my ${l}?`,{'data-act':'find-chip','data-label':l})));setState('quick',labels.length?'default':'empty')}
function findDone(){findPending=false;findTimers.forEach(clearTimeout);findTimers=[]}
function doFind(label,all=null){
  const q=(typeof label==='string'?label:$('q').value).trim();if(!q&&!demo)return;
  const everywhere=all??S().scope==='all';
  if(demo)return demoFind(q,everywhere);
  const r=findRoom();if(!everywhere&&!r)return setState('find','no-room');
  lastQ=q;findDone();
  const msg=everywhere?{type:'find',query:q,all_rooms:true}:{type:'find',query:q,room_id:r.id};
  if(!sock.sendJson(msg))return askState('offline','Not connected to the server. It reconnects by itself, then try again.',{label:'Try again',run:()=>doFind(q,everywhere)});
  findPending=true;const cancel={label:'Cancel',run:()=>{findDone();idleFind()}};
  askState('busy','Looking for “'+q+'”…',cancel);
  findTimers=[setTimeout(()=>{if(findPending)askState('slow','Still looking. The answer is slower than usual…',cancel)},8000),
    setTimeout(()=>{if(findPending){findDone();askState('error','No answer from the server after 30 seconds.',{label:'Try again',run:()=>doFind(q,everywhere)})}},30000)]}
function findFailed(msg){
  findDone();const key=/auth|401|403|api key|permission/i.test(msg||'');
  askState('error',key?'The model API rejected the key. Check MODEL_API_KEY on the server.':'The server could not answer: '+(msg||'unknown error'),{label:'Try again',run:()=>doFind(lastQ)})}
function onFindReply(m){
  if(m.all_rooms){RENDER.find(m.found?'all-results':'all-none',{groups:m.groups,query:m.query,scope:'all',labels:[]});speak(m.speech);return}
  if(m.no_room){setState('find','no-room');return}
  RENDER.result(null,m)}
async function openGroupResult(roomId){ // a result from another room: opening it asks to switch to that room first
  const g=lastGroups.find(x=>x.room_id===roomId);if(!g)return;
  if(roomId!==S().activeId){
    if(!await confirmDlg({title:`Switch to ${roomLabel(S(),roomId)}?`,body:'Rooms are kept apart. Opening this result makes that room your active room.',yes:'Switch room',no:'Not now'}))return;
    if(!await setActive(roomId))return}
  RENDER.result(null,{...g,type:'find_result',query:lastQ,concept:g.object?.label})}
// Voice is optional. If it cannot work, say so and leave the text box ready.
function startMic(){
  const R=window.SpeechRecognition||window.webkitSpeechRecognition,m=$('mic'),typeInstead=t=>{toast(t);$('q').focus()};
  if(!R)return typeInstead('Voice input is not available in this browser. Type your question instead.');
  const r=new R();r.lang=prefs.voiceLang();
  r.onresult=e=>{$('q').value=e.results[0][0].transcript;doFind()};
  r.onerror=e=>typeInstead(e.error==='not-allowed'||e.error==='service-not-allowed'?'Microphone access is blocked. Type your question instead.':e.error==='no-speech'?'I did not hear anything. Try again, or type it.':'Voice input failed. Type your question instead.');
  r.onend=()=>m.classList.remove('on');
  try{r.start();m.classList.add('on');toast('Listening…')}catch{typeInstead('Voice input could not start. Type your question instead.')}}

/* Find result */
RENDER.result=(state,m)=>{
  if(!m)return;find=m;const nf=m.found===false;
  state=state||(nf?(m.checked&&m.checked.scans===0?'notscanned':'notfound'):'found');
  setState('result',state);
  txt('resulttext',m.speech);txt('resq','“'+(m.query||m.concept||'')+'”');txt('resroom',m.room_name||roomLabel(S(),m.room_id));
  if(nf){const x=explainNotFound(m);txt('nftitle',x.title);
    const pct=Math.max(0,Math.min(100,Math.round((m.checked?.covered_deg||0)/3.6)));$('nfpct').innerHTML=pct+'<i>%</i>';$('nfbar').style.width=pct+'%';$('nfcov').hidden=!m.checked?.scans;
    txt('nftext',m.checked?.scans===0&&m.room_name?`${m.room_name} has no saved scan, so there is nothing to search. Scan it first.`:x.text.replace('your last scan',m.room_name?m.room_name+'’s last scan':'your last scan'));
    world.highlight(null);img=null;rt=0;go('result');speak(m.speech);buzz(25);return}
  const o=m.object||{},off=m.from_scan_start,dd=dirTxt(off?off.dyaw:(o.yaw||0),off?off.dpitch:(o.pitch||0));txt('dyaw',dd.yaw);txt('dpit',dd.pit);
  txt('dseen',o.n_obs!=null?`${o.n_obs} sighting${o.n_obs===1?'':'s'} · last ${ago(o.age_s??0)}${o.uncertainty_deg>0?' · ±'+Math.round(o.uncertainty_deg)+'°':''}`:'–');
  world.highlight(o.id);txt('rtag',((m.concept||'')+' · FRAME '+(m.replay?.frame_id??'–')).toUpperCase());txt('gobj',m.concept||'Object');
  img=null;if(m.replay&&!demo){img=new Image();img.src='/world/frame/'+m.replay.frame_id+'.jpg'}
  rt=0;go('result');speak(m.speech);buzz(25)};
async function guideMe(){
  const m=find;if(!m?.object)return;
  if(m.room_id!=null&&m.room_id!==S().activeId){ // guidance never crosses rooms: switch to the object's room first
    if(!await confirmDlg({title:`Switch to ${m.room_name}?`,body:'Guidance only works in the active room. Switch to it to be guided to this object.',yes:'Switch room',no:'Not now'}))return;
    if(!await setActive(m.room_id))return}
  if(!demo){const r=await api.guide(m.object.id,m.room_id);if(!r.ok)return toast(describeFailure(r,'start guidance').text)}
  wantCamera('guide')}

/* Guided search: arrow, direction and angle in words, confidence and last seen. Never a distance. */
let found=false,lastBand=99;
function coneD(c){c=Math.min(90,Math.max(0,c));if(c<1)return '';const r=138,a=c*Math.PI/180,x=Math.sin(a)*r,y=150-Math.cos(a)*r;return `M150 150 L${150-x} ${y} A${r} ${r} 0 0 1 ${150+x} ${y} Z`}
export function setGuide({angle=0,deg=0,hint='',found:f=false,coneDeg=0,words=''}){
  $('cone').setAttribute('d',f?'':coneD(coneDeg));
  let d=((angle-arrowA+540)%360)-180;arrowA+=d;
  $('arrow').style.transform=`rotate(${arrowA}deg)`;txt('gnum',Math.round(deg));txt('ghint',hint);txt('gwords',f?'':words);
  const was=section('guide').dataset.state==='found';found=f;
  section('guide').classList.toggle('found',f);setState('guide',f?'found':'default');
  if(f){txt('ghint','Found it');if(!was){buzz([30,60,30,60,80]);speak('Found it')}}}
const guideTarget=()=>find?.object&&find.found!==false?{yaw:find.object.yaw,pitch:find.object.pitch||0,label:find.concept,uncertainty:find.object.uncertainty_deg||0}:null;
let guideT=null,gt=null;
function guideInfo(){const o=find?.object||{};return`Confidence ${Math.round((o.conf_best??0)*100)}% · last seen ${ago(o.age_s??0)}${o.uncertainty_deg>0?' · ±'+Math.round(o.uncertainty_deg)+'°':''}`}
const wordsFor=(t,p)=>{const dy=nd(t.yaw-p.yaw),dp=t.pitch-p.pitch;return`About ${Math.round(Math.abs(dy))}° to your ${dy<0?'left':'right'}${Math.abs(dp)>=8?', '+Math.round(Math.abs(dp))+'° '+(dp>0?'up':'down'):''}`};
function startGuide(){
  stopGuide();found=false;lastBand=99;txt('ginfo',guideInfo());
  const t=guideTarget();if(!t||(!orient&&!demo))return toast('Nothing to guide to');
  if(demo)return demoGuide();
  let lastSpoken='',lastAt=0;
  const tick=()=>{
    const p=pose();if(!p)return setGuide({angle:0,deg:0,hint:'Waiting for the sensors'});
    const v=$('v'),g=guide(t,{yaw:p.yaw,pitch:p.pitch,roll:p.roll,width:v.videoWidth||540,height:v.videoHeight||960,hfov:world.hfov},{uncertaintyDeg:t.uncertainty});
    setGuide({angle:g.arrowDeg,deg:g.distanceDeg,hint:g.instruction,found:g.arrived,coneDeg:g.coneDeg,words:wordsFor(t,p)});
    const band=g.distanceDeg<6?0:g.distanceDeg<15?1:g.distanceDeg<30?2:3;if(band<lastBand&&band<3)buzz(band===0?40:12);lastBand=band;
    const now=performance.now();if(!g.arrived&&g.instruction!==lastSpoken&&now-lastAt>2500){lastSpoken=g.instruction;lastAt=now;speak(g.instruction)}};
  tick();guideT=setInterval(tick,100)}
function stopGuide(){clearInterval(guideT);guideT=null;clearInterval(gt);try{speechSynthesis.cancel()}catch{}}
async function openGuide(){if(!await useCamera('guide'))return;RENDER.guide('default',{result:find});startGuide()}
RENDER.guide=(state,d={})=>{if(d.result)find=d.result;txt('gobj',find?.concept||'Object');txt('ginfo',guideInfo());setState('guide',state);if(state==='found')setGuide({found:true,deg:0})};

/* Explore (AR): the room's remembered objects over the camera */
RENDER.explore=(state,d={})=>{txt('exroom',d.room?.name||roomLabel(S(),S().viewRoomId));setState('explore',state)};
function openExplore(roomId){
  store.set({viewRoomId:roomId});const r=roomById(S(),roomId);
  RENDER.explore(r?.scan_id?'default':'no-memory',{room:r});go('explore');if(r?.scan_id)wantCamera('explore')}

/* What changed: one room, two of its saved scans */
let chgRoom=null,chgRun=null;
const CHG_GLYPH={'need-two':'○','no-room':'○',offline:'OFF',error:'!'};
function chgNote(state,title,text,act){setState('changes',state);$('chgtitle').parentElement.dataset.g=CHG_GLYPH[state]||'!';$('chgtitle').parentElement.dataset.tone=state==='error'?'error':'';txt('chgtitle',title);txt('chgtext',text);const b=$('chgact');b.hidden=!act;if(act){b.textContent=act.label;chgRun=act.run}}
function fillPicker(d){
  const label=s=>`${new Date(s.started_at*1000).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})}${s.baseline?' · baseline':''}`;
  for(const[id,val]of[['chbefore',d.scan_before],['chafter',d.scan_after]]){const sel=$(id);sel.replaceChildren(...d.scans.map(s=>{const o=new Option(label(s),s.id);return o}));sel.value=String(val)}}
RENDER.changes=(state,d={})=>{
  const room=d.room;chgRoom=room?.id??null;setState('changes',state);
  txt('chgpill',room?room.name.toUpperCase():'WHAT CHANGED');
  if(state==='default'||state==='nothing-changed'){
    const c=d.data;fillPicker(c);
    const kids=[mk('div','grp panel sumline',c.summary)];
    for(const[k,icon,color,name]of[['moved','↔','var(--accent)','Moved'],['removed','−','var(--red)','Removed'],['added','+','var(--green)','New']]){
      if(!c[k].length)continue;const g=mk('div','grp panel'),h=mk('h2'),ic=mk('span','ic',icon);ic.style.background=color;h.append(ic,name,mk('span','n',c[k].length));g.append(h);
      for(const e of c[k])g.append(changeCard(k,e));kids.push(g)}
    if(c.unverified.length){const g=mk('div','grp panel unv'),h=mk('h2'),ic=mk('span','ic','?');h.append(ic,'Not checked',mk('span','n',c.unverified.length));g.append(h);
      for(const x of c.unverified)g.append(mk('p','dim',`${x.label}: ${x.reason}`));kids.push(g)}
    $('chg').replaceChildren(...kids);paintThumbs($('chg'));$('chg')._data=c}
  else if(state==='loading'||state==='slow'){txt('chgloadtitle',state==='slow'?'Still comparing…':'Comparing your scans…');txt('chgloadtext',d.message||'This usually takes a second.')}
  else if(state==='need-two')chgNote(state,'Nothing to compare yet',d.message||'Comparing needs two saved scans of the same room. Scan it, move or add something, then scan it again.',{label:'Scan this room',run:()=>openWizard({roomId:chgRoom})});
  else if(state==='no-room')chgNote(state,'Choose a room first','What changed compares two scans of one room. Pick the room, then come back.',{label:'Choose a room',run:openSwitcher});
  else if(state==='offline')chgNote(state,'Server not reachable',d.message||'Check that the computer is on. Reality OS keeps reconnecting by itself.',{label:'Try again',run:()=>openChanges(chgRoom)});
  else chgNote('error','The server could not compare the scans',d.message||'',{label:'Try again',run:()=>openChanges(chgRoom)})};
async function openChanges(roomId=null,{before,after}={}){
  const id=roomId??S().activeId;go('changes');
  if(id==null)return RENDER.changes('no-room',{});
  const room=roomById(S(),id);RENDER.changes('loading',{room});
  if(demo){const f=fixture('changes','default');return RENDER.changes('default',{...f,room:room||f.room})}
  const slow=setTimeout(()=>{if(scrn==='changes')RENDER.changes('slow',{room,message:'The server is slower than usual. You can wait, or go back and try later.'})},8000);
  const r=await api.changes(id,before,after);clearTimeout(slow);
  if(scrn!=='changes'||chgRoom!==id)return;
  if(!r.ok){const f=describeFailure(r,'compare the scans');return RENDER.changes(f.kind==='offline'?'offline':'error',{room,message:f.text})}
  const d=r.data;
  if(d.status==='need_two_scans')return RENDER.changes('need-two',{room,message:d.message});
  if(d.status!=='ok')return RENDER.changes('error',{room,message:d.message||d.status});
  const empty=!d.moved.length&&!d.removed.length&&!d.added.length&&!d.unverified.length;
  RENDER.changes(empty?'nothing-changed':'default',{room,data:d});speak(d.summary);buzz(25)}
async function reviewChange(key,state){ // confirm or dismiss one change (an empty state clears the mark)
  const c=$('chg')._data;if(!c)return;
  if(!demo){const r=await api.review(chgRoom,c.scan_before,c.scan_after,key,state||null);if(!r.ok)return toast(describeFailure(r,'save that').text)}
  for(const k of['moved','removed','added'])for(const e of c[k])if(e.key===key)e.review=state||null;
  RENDER.changes('default',{room:roomById(S(),chgRoom),data:c})}

/* Objects review (the simple phone version of the dashboard inspector): labels, then instances, then sightings */
let insp={scanId:null,roomId:null,label:null,objectId:null,data:null,detail:null,picked:new Set()};
RENDER.labels=(state,d={})=>{
  setState('labels',state);
  if(state==='error'||state==='offline'){txt('lberrtitle',state==='offline'?'Server not reachable':'Could not load the objects');txt('lberr',d.message||'')}
  if(state==='default'||state==='empty'){
    const c=d.data;insp.data=c;insp.scanId=c.scan.id;insp.roomId=c.scan.room_id;
    txt('lbpill',((c.scan.room_name||'Objects')+' · #'+c.scan.id).toUpperCase());
    txt('lbsum',`${c.instances} object${c.instances===1?'':'s'} · ${c.sightings} sightings${c.scan.status==='draft'?' · unsaved scan':''}`);
    $('lbrows').replaceChildren(...c.labels.map(labelRow));
    $('lbhidden').replaceChildren(...(c.hidden_labels.length?[mk('h2','sec','Not an object in this room'),...c.hidden_labels.map(h=>{const r=mk('div','srow'),t=mk('div');t.append(h.label,mk('small',null,h.sightings+' sightings hidden'));r.append(t,mk('button','mini','Restore',{'data-act':'unmark-object','data-label':h.label}));return r})]:[]));
    txt('lbdups',c.possible_duplicates?`${c.possible_duplicates} possible duplicate${c.possible_duplicates===1?'':'s'}: merge instances of a label here, or review them with pictures on the laptop dashboard.`:'')}};
async function openLabels(scanId){
  if(scanId==null)return toast('There is no scan to review.');
  insp.scanId=scanId;go('labels');RENDER.labels('loading');
  if(demo){const f=fixture('labels','default');return RENDER.labels('default',f)}
  const r=await api.scanObjects(scanId);if(scrn!=='labels')return;
  if(!r.ok){const f=describeFailure(r,'load the objects');return RENDER.labels(f.kind==='offline'?'offline':'error',{message:f.text})}
  RENDER.labels(r.data.labels.length?'default':'empty',{data:r.data})}
async function refreshInspect(){ // after a correction: the scan's objects as they are now
  if(demo)return;
  const r=await api.scanObjects(insp.scanId);if(r.ok)insp.data=r.data;
  loadRooms()}
RENDER.instances=(state,d={})=>{
  if(d.data){insp.data=d.data;insp.scanId=d.data.scan.id;insp.roomId=d.data.scan.room_id}
  if(d.label!==undefined)insp.label=d.label;
  const g=insp.data?.labels.find(x=>x.label===insp.label);
  if(state==='default'&&!g)state='empty';
  setState('instances',state);
  if(state==='error')txt('inerr',d.message||'');
  if(state!=='default')return;
  insp.picked=new Set([...insp.picked].filter(id=>g.objects.some(o=>o.id===id)));
  txt('inpill',(insp.label||'').toUpperCase());txt('insum',`${g.instances} instance${g.instances===1?'':'s'} · ${g.sightings} sightings. Tick two or more to merge them into one object.`);
  $('mergebtn').textContent=`Merge selected (${insp.picked.size})`;$('mergebtn').disabled=insp.picked.size<2;
  txt('notobjbtn',`“${g.label}” is not an object here`);
  $('inrows').replaceChildren(...g.objects.map(o=>instanceRow(o,insp.picked.has(o.id))));paintThumbs($('inrows'))};
async function openInstances(label){
  insp.label=label;insp.picked=new Set();go('instances');
  if(!insp.data){RENDER.instances('loading');const r=await api.scanObjects(insp.scanId);if(!r.ok)return RENDER.instances('error',{message:describeFailure(r,'load the objects').text});insp.data=r.data}
  RENDER.instances('default',{})}
RENDER.sightings=(state,d={})=>{
  setState('sightings',state);
  if(state==='error')txt('sierr',d.message||'');
  if(state!=='default')return;
  const x=d.detail;insp.detail=x;insp.objectId=x.object.id;const o=x.object;
  txt('sipill',(o.label+' #'+o.id).toUpperCase());
  $('sifacts').replaceChildren(...[['Direction',`${Math.round(o.yaw)}° / ${Math.round(o.pitch)}°`],['Uncertainty',`±${o.uncertainty_deg}°`],['Sightings',x.sightings.length],['Confidence',Math.round(o.avg_confidence*100)+'%']].map(([k,v])=>{const c=mk('div');c.append(mk('span','label',k),mk('b',null,String(v)));return c}));
  $('sirows').replaceChildren(...x.sightings.map(s=>sightingCard(s,x.sightings.length>1)));paintThumbs($('sirows'))};
async function openSightings(objectId){
  insp.objectId=objectId;go('sightings');RENDER.sightings('loading');
  if(demo)return RENDER.sightings('default',fixture('sightings','default'));
  const r=await api.object(objectId);if(scrn!=='sightings')return;
  if(!r.ok)return RENDER.sightings('error',{message:describeFailure(r,'load this object').text});
  RENDER.sightings('default',{detail:r.data})}
async function correct(call,done){ // every correction goes to the server, which records it; then the lists are refreshed
  if(demo){toast('Done (demo)');return}
  const r=await call();if(!r.ok)return toast(describeFailure(r,'do that').text);
  await refreshInspect();done(r.data)}
async function mergePicked(){
  const ids=[...insp.picked];if(ids.length<2)return;
  await correct(()=>api.mergeObjects(ids),r=>{insp.picked=new Set();toast(`Merged into one ${r.label}.`);RENDER.instances('default',{})})}
async function notObjectHere(){
  const label=insp.label;
  if(!await confirmDlg({title:`“${label}” is not an object here?`,body:`Every ${label} in this room, in all its scans and in future scans, is hidden. You can restore it from the labels list.`,yes:'Hide it',no:'Keep it'}))return;
  await correct(()=>api.markNotObject(insp.roomId,label),()=>{toast(`“${label}” is no longer treated as an object here.`);openLabels(insp.scanId)})}
async function restoreLabel(label){await correct(()=>api.unmarkNotObject(insp.roomId,label),()=>{toast(`Restored “${label}”.`);openLabels(insp.scanId)})}
async function deleteObjectHere(){
  const o=insp.detail?.object;if(!o)return;
  if(!await confirmDlg({title:`Delete ${o.label} #${o.id}?`,body:`Its ${insp.detail.sightings.length} sighting(s) are ignored from now on. This is recorded as a correction.`,yes:'Delete',no:'Keep it'}))return;
  await correct(()=>api.deleteObject(o.id),()=>{toast(`Deleted ${o.label} #${o.id}.`);const still=insp.data?.labels.some(g=>g.label===insp.label);goBack();if(!still)goBack()})}
async function detachHere(id){
  await correct(()=>api.detachSighting(id),async r=>{toast(`Detached into its own object (#${r.object_id}).`);const x=await api.object(insp.objectId);if(x.ok)RENDER.sightings('default',{detail:x.data})})}

/* Settings (global) and room settings */
const dbgEnabled=()=>P.has('debug')&&prefs.get('debug');
function applyDebug(){body.classList.toggle('dbg',dbgEnabled());for(const b of $('dbgseg').children)b.setAttribute('aria-pressed',String((b.dataset.v==='1')===dbgEnabled()))}
function seg(id,opts,curv,set){$(id).replaceChildren(...opts.map(([v,l])=>{const b=mk('button',null,l,{'data-v':v});b.setAttribute('aria-pressed',String(String(curv)===String(v)));b.onclick=()=>{set(v);settingsSegs()};return b}))}
function settingsSegs(){
  seg('kfseg',KFPS.map(n=>[n,n+' / s']),prefs.get('kfps'),v=>{prefs.set('kfps',+v);if(ctl)ctl.policy.minIntervalMs=prefs.minIntervalMs()});
  seg('detailseg',DETAILS,prefs.get('detail'),v=>prefs.set('detail',v))}
RENDER.settings=(state,d={})=>{
  $('dbgrow').hidden=!P.has('debug');applyDebug();settingsSegs();
  const s=$('langsel');if(!s.children.length)for(const[v,l]of LANGS){const o=mk('option',null,l);o.value=v;s.append(o)}s.value=prefs.get('lang');
  setState('settings',state);if(state==='error')txt('seterr',d.message||'');
  const rooms=S().rooms,box=$('setrooms');
  if(state==='default')box.replaceChildren(...(rooms.length?rooms.map(r=>{const row=mk('div','srow'),t=mk('div');t.append(r.name+(r.active?' · active':''),mk('small',null,roomStatsLine(r)));
    const b=mk('button','mini','Edit',{'data-act':'open-room-settings','data-id':r.id,'aria-label':'Settings for '+r.name});row.append(t,b);return row}):[mk('p','dim','No rooms yet.')]))};
async function openSettings(){
  go('settings');RENDER.settings(S().roomsLoaded?'default':'loading');
  const r=await loadRooms();if(scrn!=='settings')return;
  if(r.ok)RENDER.settings('default');else RENDER.settings('error',{message:describeFailure(r,'load your rooms').text})}
async function deleteAll(){
  const n=S().rooms.length;
  if(!await confirmDlg({title:'Delete all data?',body:`${n?`All ${n} room${n===1?'':'s'}, every scan, photo and object`:'Every scan, photo and object'} on the computer running Reality OS will be erased. This cannot be undone.`,yes:'Delete everything',no:'Keep my data',items:[plural(n,'room'),plural(S().rooms.reduce((a,r)=>a+r.n_scans+(r.n_drafts||0),0),'scan'),plural(S().rooms.reduce((a,r)=>a+r.n_objects,0),'object'),'every photo']}))return;
  if(!demo){const r=await api.deleteAll();if(!r.ok)return toast(describeFailure(r,'delete the data').text);store.set({rooms:[],recent:[],activeId:null,scan:null})}
  toast('All data deleted');openHome()}
RENDER.roomsettings=(state,d={})=>{
  const r=roomById(S(),S().viewRoomId);if(!r)return;
  $('rsname').value=r.name;setState('roomsettings',state);if(state==='error')txt('rserr',d.message||'');
  txt('rsactivetext',r.active?'This is your active room: Find, Explore and the Reality API use it by default.':'Make this your active room so Find, Explore and the Reality API use it by default.');$('rsact').hidden=r.active;
  const n=r.n_scans+(r.n_drafts||0);txt('rsdeltext',`Erases ${n} scan${n===1?'':'s'}, ${r.n_objects} remembered object${r.n_objects===1?'':'s'} and every photo in ${r.name}. This cannot be undone.`)};
function openRoomSettings(id){store.set({viewRoomId:id});RENDER.roomsettings('default',{});go('roomsettings')}
async function renameRoom(){
  const id=S().viewRoomId,name=$('rsname').value.trim();
  if(demo){toast('Saved (demo)');return}
  const r=await api.patchRoom(id,{name});
  if(!r.ok)return RENDER.roomsettings('error',{message:describeFailure(r,'rename the room').text});
  await loadRooms();RENDER.roomsettings('default',{});toast('Renamed to '+name)}
async function deleteRoom(){
  const r=roomById(S(),S().viewRoomId);if(!r)return;
  const n=r.n_scans+(r.n_drafts||0);
  if(!await confirmDlg({title:`Delete ${r.name}?`,body:`This erases ${n} scan${n===1?'':'s'}, ${r.n_objects} remembered object${r.n_objects===1?'':'s'} and every photo in ${r.name}. Other rooms are not touched. This cannot be undone.`,yes:'Delete room',no:'Keep it',items:[plural(n,'scan'),plural(r.n_objects,'object'),'every photo in '+r.name]}))return;
  if(!demo){const x=await api.deleteRoom(r.id);if(!x.ok)return toast(describeFailure(x,'delete the room').text);await loadRooms()}
  toast(`${r.name} deleted`);openHome()}
function confirmDlg({title,body,yes='OK',no='Cancel',items=[]}){ // items: what is erased, with counts, listed above the buttons
  return new Promise(res=>{txt('dlgtitle',title);txt('dlgbody',body);txt('dlgyes',yes);txt('dlgno',no);const ul=$('dlgitems');ul.replaceChildren(...items.map(t=>mk('li',null,t)));ul.hidden=!items.length;
    const d=$('dlg');d.classList.add('on');const done=v=>{d.classList.remove('on');$('dlgyes').onclick=$('dlgno').onclick=null;res(v)};
    $('dlgyes').onclick=()=>done(true);$('dlgno').onclick=()=>done(false)})}

/* Intro and primer */
RENDER.welcome=()=>{
  const c=ob.card;$('introcard').replaceChildren(mk('div','ic',c.icon),mk('h1',null,c.title),...c.body.map(t=>mk('p',null,t)));
  $('introdots').replaceChildren(...CARDS.map((_,i)=>mk('i',i===ob.index?'on':null)));
  txt('intronext',ob.nextLabel);$('introback').style.visibility=ob.index?'visible':'hidden';setState('welcome','default')};
RENDER.primer=(state,d={})=>{
  const problem=d.problem;txt('primertitle',PRIMER.title);txt('primerintro',PRIMER.intro);txt('primernote',PRIMER.note);
  $('primeritems').replaceChildren(...PRIMER.items.map(i=>{const r=mk('div','ritem'),t=mk('div');t.append(mk('b',null,i.name),mk('span',null,i.why));r.append(mk('div','ic',i.id==='camera'?'◉':'↻'),t);return r}));
  primerCanAsk=!problem||problem.retry!==false;setState('primer',problem?'denied':'default');
  if(problem){txt('problemtitle',problem.title);txt('problembody',problem.body)}
  txt('primergo',problem?(primerCanAsk?'Try again':'Close'):'Continue');$('primergo').disabled=false};
function introNext(){
  if(!ob.last){ob.next();return RENDER.welcome()}
  const replay=ob.replay;ob.markSeen();if(replay)return go('settings');
  pendingAction=null;prefs.primed?openHome():openPrimer()}
async function primerGo(){
  if(!primerCanAsk){pendingAction=null;return openHome()}
  const b=$('primergo');b.disabled=true;b.textContent='Waiting for your answer…';
  const r=demo?{ok:true}:await requestAccess({nav:navigator,win:window,secure:window.isSecureContext});
  if(r.ok){prefs.set('primed',true);const a=pendingAction;pendingAction=null;if(a)return runAction(a);openHome();return toast('Allowed. Tap Scan to begin.')}
  openPrimer(r.problem)}

/* ---------- one handler for every tap: data-act names the action ---------- */
const rescanTarget=()=>scrn==='result'?find?.room_id:scrn==='find'?findRoom()?.id:S().viewRoomId;
const ACT={
  nav:b=>({home:openHome,find:()=>openFind(),scan:()=>openWizard(),changes:()=>openChanges(),settings:openSettings})[b.dataset.nav](),
  back:goBack,retry:()=>{poll?.retryNow({refresh:true});({home:openHome,room:()=>openRoom(S().viewRoomId),settings:openSettings,changes:()=>openChanges(chgRoom)})[scrn]?.()},
  switcher:openSwitcher,'close-switcher':closeSwitcher,
  'activate-room':async b=>{closeSwitcher();if(await setActive(+b.dataset.id))reopen(['find','changes','room','home','settings'].includes(scrn)?scrn:'home')},
  'add-room':()=>{closeSwitcher();openWizard({fresh:true})},
  'open-room':b=>openRoom(+b.dataset.id),
  'filter-room':b=>{store.set({filterRoomId:b.dataset.id?+b.dataset.id:null});RENDER.home(homeState());runSearch()},
  'open-object':b=>openObject(b.dataset.label,+b.dataset.room),
  'pick-room':b=>{wz.selectedId=+b.dataset.id;wz.name='';$('wzname').value='';wizardRefresh('rescan')},
  'name-chip':b=>{wz.name=b.dataset.name;wz.selectedId=null;$('wzname').value=wz.name;wizardRefresh('new')},
  'toggle-active':()=>{wz.active=!wz.active;$('wzactive').setAttribute('aria-pressed',String(wz.active))},
  'wizard-go':wizardGo,
  'toggle-people':b=>b.setAttribute('aria-pressed',String(b.getAttribute('aria-pressed')!=='true')),
  'scan-start':startScan,'scan-cancel':cancelScan,'scan-done':()=>{if(demo)return demoDone();ctl.done()},
  stop:()=>{if(scrn==='scan')return cancelScan();go(scrn==='explore'?'room':scrn==='guide'?'result':'home')},
  'save-review':saveReview,'discard-review':discardReview,'view-room':()=>openRoom(summary?.room_id??S().scan?.roomId),
  'toggle-group':b=>toggleTile(b.dataset.label),noop:()=>{},
  'proc-check':recoverSummary,'proc-home':()=>{openHome();toast('The scan keeps processing on the server. Its result shows in the room.');poll?.retryNow()},
  'find-room':()=>openFind({roomId:S().viewRoomId}),rescan:()=>openWizard({roomId:rescanTarget()}),
  'changes-room':()=>openChanges(S().viewRoomId),'explore-room':()=>openExplore(S().viewRoomId),'room-settings':()=>openRoomSettings(S().viewRoomId),
  'activate-this':async()=>{if(await setActive(S().viewRoomId)){if(scrn==='room')openRoom(S().viewRoomId);else RENDER.roomsettings('default',{})}},
  'save-scan':b=>scanAction('save-scan',+b.dataset.scan),'discard-scan':b=>scanAction('discard-scan',+b.dataset.scan),'delete-scan':b=>scanAction('delete-scan',+b.dataset.scan),
  'set-baseline':b=>scanAction('set-baseline',+b.dataset.scan),'clear-baseline':b=>scanAction('clear-baseline',+b.dataset.scan),
  scope:b=>{store.set({scope:b.dataset.v});findDone();$('fgroups').replaceChildren();RENDER.find('idle',{labels:[],scope:b.dataset.v});idleFind()},
  'find-chip':b=>{closeSwitcher();$('q').value=b.dataset.label;doFind(b.dataset.label)},'open-quick':()=>section('quick').classList.add('on'),'find-go':()=>doFind(),mic:startMic,'ask-act':()=>askRun&&askRun(),
  'open-group':b=>openGroupResult(+b.dataset.room),
  'find-all-rooms':()=>{const q=find?.query||'';openFind({scope:'all'});$('q').value=q;if(q)doFind(q,true)},'find-again':()=>openFind(),
  'guide-me':guideMe,'guide-end':()=>{stopGuide();go('result')},'guide-done':()=>{stopGuide();world.highlight(null);openHome()},'toggle-ar':()=>setAr(!arOn),
  review:b=>reviewChange(b.dataset.key,b.dataset.state||null),'changes-act':()=>chgRun&&chgRun(),
  'intro-next':introNext,'intro-back':()=>{ob.back();RENDER.welcome()},'intro-skip':()=>{const r=ob.replay;ob.markSeen();r?go('settings'):openHome()},
  'primer-go':primerGo,'primer-later':()=>{pendingAction=null;openHome()},
  'replay-intro':()=>openIntro(true),'delete-all':deleteAll,'open-room-settings':b=>openRoomSettings(+b.dataset.id),'rename-room':renameRoom,'delete-room':deleteRoom,
  'inspect-scan':b=>openLabels(+(b.dataset.scan||summary?.scan_id)||null),'open-label':b=>openInstances(b.dataset.label),'open-instance':b=>openSightings(+b.dataset.id),
  'pick-instance':b=>{const id=+b.dataset.id;insp.picked.has(id)?insp.picked.delete(id):insp.picked.add(id);RENDER.instances('default',{})},
  'merge-picked':mergePicked,'not-object':notObjectHere,'unmark-object':b=>restoreLabel(b.dataset.label),'delete-object':deleteObjectHere,'detach-sighting':b=>detachHere(+b.dataset.id),
  debug:b=>{prefs.set('debug',b.dataset.v==='1');applyDebug()},
};
document.addEventListener('click',e=>{const b=e.target.closest('[data-act]');if(!b||b.disabled)return;const f=ACT[b.dataset.act];if(f){e.preventDefault();f(b)}});
$('q').onkeydown=e=>e.key==='Enter'&&doFind();
$('langsel').onchange=e=>{prefs.set('lang',e.target.value);toast('Voice language: '+e.target.selectedOptions[0].textContent)};
$('chbefore').onchange=$('chafter').onchange=()=>openChanges(chgRoom,{before:+$('chbefore').value,after:+$('chafter').value});
$('cap').onclick=()=>$('cap').style.display='none';
// dev (?debug): flip the camera, stream frames to the server (echo only) and save full-size samples with their orientation (M0 measurements)
$('flipbtn').onclick=async()=>{if(!session)return;facing=facing==='environment'?'user':'environment';if(session.busy){session.release('flip');await useCamera(scrn)}};
$('streambtn').onclick=()=>{
  if(!sock)return;if(streaming){clearInterval(streaming);streaming=null;$('streambtn').textContent='Stream';return}
  $('streambtn').textContent='Stop';
  streaming=setInterval(async()=>{if(!sock.ready||sock.backlog>512*1024)return;const b=await cam.grabJpeg($('v'),960,.8);if(b)sock.send(b)},500)};
$('savebtn').onclick=async()=>{
  if(!sock||!sock.ready)return toast('Not connected to the server');
  if(!session?.active)return toast('The camera is off. Open a camera screen first.');
  const b=await cam.grabJpeg($('v'),1920,.9);if(!b)return;
  await sock.send(b,{save:true,meta:{orientation:orient.snapshot(),camera:camMeta()}});toast('Sample saved')};

/* ---------- pose, AR overlay, render loop ---------- */
function pose(){
  if(demo)return{yaw:cur.yaw,pitch:cur.pitch,roll:0};
  const s=orient&&(orient.latest.absolute||orient.latest.relative);if(!s)return null;
  return{yaw:s.yaw,pitch:s.pitch,roll:(s.roll||0)+(window.screen.orientation?.angle||0)}}
const viewSize=()=>{const v=$('v');return demo||!v.videoWidth?{width:1080,height:1920}:{width:v.videoWidth,height:v.videoHeight}};
let fpsT=[],drawMs=0,lastInfo=0,arStats={drawn:0,hidden:0};
const fps=()=>{const n=fpsT.length;return n>1?(n-1)*1000/(fpsT[n-1]-fpsT[0]):0};
window.__ar={fps,stats:()=>({fps:fps(),drawMs,...arStats,objects:world.objects.length,hfov:world.hfov,calibrated:world.calibrated})};
function setAr(on){arOn=on;prefs.set('ar',on);for(const b of[$('artog'),$('artog2')]){b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on))}syncWorld()}
let last=performance.now();
function frame(now){
  const dt=Math.min(.05,(now-last)/1000);last=now;fpsT.push(now);while(fpsT.length>90)fpsT.shift();
  const p=demo?null:pose();if(p){cur.tyaw=p.yaw;cur.tpitch=p.pitch;cur.roll=p.roll}
  const k=demo?Math.min(1,dt*(dragging?30:10)):1; // the overlay must follow the live pose exactly, or objects swim
  cur.yaw=(cur.yaw+nd(cur.tyaw-cur.yaw)*k+360)%360;cur.pitch+=(cur.tpitch-cur.pitch)*k;
  cur.pulse=Math.max(0,cur.pulse-dt*2.5);cur.shutter+=dt/.5;
  if(isCamera(scrn)){
    const ar=$('ar'),tg=guideTarget(),sc=scrn==='scan',t0=performance.now(),objs=world.objects;
    const s={mode:scrn==='explore'?'home':scrn,now,yaw:cur.yaw,pitch:cur.pitch,roll:cur.roll,view:{...viewSize(),hfov:world.hfov},world:objs,showWorld:arOn&&!camAlarm,detail:prefs.get('detail'),
      bins:sc?cur.bins:null,gapBins:sc&&cur.gap?new Set(Array.from({length:cur.gap.len},(_,i)=>(cur.gap.start+i)%36)):null,gap:sc?cur.gap:null,
      markers:scrn==='guide'?(tg?[{yaw:tg.yaw,hl:true}]:[]):objs,target:tg,found,label:tg?.label,pulse:cur.pulse,shutter:cur.shutter,tapeY:M.tapeY,top:M.top,bot:M.bot};
    drawAR(ar,s);arStats=s.stats;drawMs+=((performance.now()-t0)-drawMs)*.1}
  if(body.classList.contains('dbg')&&now-lastInfo>500){lastInfo=now;const s=window.__ar.stats(),c=session?session.state:null;
    txt('arinfo',`AR ${arOn?'on':'off'} · ${s.fps.toFixed(0)} fps · draw ${s.drawMs.toFixed(1)} ms · ${s.drawn} shown, ${s.hidden} hidden · FOV ${s.hfov}°${s.calibrated?'':' (not calibrated)'}${c?` · camera ${c.camera}, wake ${c.wake?'on':'off'}, portrait ${c.portrait?'locked':'free'}`:''}`)}
  if(scrn==='scan'&&section('scan').dataset.state==='live')drawRing($('ring'),cur.bins,cur.yaw,cur.pulse,{hfov:world.hfov,bands:cur.bands,pitch:cur.pitch}); // hidden (zero size) while the checklist shows
  if(scrn==='result'&&find?.replay&&find.found!==false){rt+=dt*1.6;drawReplay($('replay'),img,find.replay,rt)}
  if(scrn==='result'&&find?.found!==false){const o=find?.object||{};drawBearing($('bear'),o.yaw||0,o.pitch||0,o.uncertainty_deg||0)}
  requestAnimationFrame(frame)}
requestAnimationFrame(frame);
let dragging=false,px=0,py=0;const ppd=()=>innerWidth/60; // drag to look around (demo / desktop preview)
addEventListener('pointerdown',e=>{if(!demo||!isCamera(scrn)||e.target.closest('button,input,summary,details,select'))return;dragging=true;px=e.clientX;py=e.clientY});
addEventListener('pointermove',e=>{if(!dragging||scrn==='scan')return;cur.tyaw=(cur.tyaw-(e.clientX-px)/ppd()+360)%360;cur.tpitch=Math.max(-70,Math.min(70,cur.tpitch+(e.clientY-py)/ppd()));px=e.clientX;py=e.clientY});
addEventListener('pointerup',()=>dragging=false);
window.__scan=()=>ctl;window.__ui={cur,go,world,prefs,store,say:speak,screen:()=>scrn,session:()=>session,show:(id,state)=>showFixture(id,state),RENDER};

/* ---------- boot ---------- */
// Opening the page only builds the screens and starts the server check. The camera, the motion sensors and the microphone
// stay untouched until a tap asks for them (tests/test_entry_flow.py fails if that ever changes).
async function boot(){
  setAr(arOn);applyDebug();renderConn();
  if(demo){body.classList.add('demo');return startDemo()}
  try{
    const [c,w,o,k,s]=await Promise.all(['camera','ws','orientation','keyframe','scan'].map(n=>import('/js/'+n+'.js')));
    cam=c;orient=new o.OrientationTracker();
    session=new CameraSession({video:$('v'),startCamera:c.startCamera,orient,onState:onCamState,onHidden,onEnded:onCamEnded});
    sock=new w.FrameSocket(r=>{
      if(r.type==='find_result'){if(!findPending)return;findDone();return onFindReply(r)}
      if(r.type==='guide_request')return handleEvent(r);
      if(r.type==='error'&&r.where==='find')return findFailed(r.message);
      if(r.type)return ctl.onEvent(r)},onSockState);
    ctl=new s.ScanController({video:$('v'),orient,sock,grabJpeg:c.grabJpeg,camera:camMeta,onChange,fov:()=>world.hfov,guard,policy:{minIntervalMs:prefs.minIntervalMs()}});
    watchCamera();
    poll=new StatusPoller({onUpdate:onStatus});poll.start();
    document.addEventListener('visibilitychange',()=>{if(document.hidden)poll.stop();else poll.start()}); // no polling in the background
    measure();openHome();
    if(showIntroOnStart(prefs))openIntro();
  }catch(e){console.error('boot failed, using the demo:',e);demo=true;body.classList.add('demo');toast('Backend not found, demo mode');startDemo()}}

/* ---------- demo: /?demo&screen=ID&state=STATE shows a screen from fixtures.js with the same render code ---------- */
const demoBase=()=>{const b=fixture('home','default');store.set({rooms:b.rooms,recent:b.recent,activeId:1,roomsLoaded:true})};
function demoWorld(){ // what the overlay shows in demo mode; /?demo&screen=explore&objects=60 makes a crowded room for measuring
  const blob=(rx,ry)=>Array.from({length:40},(_,i)=>{const a=i/40*Math.PI*2;return[270+Math.cos(a)*rx*(1+.12*Math.sin(a*3)+.03*Math.sin(a*11)),480+Math.sin(a)*ry*(1+.12*Math.cos(a*2)+.03*Math.cos(a*9))]});
  const all=[['laptop',-20,5],['laptop',35,0],['laptop',200,8],['cable',60,-25],['cable',-75,-30],['cable',150,-20],['bottle',95,-5],['bottle',250,0],['charger',-60,-10],['mug',120,2],['keys',300,-15],['phone',10,-12],['monitor',-5,20],['lamp',330,35],['wall',20,0],['ceiling',90,70]];
  const kinds=['laptop','cable','bottle','mug','keys','phone','book','pen','lamp','plant','remote','wall'],want=+P.get('objects')||0;
  for(let k=all.length;k<want;k++)all.push([kinds[k%kinds.length],(k*47)%360-180,((k*29)%50)-25]);
  world.apply({hfov_deg:P.has('fov')?+P.get('fov'):60,fov_calibrated:false,scan_id:3,objects:all.map(([label,yaw,pitch],i)=>({id:i+1,label,yaw:(yaw+360)%360,pitch,uncertainty_deg:i===5?30:i%4,n_obs:3,confidence:i%6===4?.35:i<16?.9:.5+((i*13)%50)/100,
    age_s:i<14?[5,5,20,400,900,5,60,5,5,5,5,5,5,5][i]:(i*37)%900,footprint:[(yaw+360)%360,-7,7,pitch-6,pitch+6],mask:i%2?null:{poly:blob(60+(i%7)*3,46),yaw:(yaw+360)%360,pitch,roll:0,width:540,height:960,hfov:60}}))})}
function showFixture(id,state){
  state=state&&hasState(id,state)?state:SCREENS[id].states[0];const d=fixture(id,state)||{};demoBase();
  const viewId=d.room?.id??1;store.set({viewRoomId:viewId,findRoomId:null});
  switch(id){
    case'scan':RENDER.scan(state,d);ready=state==='ready';go('scan');if(state==='ready')startLight();else demoScan(true);break;
    case'welcome':openIntro();break;
    case'primer':openPrimer(d.problem);break;
    case'switcher':go('home');RENDER.home('default',fixture('home','default'));if(state==='empty')store.set({rooms:[]});openSwitcher();break;
    case'guide':find=d.result;go('guide');RENDER.guide(state,d);if(state==='default')startGuide();break;
    case'explore':RENDER.explore(state,d);go('explore');break;
    case'labels':go('labels');RENDER.labels(state,d);break;
    case'instances':go('instances');RENDER.instances(state,d);break;
    case'sightings':go('sightings');RENDER.sightings(state,d);break;
    case'quick':go('find');RENDER.find('idle',{labels:d.labels,scope:'room'});setState('quick',state);section('quick').classList.add('on');break;
    case'summary':go('summary');RENDER.summary(state,d);break;
    case'result':RENDER.result(state,d);break;
    case'home':if(['loading','error','offline','slow'].includes(state))store.set({roomsLoaded:false});if(state==='empty')store.set({rooms:[],recent:[],activeId:null});go('home');RENDER.home(state,d);break;
    case'find':store.set({scope:d.scope||'room',findRoomId:d.room?.id??null,...(d.room===null?{activeId:null}:{})});go('find');RENDER.find(state,d);break;
    case'changes':if(d.room===null)store.set({activeId:null});go('changes');RENDER.changes(state,d);break;
    default:go(id);RENDER[id](state,d)}}
function startDemo(){
  demoWorld();demoBase();renderConn();
  pollView=describe({ok:true,status:{server:true,api:{state:'ok',message:''}}});renderConn();renderSwitchers();measure();
  if(P.has('yaw'))cur.tyaw=cur.yaw=+P.get('yaw');if(P.has('pitch'))cur.tpitch=cur.pitch=+P.get('pitch');
  const id=P.get('screen')||'home';if(!SCREENS[id]){go('home');RENDER.home('default',fixture('home','default'));return}
  showFixture(id,P.get('state'))}
let st=null;
function demoScan(preview){
  clearInterval(st);ready=false;go('scan');setState('scan','live');Object.assign(cur,{bins:Array(36).fill(false),frac:0,sent:0,tyaw:0,yaw:0,tpitch:0,pitch:0,gap:null});
  const hints=['Slow down','Hold steady','Keep turning','Gap 40° to the left','Almost there'];let n=0,y=0;
  const names=[['laptop'],['mouse','cable'],['mug'],['bottle'],['charger'],['keys'],['phone'],['cable'],['lamp']];
  st=setInterval(()=>{n++;y=(y+3.6)%360;if(y>150&&y<200)y+=30;const b=cur.bins.slice();for(let i=0;i<36;i++)if(Math.abs(nd(i*10+5-y))<7)b[i]=true;
    const fr=b.filter(Boolean).length/36,show=n%3===0;cur.tyaw=y;
    onChange({phase:'scanning',sent:show?cur.sent+1:cur.sent,objects:Math.min(14,Math.floor(fr*16)),names:show?names[(n/3|0)%names.length]:[],hint:fr>.85?hints[4]:'',status:'frame '+cur.sent+' stored'},{bins:b,fraction:fr,bands:{low:{fraction:fr*.3},mid:{fraction:fr},high:{fraction:fr*.4}}});
    if(fr>=.98||preview&&n>100)clearInterval(st)},80)}
function demoDone(){clearInterval(st);go('summary');RENDER.summary('processing',{progress:{fraction:.75,text:'Reading frames: 18 of 24',level:'ok'}});setTimeout(()=>RENDER.summary('review',fixture('summary','review')),1600)}
function demoFind(q,everywhere){
  const labels=fixture('home','default').recent.map(o=>o.label),l=labels.find(x=>q.toLowerCase().includes(x));
  if(everywhere){const f=fixture('find',l?'all-results':'all-none');return RENDER.find(l?'all-results':'all-none',{...f,query:q})}
  if(!l)return RENDER.result(null,{...fixture('result','notfound'),query:q,concept:q.replace(/^where is (my )?/i,'')});
  const m=fixture('result','found');RENDER.result(null,{...m,query:q,concept:l,speech:`The ${l} was last seen near the middle of the room.`,object:{...m.object,label:l}})}
function demoGuide(){ // the real guide() against the dragged pose, so the demo behaves like the phone
  clearInterval(gt);const t=guideTarget();if(!t)return;
  gt=setInterval(()=>{const g=guide(t,{yaw:cur.yaw,pitch:cur.pitch,roll:0,width:540,height:960,hfov:world.hfov},{uncertaintyDeg:t.uncertainty});setGuide({angle:g.arrowDeg,deg:g.distanceDeg,hint:g.instruction,found:g.arrived,coneDeg:g.coneDeg,words:wordsFor(t,{yaw:cur.yaw,pitch:cur.pitch})})},100)}
boot();
