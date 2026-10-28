import {drawRing,drawMap,drawReplay,drawPhoto} from './ui.js';
import {drawAR,drawBearing,nd} from './ar.js';
import {guide} from './guide.js';
import {WorldLayer} from './world.js';
import {diffCalibration} from './camera-lock.js';
import {Prefs,KFPS,LANGS} from './prefs.js';
import {Onboarding,CARDS,PRIMER,cameraGate,showIntroOnStart} from './onboarding.js';
import {requestAccess,classifyCameraError,MOTION_DENIED} from './permissions.js';
import {CameraSession,RELEASED} from './camera-session.js';
import {coachHint,lightVerdict,meanLuma,processing} from './coach.js';
import {StatusPoller,describe,fetchJson} from './connection.js';
import {explainNotFound,ago} from './explain.js';
const $=id=>document.getElementById(id),el=$,P=new URLSearchParams(location.search);
const body=document.body;let demo=P.has('demo'),scrn='home';
const prefs=new Prefs();
// Nothing in this file touches the camera, the motion sensors or the microphone while the page loads. They start only from a
// tap on New scan, Find something, Guide me (or the permission primer's button), through CameraSession.acquire().
const CAM_SCREENS=new Set(['ask','scan','guide']);
const cur={yaw:0,tyaw:0,pitch:0,tpitch:0,roll:0,bins:Array(36).fill(false),frac:0,pulse:0,sent:0,shutter:9,gap:null};
let summary=null,shownSummary=null,find=null,img=null,rt=0,mapT=0,arrowA=0,M={top:150,bot:200,tapeY:70};
const world=new WorldLayer({intervalMs:2000});
let arOn=prefs.get('ar');
const buzz=ms=>{try{navigator.vibrate&&navigator.vibrate(ms)}catch{}};
// Every spoken reply is also shown as text. Screens that already print the reply (find, summary, changes, guide) do not need the bar.
const SELF_CAPTIONED=new Set(['find','summary','changes','guide']);let capT=null;
function caption(t){const c=el('cap');if(!t||SELF_CAPTIONED.has(scrn)){c.style.display='none';return}el('captext').textContent=t;c.style.display='block';clearTimeout(capT);capT=setTimeout(()=>c.style.display='none',9000)}
const speak=t=>{caption(t);try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(t);u.lang=prefs.voiceLang();u.rate=1;speechSynthesis.speak(u)}catch{}};
const toast=(m,ms=3500)=>{const e=el('toast');e.textContent=m;e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display='none',ms)};
function mk(tag,cls,txt){const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function measure(){ // where the HUD may draw: below the top bar, above the bottom controls
  const hud=el('hud').getBoundingClientRect(),tp=document.querySelector(`#${scrn} .top`),bot=document.getElementById(scrn)?.querySelector('.bot')?.getBoundingClientRect();
  M.tapeY=tp?tp.getBoundingClientRect().bottom+4:70;M.top=M.tapeY+80;M.bot=bot?hud.height-bot.top+14:200}
function go(s){const was=scrn;scrn=s;body.dataset.screen=s;if(s==='find'){rt=0}if(s==='summary')mapT=0;if(was==='guide'&&s!=='guide')stopGuide();
  if(was!==s&&CAM_SCREENS.has(was)&&!CAM_SCREENS.has(s))stopCamera('left the '+was+' screen'); // the camera is on only while a camera screen is showing
  if(was==='summary'&&s!=='summary')stopProc();
  if(s==='ask'){renderQuick();world.refresh().then(renderQuick)}
  syncWorld();requestAnimationFrame(measure)} // speak() after go(): leaving the guide cancels speech
addEventListener('resize',measure);
const showsWorld=()=>arOn&&CAM_SCREENS.has(scrn);
function syncWorld(){if(demo)return;if(showsWorld()||scrn==='find')world.start();else world.stop()} // keeps /api/overlay fresh only while it is on screen

/* ---------- ScanController callback ---------- */
function gapInfo(b){const n=36;let best=null;for(let i=0;i<n;i++){if(b[i]||!b[(i+n-1)%n])continue;let j=0;while(j<n&&!b[(i+j)%n])j++;if(j<n&&(!best||j>best.len))best={start:i,len:j}}
  return best&&{...best,mid:(best.start+best.len/2)*10,size:best.len*10}}
let ready=false; // the scan screen is showing its checklist; nothing is being scanned yet
export function onChange(st,cov){
  if(cov){cur.bins=cov.bins;cur.frac=cov.fraction;cur.gap=cur.frac>.12?gapInfo(cur.bins):null}
  if(st.sent>cur.sent){cur.sent=st.sent;cur.pulse=1;cur.shutter=0;el('flash').classList.remove('go');void el('flash').offsetWidth;el('flash').classList.add('go');buzz(12)}
  if(st.sent<cur.sent)cur.sent=st.sent;
  const ph=st.phase,f=cur.frac;
  if(ph==='starting'||ph==='scanning'){if(scrn!=='scan'){if(demo&&scrn!=='home')return;go('scan')}el('scan').dataset.ph='live'}
  if(ph==='finishing'){if(scrn!=='summary')go('summary');el('sumsheet').classList.add('busy');startProc()}
  if(ph==='done'&&scrn==='scan')go('summary');
  if(ph==='idle'&&scrn==='scan'&&!ready)go('home');
  if(ph==='error'){toast(st.error||'Error',st.error&&/Camera settings changed/.test(st.error)?60000:5000);if(scrn==='scan'||scrn==='summary')go('home');if(poll)poll.retryNow()}
  el('pct').innerHTML=Math.round(f*100)+'<i>%</i>';
  el('fcount').textContent=st.sent+' frames';el('nfr').textContent=st.sent;
  el('nob').textContent=st.objects??0;
  el('nnm').textContent=(st.names&&st.names.length)?st.names.join(' · '):'—';
  const h=coachHint({speed:ctl?ctl.policy.speed():null,pitch:cur.pitch,roll:cur.roll,landscape:innerWidth>innerHeight,fraction:f,gap:ctl?ctl.coverage.nextGap(cur.yaw):null,policy:st.hint||''});
  const sl=el('scanline');sl.textContent=h.text;sl.className='live '+h.tone;
  el('hint').textContent=ph==='scanning'?(/^(frame|capture|no orientation|no scan|queue)/i.test(st.hint||'')?'Problem: '+st.hint:(st.status||'')):'';
  if(st.summary&&st.summary!==shownSummary){shownSummary=st.summary;
    if(st.aborted){toast('The scan was stopped early. What it found is saved.');if(poll)poll.retryNow()} // no jump to a summary the user did not ask for
    else handleEvent({type:'scan_summary',...st.summary})}
}

/* ---------- server events ---------- */
export function handleEvent(m){
  const t=m.type||m.event;
  if(t==='scan_summary'){summary=m;renderSummary(m);world.refresh();if(poll)poll.retryNow()}
  if(t==='find_result')renderFind(m);
  if(t==='guide_request'){renderFind(m.result);toast('Another app sent you to this object. Tap Guide me to follow it.')} // the camera starts only from a tap, so this stops at the result
  if(t==='error')toast(m.message||'Server error');
}
function renderSummary(m){
  // Real summaries carry frames {received, stored, ...} and coverage {covered_deg, gaps}; the demo data uses plain numbers.
  const frames=typeof m.frames==='object'?m.frames.stored:m.frames,cover=typeof m.coverage==='object'?m.coverage.covered_deg/360:m.coverage;
  summary=m;stopProc();el('sumsheet').classList.remove('busy');el('sumtext').textContent=m.speech||'';
  const c={};m.objects.forEach(o=>c[o.label]=(c[o.label]||0)+1);
  el('chips').replaceChildren(...Object.entries(c).map(([k,n])=>{const s=mk('span','chip'),e=mk('em',null,n);s.append(e,k);return s}));
  el('mf').textContent=frames;el('ms').textContent=Math.round(m.seconds);el('mc').textContent=Math.round((cover||0)*100)+'%';el('mu').textContent=m.cost_usd==null?'–':'$'+m.cost_usd.toFixed(3);
  mapT=0;go('summary');speak(m.speech);buzz([20,40,20]);
}
function renderQuick(){
  const u=world.labels(3),q=el('quick');q.replaceChildren();
  if(u.length)q.append(mk('span','label','Ask'));
  for(const l of u){const b=mk('button',null,l);b.onclick=()=>{el('q').value='where is my '+l;doFind(l)};q.append(b)}
}
const dirTxt=(y,p)=>{y=nd(y);return{yaw:Math.round(Math.abs(y))+'° '+(y<0?'L':'R'),pit:Math.round(Math.abs(p||0))+'° '+((p||0)>=0?'↑':'↓')}};
function renderFind(m){
  find=m;const nf=m.found===false;el('result').classList.toggle('nf',nf);
  el('resulttext').textContent=m.speech;el('resq').textContent='“'+(m.query||m.concept||'')+'”';
  if(nf){const x=explainNotFound(m);el('nftitle').textContent=x.title;el('nftext').textContent=x.text;world.highlight(null);img=null;rt=0;go('find');speak(m.speech);buzz(25);return}
  const o=m.object||{},off=m.from_scan_start,d=dirTxt(off?off.dyaw:(o.yaw||0),off?off.dpitch:(o.pitch||0));el('dyaw').textContent=d.yaw;el('dpit').textContent=d.pit;
  el('dseen').textContent=o.n_obs!=null?`${o.n_obs} sighting${o.n_obs===1?'':'s'} · last ${ago(o.age_s??0)}${o.uncertainty_deg>0?' · ±'+Math.round(o.uncertainty_deg)+'°':''}`:'–';
  world.highlight(o.id);
  el('rtag').textContent=((m.concept||'')+' · FRAME '+(m.replay?.frame_id??'–')).toUpperCase();
  el('guidebtn').style.display='';el('gobj').textContent=m.concept||'Object';
  img=null;if(m.replay&&!demo){img=new Image();img.src='/world/frame/'+m.replay.frame_id+'.jpg'}
  rt=0;go('find');speak(m.speech);buzz(25);
}

/* ---------- guide ---------- */
let found=false,lastBand=99;
// The cone behind the arrow: the arrow may really point anywhere within +-coneDeg of where it shows, because the stored direction is uncertain.
function coneD(c){c=Math.min(90,Math.max(0,c));if(c<1)return '';const r=138,a=c*Math.PI/180,x=Math.sin(a)*r,y=150-Math.cos(a)*r;return `M150 150 L${150-x} ${y} A${r} ${r} 0 0 1 ${150+x} ${y} Z`}
export function setGuide({angle=0,deg=0,hint='',found:f=false,coneDeg=0}){
  el('cone').setAttribute('d',f?'':coneD(coneDeg));
  let d=((angle-arrowA+540)%360)-180;arrowA+=d;
  el('arrow').style.transform=`rotate(${arrowA}deg)`;el('gnum').textContent=Math.round(deg);el('ghint').textContent=hint;
  const was=el('guide').classList.contains('found');found=f;
  el('guide').classList.toggle('found',f);if(f){el('ghint').textContent='Found it';el('gfound').style.display='block';if(!was){buzz([30,60,30,60,80]);speak('Found it')}}else el('gfound').style.display='none';
}
const guideTarget=()=>find?.object&&find.found!==false?{yaw:find.object.yaw,pitch:find.object.pitch||0,label:find.concept,uncertainty:find.object.uncertainty_deg||0}:null;
// Live guidance: the stored anchor (compass yaw, pitch) against the current camera pose, ten times a second.
let guideT=null;
function startGuide(){
  stopGuide();found=false;lastBand=99;
  const t=guideTarget();if(!t||(!orient&&!demo))return toast('Nothing to guide to');
  if(demo)return demoGuide();
  let lastSpoken='',lastAt=0;
  const tick=()=>{
    const p=pose();if(!p)return setGuide({angle:0,deg:0,hint:'Waiting for the sensors'});
    const v=el('v'),g=guide(t,{yaw:p.yaw,pitch:p.pitch,roll:p.roll,width:v.videoWidth||540,height:v.videoHeight||960,hfov:world.hfov},{uncertaintyDeg:t.uncertainty});
    setGuide({angle:g.arrowDeg,deg:g.distanceDeg,hint:g.instruction,found:g.arrived,coneDeg:g.coneDeg});
    const band=g.distanceDeg<6?0:g.distanceDeg<15?1:g.distanceDeg<30?2:3;if(band<lastBand&&band<3)buzz(band===0?40:12);lastBand=band;
    const now=performance.now();if(!g.arrived&&g.instruction!==lastSpoken&&now-lastAt>2500){lastSpoken=g.instruction;lastAt=now;speak(g.instruction)}
  };
  tick();guideT=setInterval(tick,100);
}
function stopGuide(){clearInterval(guideT);guideT=null;clearInterval(gt);try{speechSynthesis.cancel()}catch{}}

/* ---------- changes ---------- */
// `d` is the /api/changes answer (same as the Reality API's what_changed). Model text only goes in via textContent.
const CHG_GROUPS=[['moved','↔','var(--accent)','Moved'],['removed','−','var(--red)','Removed'],['added','+','var(--green)','Added']];
export function renderChanges(d){
  const box=el('chg'),slot=(label,rp)=>{
    const s=mk('div','stripe');
    if(rp){const cv=document.createElement('canvas');s.append(cv);requestAnimationFrame(()=>drawPhoto(cv,rp))}else s.append('—');
    s.append(mk('i',null,label));return s};
  el('chgpill').textContent='SCAN #'+d.scan_before+' → #'+d.scan_after;
  const kids=[mk('div','grp panel sumline',d.summary)];
  for(const[k,icon,color,name]of CHG_GROUPS){
    if(!d[k].length)continue;
    const g=mk('div','grp panel'),h=mk('h2'),ic=mk('span','ic',icon);ic.style.background=color;
    h.append(ic,name,mk('span','n',d[k].length));g.append(h);
    for(const e of d[k]){
      const pair=mk('div','pair'+(k==='moved'?'':' one'));
      if(k==='moved')pair.append(slot('BEFORE',e.from.replay),slot('AFTER',e.to.replay));else pair.append(slot(k==='removed'?'BEFORE':'AFTER',e.replay));
      g.append(pair,mk('p',null,e.speech));
    }
    kids.push(g);
  }
  if(d.unverified.length){
    const g=mk('div','grp panel unv'),h=mk('h2'),ic=mk('span','ic','?');ic.style.cssText='background:#fff3;color:#fff';
    h.append(ic,'Not checked',mk('span','n',d.unverified.length));g.append(h);
    for(const x of d.unverified){const p=mk('p',null,`${x.label}: ${x.reason}`);p.style.cssText='margin:0 0 8px;color:var(--dim);font-size:14px';g.append(p)}
    kids.push(g);
  }
  box.replaceChildren(...kids);box.scrollTop=0;
}
// A card for the states of this screen: loading, nothing to compare, server away, error.
function chgNote(title,text,{spin=false,act=null}={}){
  const c=mk('div','statecard panel');if(spin)c.append(mk('div','spin'));
  c.append(mk('h2',null,title),mk('p',null,text));
  if(act){const b=mk('button','plainbtn',act.label);b.onclick=act.run;c.append(b)}
  el('chg').replaceChildren(c)}
async function openChanges(){
  if(demo){renderChanges(MOCK_CHG);go('changes');return speak(MOCK_CHG.summary)}
  go('changes');el('chgpill').textContent='COMPARING';
  chgNote('Comparing your last two scans…','This usually takes a second.',{spin:true});
  const slow=setTimeout(()=>chgNote('Still comparing…','The server is slower than usual. You can wait, or go back and try later.',{spin:true}),8000);
  const r=await fetchJson('/api/changes',{timeoutMs:30000});clearTimeout(slow);
  if(scrn!=='changes')return; // the user left while it loaded
  const retry={label:'Try again',run:openChanges};
  if(!r.ok&&!r.status)return chgNote(r.timedOut?'The server did not answer in time':'Server not reachable',r.timedOut?'It may be busy. Try again in a moment.':'Check that the computer is on. Reality OS keeps reconnecting by itself.',{act:retry});
  if(!r.ok)return chgNote('The server could not compare the scans',`It answered with an error (${r.status}). ${r.error||''}`.trim(),{act:retry});
  const d=r.data;
  if(d.status==='need_two_scans')return chgNote('Nothing to compare yet','Comparing needs two scans of the same room. Scan once, move or add something, then scan again.',{act:{label:'Scan the room',run:()=>wantCamera('scan')}});
  if(d.status!=='ok')return chgNote('Cannot compare yet',d.message||d.detail||'Try again after another scan.',{act:retry});
  renderChanges(d);speak(d.summary);buzz(25);
}
const SHOT={frame_id:1,box:[180,190,330,300],width:640,height:480};
const MOCK_CHG={status:'ok',scan_before:3,scan_after:4,summary:'The mug moved: it was 40 degrees left, now 80 degrees right. A water bottle is new. 1 object could not be checked because that part of the room was not scanned both times.',
  moved:[{label:'mug',speech:'The mug moved: it was 40 degrees left, near the laptop, now 80 degrees right, on the desk.',from:{replay:SHOT},to:{replay:SHOT}}],
  removed:[{label:'keys',speech:'The keys are gone; they were 150 degrees right, near the door.',replay:SHOT}],
  added:[{label:'bottle',speech:'A bottle is new, 40 degrees right, on the desk.',replay:SHOT}],
  unverified:[{label:'phone',reason:'that direction was not rescanned'}]};

/* ---------- pose and the AR overlay ---------- */
let ctl=null,sock=null,cam=null,orient=null,session=null,poll=null,facing='environment',streaming=null,guard=null,camAlarm=false,sockUp=false;
// The live camera pose: sensor yaw, pitch and the image roll (sensor roll plus the screen angle), as the server anchored it.
function pose(){
  if(demo)return{yaw:cur.yaw,pitch:cur.pitch,roll:0};
  const s=orient&&(orient.latest.absolute||orient.latest.relative);if(!s)return null;
  return{yaw:s.yaw,pitch:s.pitch,roll:(s.roll||0)+(window.screen.orientation?.angle||0)};
}
const viewSize=()=>{const v=el('v');return demo||!v.videoWidth?{width:1080,height:1920}:{width:v.videoWidth,height:v.videoHeight}};
let fpsT=[],drawMs=0,lastInfo=0,arStats={drawn:0,hidden:0};
const fps=()=>{const n=fpsT.length;return n>1?(n-1)*1000/(fpsT[n-1]-fpsT[0]):0};
window.__ar={fps,stats:()=>({fps:fps(),drawMs,...arStats,objects:world.objects.length,hfov:world.hfov,calibrated:world.calibrated})};
function setAr(on){arOn=on;prefs.set('ar',on);
  for(const b of [el('artog'),el('artog2')]){b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on))}syncWorld()}

/* ---------- render loop ---------- */
let last=performance.now();
function frame(now){
  const dt=Math.min(.05,(now-last)/1000);last=now;
  fpsT.push(now);while(fpsT.length>90)fpsT.shift();
  const p=demo?null:pose();
  if(p){cur.tyaw=p.yaw;cur.tpitch=p.pitch;cur.roll=p.roll}
  const k=demo?Math.min(1,dt*(dragging?30:10)):1; // the overlay must follow the live pose exactly, or objects swim
  cur.yaw=(cur.yaw+nd(cur.tyaw-cur.yaw)*k+360)%360;cur.pitch+=(cur.tpitch-cur.pitch)*k;
  cur.pulse=Math.max(0,cur.pulse-dt*2.5);cur.shutter+=dt/.5;
  if(CAM_SCREENS.has(scrn)){
    const ar=el('ar'),tg=guideTarget(),sc=scrn==='scan',t0=performance.now();
    const objs=world.objects;
    const s={mode:scrn,now,yaw:cur.yaw,pitch:cur.pitch,roll:cur.roll,view:{...viewSize(),hfov:world.hfov},world:objs,showWorld:arOn&&!camAlarm,
      bins:sc?cur.bins:null,gapBins:sc&&cur.gap?new Set(Array.from({length:cur.gap.len},(_,i)=>(cur.gap.start+i)%36)):null,gap:sc?cur.gap:null,
      markers:scrn==='guide'?(tg?[{yaw:tg.yaw,hl:true}]:[]):objs,target:tg,found,label:tg?.label,pulse:cur.pulse,shutter:cur.shutter,tapeY:M.tapeY,top:M.top,bot:M.bot};
    drawAR(ar,s);arStats=s.stats;drawMs+=((performance.now()-t0)-drawMs)*.1;
  }
  if(body.classList.contains('dbg')&&now-lastInfo>500){lastInfo=now;const s=window.__ar.stats(),c=session?session.state:null;
    el('arinfo').textContent=`AR ${arOn?'on':'off'} · ${s.fps.toFixed(0)} fps · draw ${s.drawMs.toFixed(1)} ms · ${s.drawn} shown, ${s.hidden} hidden · FOV ${s.hfov}°${s.calibrated?'':' (not calibrated)'}${c?` · camera ${c.camera}, wake ${c.wake?'on':'off'}, portrait ${c.portrait?'locked':'free'}`:''}`}
  if(scrn==='scan'&&el('scan').dataset.ph==='live')drawRing(el('ring'),cur.bins,cur.yaw,cur.pulse); // the ring is hidden (zero size) while the checklist shows
  if(scrn==='summary'&&summary&&!el('sumsheet').classList.contains('busy')){mapT+=dt;drawMap(el('mapc'),summary.objects.map((o,i)=>({...o,age:Math.min(1,Math.max(0,mapT*2-i*.08))})))}
  if(scrn==='find'&&find?.replay){rt+=dt*1.6;drawReplay(el('replay'),img,find.replay,rt)}
  if(scrn==='find'&&find?.found!==false){const o=find?.object||{};drawBearing(el('bear'),o.yaw||0,o.pitch||0,o.uncertainty_deg||0)}
  requestAnimationFrame(frame)}
requestAnimationFrame(frame);

/* ---------- drag to look around (demo / desktop preview) ---------- */
let dragging=false,px=0,py=0;const ppd=()=>innerWidth/60;
addEventListener('pointerdown',e=>{if(!demo||!CAM_SCREENS.has(scrn)||e.target.closest('button,input,summary,details,.chg,select'))return;dragging=true;px=e.clientX;py=e.clientY});
addEventListener('pointermove',e=>{if(!dragging||scrn==='scan')return;cur.tyaw=(cur.tyaw-(e.clientX-px)/ppd()+360)%360;cur.tpitch=Math.max(-70,Math.min(70,cur.tpitch+(e.clientY-py)/ppd()));px=e.clientX;py=e.clientY});
addEventListener('pointerup',()=>dragging=false);

/* ---------- the camera: starts from a tap, stops when the screen is left ---------- */
// The camera state each frame was taken in. The server compares it with the state the FOV was calibrated for.
const camMeta=()=>({facing,video_w:el('v').videoWidth,video_h:el('v').videoHeight,screen_angle:window.screen.orientation?.angle??null,
  settings:guard?{...guard.current(),zoomLocked:guard.zoomLocked}:null});
const active=()=>ctl&&['starting','scanning','finishing'].includes(ctl.phase);
function onCamState(st){
  body.classList.toggle('live',st.camera==='on');
  for(const b of document.querySelectorAll('.camstop')){b.classList.toggle('starting',st.camera==='starting');b.classList.toggle('off',st.camera==='off');
    b.querySelector('.cl').textContent=st.camera==='starting'?'Starting camera…':st.camera==='off'?'Camera off':'Camera on'}
}
function stopCamera(reason){ // all tracks, the sensors, the wake lock and the portrait lock
  clearInterval(lightT);
  if(demo||!session)return;
  session.release(reason);guard=null;if(ctl)ctl.guard=null;camAlarm=false;
}
// The tab was hidden (or the page left): the camera is already off. A running scan stops and keeps what it sent.
function onHidden(){
  if(active())ctl.abort('The app went to the background, so the scan stopped. What it found so far is kept.');
  else if(CAM_SCREENS.has(scrn)){go('home');toast('The camera was turned off because the page was hidden.')}
}
function onCamEnded(){ // another app or the system took the camera
  if(active())ctl.abort('The camera stopped (another app may be using it), so the scan stopped. What it found so far is kept.');
  else if(CAM_SCREENS.has(scrn)){go('home');toast('The camera stopped. Another app may be using it.')}
}
// Bring the camera up on a camera screen. The tap that got us here is the user's permission to start it.
async function useCamera(screen){
  go(screen);
  if(demo)return true;
  try{
    const r=await session.acquire(facing);guard=r.guard;ctl.guard=guard;camAlarm=false;
    if(orient.permission==='denied')throw Object.assign(new Error('Motion access was denied'),{name:'MotionDenied'});
    measure();return true;
  }catch(e){
    if(e.name===RELEASED)return false;
    stopCamera('failed');failCamera(e,screen);return false;
  }
}
function failCamera(e,action){pendingAction=action;openPrimer(e.name==='MotionDenied'?MOTION_DENIED:classifyCameraError(e))}
// Outside a scan too: if the zoom, resolution or lens changes, say so at once and keep saying so (the FOV no longer applies).
function watchCamera(){
  setInterval(()=>{
    const d=guard?[...guard.diff(),...diffCalibration(world.calibration,guard.current())]:[];
    if(d.length&&!camAlarm){camAlarm=true;renderConn();
      toast('Camera settings changed ('+d.map(x=>x.key+' '+x.was+' → '+x.now).join(', ')+'). Overlay and scans are off until the camera is restarted.',60000);
      if(active())ctl.abort('Camera settings changed ('+d.map(x=>x.key).join(', ')+'). The scan was stopped.')}
  },1000)
}

/* ---------- first run: intro, then the permission primer ---------- */
let ob=null,pendingAction=null,primerCanAsk=true;
function openIntro(replay=false){ob=new Onboarding(prefs,{replay});renderIntro();go('welcome')}
function renderIntro(){
  const c=ob.card;el('introcard').replaceChildren(mk('div','ic',c.icon),mk('h1',null,c.title),...c.body.map(t=>mk('p',null,t)));
  el('introdots').replaceChildren(...CARDS.map((_,i)=>mk('i',i===ob.index?'on':null)));
  el('intronext').textContent=ob.nextLabel;el('introback').style.visibility=ob.index?'visible':'hidden';
}
el('intronext').onclick=()=>{
  if(!ob.last){ob.next();return renderIntro()}
  const replay=ob.replay;ob.markSeen();
  if(replay)return go('settings');
  pendingAction=null;prefs.primed?go('home'):openPrimer();
};
el('introskip').onclick=()=>{const replay=ob.replay;ob.markSeen();go(replay?'settings':'home')};
el('introback').onclick=()=>{ob.back();renderIntro()};
function openPrimer(problem=null){
  el('primertitle').textContent=PRIMER.title;el('primerintro').textContent=PRIMER.intro;el('primernote').textContent=PRIMER.note;
  el('primeritems').replaceChildren(...PRIMER.items.map(i=>{const r=mk('div','ritem'),t=mk('div');t.append(mk('b',null,i.name),mk('span',null,i.why));r.append(mk('div','ic',i.id==='camera'?'◉':'↻'),t);return r}));
  el('primerproblem').hidden=!problem;primerCanAsk=!problem||problem.retry!==false;
  if(problem){el('problemtitle').textContent=problem.title;el('problembody').textContent=problem.body}
  el('primergo').textContent=problem?(primerCanAsk?'Try again':'Close'):'Continue';el('primergo').disabled=false;
  go('primer');
}
// The button that triggers the browser's own prompts: only reached after the primer has said what they are for.
el('primergo').onclick=async()=>{
  if(!primerCanAsk){pendingAction=null;return go('home')}
  const b=el('primergo');b.disabled=true;b.textContent='Waiting for your answer…';
  const r=demo?{ok:true}:await requestAccess({nav:navigator,win:window,secure:window.isSecureContext});
  if(r.ok){prefs.set('primed',true);const a=pendingAction;pendingAction=null;if(a)return runAction(a);go('home');return toast('Allowed. Tap New scan to begin.')}
  openPrimer(r.problem);
};
el('primerlater').onclick=()=>{pendingAction=null;go('home')};
const wantCamera=action=>{ // New scan, Find something, Guide me
  if(!demo&&cameraGate(prefs)==='primer'){pendingAction=action;return openPrimer()}
  return runAction(action)};
function runAction(a){return a==='scan'?openScan():a==='ask'?openAsk():openGuide()}

/* ---------- connection and Home ---------- */
let pollView=describe(null);
function renderConn(){
  const d=pollView;let label=d.label,tone=d.tone;
  const wsOk=demo||!sock||sockUp;
  if(camAlarm){label='Camera changed';tone='bad'}
  else if(d.state==='ready'&&!wsOk){label='Reconnecting…';tone='warn'}
  el('stat').textContent=label;el('dotc').className='dot'+(tone==='ok'?'':tone==='bad'?' bad':tone==='warn'?' warn':' wait');
  const can={scan:d.can.scan&&wsOk,find:d.can.find&&wsOk,changes:d.can.changes};
  el('scanbtn').disabled=!can.scan;el('askbtn').disabled=!can.find;el('chgbtn').disabled=!can.changes;
  const n=el('homenotice'),detail=d.state==='ready'&&!wsOk?'Lost the live link to the server. Reconnecting automatically…':d.detail;
  n.hidden=!detail;if(detail){n.className='notice panel '+(tone==='warn'?'warn':'bad');el('noticetext').textContent=detail}
}
function renderLast(){
  const c=el('lastcard'),d=pollView;c.replaceChildren();
  if(d.state==='loading'){c.append(mk('div','skel'),mk('div','skel'));c.lastChild.style.width='60%';return}
  if(d.state!=='ready'){c.append(mk('h2',null,'Last scan unavailable'),mk('p',null,'It will show here when the server is back.'));return}
  const ls=poll?.last?.status?.last_scan;
  if(!ls){c.append(mk('h2',null,'No scan yet'),mk('p',null,'Tap New scan, stand in the middle of the room and turn slowly. Then you can ask where things are.'));return}
  const t=ls.ended_at||ls.started_at,big=mk('div','big');
  for(const[k,v]of [['Objects',ls.n_objects],['Covered',ls.coverage_pct+'%'],['When',ago(Math.max(0,Date.now()/1000-t))]]){const x=mk('div');x.append(mk('span','label',k),mk('b',null,v));big.append(x)}
  c.append(mk('h2',null,'Last scan'),big);
}
function onStatus(r,d){pollView=d;renderConn();renderLast()}
el('connpill').onclick=()=>{if(poll)poll.retryNow({refresh:true});else toast(demo?'Demo mode: no server':'Starting up…')};
el('retrybtn').onclick=()=>{if(poll)poll.retryNow({refresh:true})};
function onSockState(s){
  sockUp=s==='connected';renderConn();
  if(!sockUp&&active())ctl.abort('Connection lost, the scan stopped. Objects found so far are kept.');
  if(!sockUp&&findPending){findDone();askState('err','Lost the connection to the server. It reconnects by itself, then try again.',{label:'Try again',run:()=>doFind(lastQ)})}
  if(sockUp&&poll)poll.retryNow();
}

/* ---------- scanning: checklist, coaching, processing ---------- */
const lc=document.createElement('canvas');lc.width=32;lc.height=24;let lightT=null;
function sampleLight(){const v=el('v');if(!v.videoWidth)return null;const x=lc.getContext('2d',{willReadFrequently:true});x.drawImage(v,0,0,32,24);return meanLuma(x.getImageData(0,0,32,24).data)}
function startLight(){
  clearInterval(lightT);
  const tick=()=>{const lv=lightVerdict(demo?120:sampleLight()),r=el('chklight');el('lighttext').textContent=lv.text;r.className='chk'+(lv.ok===true?' ok':lv.ok===false?' bad':'');r.firstChild.textContent=lv.ok===true?'✓':lv.ok===false?'!':'·'};
  tick();lightT=setInterval(tick,1000);
}
async function openScan(){
  summary=null;shownSummary=null;ready=true;el('scan').dataset.ph='ready';
  el('chkpeople').setAttribute('aria-pressed','false');el('lighttext').textContent='Checking the light…';
  if(!await useCamera('scan'))return;
  startLight();
}
el('startbtn').onclick=()=>{
  if(demo){ready=false;return demoScan()}
  if(!session.active)return toast('The camera is not running yet');
  ctl.policy.minIntervalMs=prefs.minIntervalMs();ready=false;clearInterval(lightT);ctl.start();
};
el('chkpeople').onclick=e=>{const b=e.currentTarget;b.setAttribute('aria-pressed',String(b.getAttribute('aria-pressed')!=='true'))};
el('donebtn').onclick=()=>{if(demo)return demoDone();ctl.done()};
async function cancelScan(){
  if(ready){ready=false;stopCamera('cancelled');return go('home')} // nothing was started: just close the camera
  const yes=await confirmDlg({title:'Discard this scan?',body:'Everything captured so far is deleted. You can scan again right away.',yes:'Discard',no:'Keep scanning'});
  if(!yes)return;
  if(demo){clearInterval(st);return go('home')}
  if(ctl.phase==='scanning'||ctl.phase==='starting')ctl.cancel();else go('home');
}
el('scancancel').onclick=cancelScan;
let procT=null;
function startProc(){stopProc();updateProc();procT=setInterval(updateProc,500)}
function stopProc(){clearInterval(procT);procT=null}
function updateProc(){
  if(!ctl||demo)return;
  const st=ctl.st,now=performance.now(),p=processing(st,now-st.doneAt,now-st.progressAt);
  el('proctxt').textContent=p.text;el('procbar').style.width=Math.round(p.fraction*100)+'%';el('procpct').textContent=Math.round(p.fraction*100)+'%';
  el('procbox').className='procbox '+p.level;el('procact').hidden=!(p.level==='stalled'||p.level==='late');
}
// "Check the server": the scan may have finished while the summary never arrived (the link dropped). Ask what it stored.
async function recoverSummary(){
  const id=ctl?.st.scanId;
  const s=await fetchJson('/api/status',{timeoutMs:10000});
  if(!s.ok)return toast(s.status?`The server answered with an error (${s.status}).`:'The server is not reachable. It keeps trying to reconnect.');
  const ls=s.data.last_scan;
  if(!ls||(id!=null&&ls.id<id)||!ls.ended_at)return toast('The server has not finished this scan yet. Keep waiting, or go home and look again later.');
  const o=await fetchJson('/api/overlay?scan_id='+ls.id,{timeoutMs:10000});
  if(!o.ok)return toast('Could not load the scan’s objects.');
  const n=o.data.objects.length;
  renderSummary({speech:n?`I found ${n} object${n===1?'':'s'}.`:'I did not find any objects.',coverage:ls.coverage_pct/100,frames:ls.n_frames,seconds:ls.ended_at-ls.started_at,objects:o.data.objects,scan_id:ls.id});
}
el('proccheck').onclick=recoverSummary;
el('prochome').onclick=()=>{go('home');toast('The scan keeps processing on the server. Its result shows on Home.');if(poll)poll.retryNow()};

/* ---------- ask / find ---------- */
let findPending=false,findTimers=[],lastQ='';
function askState(kind,msg,act){ // kind: null | 'busy' | 'slow' | 'err' | 'info'
  const b=el('askstate');if(!kind){b.hidden=true;return}
  b.hidden=false;b.classList.toggle('err',kind==='err');el('askspin').style.display=kind==='busy'||kind==='slow'?'':'none';el('askmsg').textContent=msg;
  const a=el('askact');a.hidden=!act;if(act){a.textContent=act.label;a.onclick=act.run}
}
function findDone(){findPending=false;findTimers.forEach(clearTimeout);findTimers=[];askState(null)}
function cancelFind(){findDone()}
async function openAsk(){await useCamera('ask')}
function doFind(label){
  const q=(typeof label==='string'?label:el('q').value).trim();if(!q&&!demo)return;
  if(demo)return demoFind(q);
  lastQ=q;findDone();
  if(!sock.sendJson({type:'find',query:q}))return askState('err','Not connected to the server. It reconnects by itself, then try again.',{label:'Try again',run:()=>doFind(q)});
  findPending=true;const cancel={label:'Cancel',run:cancelFind};
  askState('busy','Looking for “'+q+'”…',cancel);
  findTimers=[setTimeout(()=>{if(findPending)askState('slow','Still looking. The answer is slower than usual…',cancel)},8000),
    setTimeout(()=>{if(findPending){findDone();askState('err','No answer from the server after 30 seconds.',{label:'Try again',run:()=>doFind(q)})}},30000)];
}
function findFailed(msg){
  findDone();
  const key=/auth|401|403|api key|permission/i.test(msg||'');
  askState('err',key?'The model API rejected the key. Check MODEL_API_KEY on the server.':'The server could not answer: '+(msg||'unknown error'),{label:'Try again',run:()=>doFind(lastQ)});
}
el('findbtn').onclick=()=>doFind();el('q').onkeydown=e=>e.key==='Enter'&&doFind();
// Voice is optional. If it cannot work, say so and leave the text box ready.
el('mic').onclick=()=>{
  const R=window.SpeechRecognition||window.webkitSpeechRecognition,m=el('mic'),typeInstead=t=>{askState('info',t);el('q').focus()};
  if(!R)return typeInstead('Voice input is not available in this browser. Type your question instead.');
  const r=new R();r.lang=prefs.voiceLang();
  r.onresult=e=>{el('q').value=e.results[0][0].transcript;doFind()};
  r.onerror=e=>typeInstead(e.error==='not-allowed'||e.error==='service-not-allowed'?'Microphone access is blocked. Type your question instead.':e.error==='no-speech'?'I did not hear anything. Try again, or type it.':'Voice input failed. Type your question instead.');
  r.onend=()=>m.classList.remove('on');
  try{r.start();m.classList.add('on');askState('info','Listening…',null)}catch{typeInstead('Voice input could not start. Type your question instead.')}
};

/* ---------- settings ---------- */
let mockScans=null;
function confirmDlg({title,body,yes='OK',no='Cancel'}){
  return new Promise(res=>{
    el('dlgtitle').textContent=title;el('dlgbody').textContent=body;el('dlgyes').textContent=yes;el('dlgno').textContent=no;
    const d=el('dlg');d.classList.add('on');
    const done=v=>{d.classList.remove('on');el('dlgyes').onclick=el('dlgno').onclick=null;res(v)};
    el('dlgyes').onclick=()=>done(true);el('dlgno').onclick=()=>done(false);
  });
}
const dbgEnabled=()=>P.has('debug')&&prefs.get('debug');
function applyDebug(){body.classList.toggle('dbg',dbgEnabled());for(const b of el('dbgseg').children)b.setAttribute('aria-pressed',String((b.dataset.v==='1')===dbgEnabled()))}
function renderSettings(){
  el('dbgrow').hidden=!P.has('debug');applyDebug();
  const k=el('kfseg');k.replaceChildren(...KFPS.map(n=>{const b=mk('button',null,n+' / s');b.setAttribute('aria-pressed',String(prefs.get('kfps')===n));
    b.onclick=()=>{prefs.set('kfps',n);if(ctl)ctl.policy.minIntervalMs=prefs.minIntervalMs();renderSettings()};return b}));
  const s=el('langsel');if(!s.children.length)for(const[v,l]of LANGS){const o=mk('option',null,l);o.value=v;s.append(o)}
  s.value=prefs.get('lang');
}
for(const b of el('dbgseg').children)b.onclick=()=>{prefs.set('debug',b.dataset.v==='1');applyDebug()};
el('langsel').onchange=e=>{prefs.set('lang',e.target.value);toast('Voice language: '+(e.target.selectedOptions[0].textContent))};
async function loadScans(){
  const box=el('scanlist'),all=el('delall');all.hidden=true;box.replaceChildren(mk('div','skel'));
  const note=(t,act)=>{const d=mk('div');d.append(mk('p',null,t));if(act){const b=mk('button','plainbtn',act.label);b.style.marginTop='10px';b.style.width='100%';b.onclick=act.run;d.append(b)}box.replaceChildren(d)};
  const r=demo?{ok:true,data:{scans:mockScans}}:await fetchJson('/api/scans',{timeoutMs:10000});
  if(scrn!=='settings')return;
  if(!r.ok)return note(r.status?`Could not load your scans (server error ${r.status}).`:'The server is not reachable, so scans cannot be listed. Try again when it is back.',{label:'Try again',run:loadScans});
  const scans=r.data.scans;
  if(!scans.length)return note('No scans are stored.');
  box.replaceChildren(...scans.map(s=>{
    const row=mk('div','srow'),t=mk('div');t.append(`Scan #${s.id}`,mk('small',null,`${ago(Math.max(0,Date.now()/1000-s.started_at))} · ${s.n_objects} objects · ${s.n_frames} frames`));
    const b=mk('button',null,'Delete');b.setAttribute('aria-label','Delete scan '+s.id);b.onclick=()=>deleteScans(s.id);row.append(t,b);return row}));
  all.hidden=scans.length<2;
}
async function deleteScans(id){ // id = one scan, or undefined for all of them
  const yes=await confirmDlg(id!=null?{title:`Delete scan #${id}?`,body:'Its frames, photos and objects are removed from the computer. This cannot be undone.',yes:'Delete',no:'Keep it'}
    :{title:'Delete all scans?',body:'Every stored scan, with its frames, photos and objects, is removed from the computer. This cannot be undone.',yes:'Delete all',no:'Keep them'});
  if(!yes)return;
  if(demo){mockScans=id!=null?mockScans.filter(s=>s.id!==id):[];toast('Deleted');return loadScans()}
  const r=await fetchJson(id!=null?'/api/scans/'+id:'/api/scans',{method:'DELETE',timeoutMs:20000});
  if(!r.ok)return toast(r.status?`Could not delete (server error ${r.status}).`:'Could not delete: the server is not reachable.');
  toast(id!=null?`Scan #${id} deleted`:'All scans deleted');world.refresh();if(poll)poll.retryNow();loadScans();
}
el('delall').onclick=()=>deleteScans();
function openSettings(){renderSettings();go('settings');loadScans()}
el('replayintro').onclick=()=>openIntro(true);
el('setback').onclick=()=>go('home');

/* ---------- wiring ---------- */
el('scanbtn').onclick=()=>{buzz(20);wantCamera('scan')};
el('askbtn').onclick=()=>wantCamera('ask');
el('chgbtn').onclick=openChanges;el('setbtn').onclick=openSettings;
el('askback').onclick=()=>go('home');
document.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>{if(scrn==='scan')return cancelScan();go('home')}); // leaving a camera screen stops the camera
el('resback').onclick=el('cback').onclick=()=>go('home');el('sumback').onclick=()=>go('home');el('sumfind').onclick=()=>wantCamera('ask');
el('nfscan').onclick=()=>wantCamera('scan');el('nfask').onclick=()=>wantCamera('ask');
el('guidebtn').onclick=()=>wantCamera('guide');
async function openGuide(){if(!await useCamera('guide'))return;startGuide()}
el('gback').onclick=()=>{stopGuide();go('find')};el('gfound').onclick=()=>{stopGuide();world.highlight(null);go('home')};
el('artog').onclick=el('artog2').onclick=()=>setAr(!arOn);
el('cap').onclick=()=>el('cap').style.display='none';
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{const s=b.dataset.go;
  if(s==='summary'&&!summary)renderSummary(MOCK_SUM);else if(s==='summary')go('summary');else if(s==='find')renderFind(MOCK_FIND);else if(s==='guide'){if(!find)renderFind(MOCK_FIND);go('guide');startGuide()}
  else if(s==='changes'){renderChanges(MOCK_CHG);go('changes')}else if(s==='scan'){go('scan');demoScan(true)}else go(s);el('dev').open=false});
// dev (?debug): flip the camera, stream frames to the server (echo only) and save full-size samples with their orientation (M0 measurements)
el('flipbtn').onclick=async()=>{if(!session)return;facing=facing==='environment'?'user':'environment';if(session.busy){session.release('flip');await useCamera(scrn)}};
el('streambtn').onclick=()=>{
  if(!sock)return;
  if(streaming){clearInterval(streaming);streaming=null;el('streambtn').textContent='Stream';return}
  el('streambtn').textContent='Stop';
  streaming=setInterval(async()=>{if(!sock.ready||sock.backlog>512*1024)return;const b=await cam.grabJpeg(el('v'),960,.8);if(b)sock.send(b)},500);
};
el('savebtn').onclick=async()=>{
  if(!sock||!sock.ready)return toast('Not connected to the server');
  if(!session?.active)return toast('The camera is off. Open a camera screen first.');
  const b=await cam.grabJpeg(el('v'),1920,.9);if(!b)return;
  await sock.send(b,{save:true,meta:{orientation:orient.snapshot(),camera:camMeta()}});toast('Sample saved');
};
window.__scan=()=>ctl;window.__ui={cur,go,world,prefs,say:speak,screen:()=>scrn,session:()=>session};

/* ---------- boot ---------- */
// Opening the page only builds the screens and starts the server check. The camera, the motion sensors and the microphone
// stay untouched until a tap asks for them (tests/test_entry_flow.py fails if that ever changes).
async function boot(){
  setAr(arOn);applyDebug();renderConn();renderLast();
  if(demo){body.classList.add('demo');return startDemo()}
  try{
    const [c,w,o,k,s]=await Promise.all(['camera','ws','orientation','keyframe','scan'].map(n=>import('/js/'+n+'.js')));
    cam=c;orient=new o.OrientationTracker();
    session=new CameraSession({video:el('v'),startCamera:c.startCamera,orient,onState:onCamState,onHidden,onEnded:onCamEnded});
    sock=new w.FrameSocket(r=>{
      if(r.type==='find_result'){if(!findPending)return;findDone();return handleEvent(r)}
      if(r.type==='guide_request')return handleEvent(r);
      if(r.type==='error'&&r.where==='find')return findFailed(r.message);
      if(r.type)return ctl.onEvent(r)},onSockState);
    ctl=new s.ScanController({video:el('v'),orient,sock,grabJpeg:c.grabJpeg,camera:camMeta,onChange,fov:()=>world.hfov,guard,policy:{minIntervalMs:prefs.minIntervalMs()}});
    watchCamera();
    poll=new StatusPoller({onUpdate:onStatus});poll.start();
    document.addEventListener('visibilitychange',()=>{if(document.hidden)poll.stop();else poll.start()}); // no polling in the background
    measure();
    if(showIntroOnStart(prefs))openIntro();
  }catch(e){console.error('boot failed, using the demo:',e);demo=true;body.classList.add('demo');toast('Backend not found, demo mode');startDemo()}
}

/* ---------- demo (open /?demo, or automatic when the backend is not reachable) ---------- */
const MOCK_SUM={speech:'I found 14 objects: 3 laptops, 3 cables, 2 water bottles, a charger, a mug, keys and a phone.',coverage:.97,frames:42,seconds:31,cost_usd:.084,objects:[['laptop',-20,5],['laptop',35,0],['laptop',200,8],['cable',60,-25],['cable',-75,-30],['cable',150,-20],['bottle',95,-5],['bottle',250,0],['charger',-60,-10],['mug',120,2],['keys',300,-15],['phone',10,-12],['monitor',-5,20],['lamp',330,35]].map(([label,yaw,pitch],i)=>({id:i+1,label,yaw:(yaw+360)%360,pitch,n_obs:3}))};
const MOCK_FIND={speech:'The charger was last seen about 60 degrees to the left of where the scan started, below the laptop.',concept:'charger',found:true,object:{id:9,label:'charger',yaw:300,pitch:-10,n_obs:3,age_s:120,uncertainty_deg:8,fov_error_deg:2.5},replay:{frame_id:14,box:[180,190,330,300],width:640,height:480}};
const MOCK_NOTFOUND={type:'find_result',query:'my passport',concept:'passport',found:false,speech:'I have not seen that yet.',object:null,replay:null,checked:{scans:3,covered_deg:313,scan_id:3}};
const demoStatus=(api='ok',last=true)=>({ok:true,status:{server:true,api:{state:api,message:{missing:'MODEL_API_KEY is not set on the server. Scans and Find cannot work until it is.'}[api]||''},scans:last?3:0,
  last_scan:last?{id:3,started_at:Date.now()/1000-7200,ended_at:Date.now()/1000-7170,n_objects:14,n_frames:42,coverage_pct:97,covered_deg:349}:null}});
// demo objects for the overlay: half with a mask outline captured facing them, half with only a footprint
function demoWorld(){
  const blob=(rx,ry)=>Array.from({length:10},(_,i)=>{const a=i/10*Math.PI*2;return[270+Math.cos(a)*rx*(1+.12*Math.sin(a*3)),480+Math.sin(a)*ry*(1+.12*Math.cos(a*2))]});
  world.apply({hfov_deg:P.has('fov')?+P.get('fov'):60,fov_calibrated:false,scan_id:3,objects:MOCK_SUM.objects.map((o,i)=>({
    id:o.id,label:o.label,yaw:o.yaw,pitch:o.pitch,uncertainty_deg:i===5?30:i%4,n_obs:o.n_obs,confidence:.9,age_s:[5,5,20,400,900,5,60,5,5,5,5,5,5,5][i],
    footprint:[o.yaw,-7,7,o.pitch-6,o.pitch+6],
    mask:i%2?null:{poly:blob(60+i*3,46),yaw:o.yaw,pitch:o.pitch,roll:0,width:540,height:960,hfov:60}}))});
}
let st=null,gt=null;
function demoHome(view){ // /?demo&screen=home-offline|home-loading|home-empty|home-nokey
  const r={'home-offline':{ok:false,error:'Failed to fetch'},'home-empty':demoStatus('ok',false),'home-nokey':demoStatus('missing',false)}[view];
  pollView=view==='home-loading'?describe(null):describe(r);poll={last:r,retryNow(){}};renderConn();renderLast();
}
function startDemo(){
  demoWorld();renderQuick();measure();mockScans=[3,2,1].map((id,i)=>({id,started_at:Date.now()/1000-7200*(i+1),n_objects:[14,11,9][i],n_frames:[42,30,25][i]}));
  const ok=demoStatus();poll={last:ok,retryNow(){}};pollView=describe(ok);renderConn();renderLast();
  if(P.has('yaw'))cur.tyaw=cur.yaw=+P.get('yaw');if(P.has('pitch'))cur.tpitch=cur.pitch=+P.get('pitch');
  const s=P.get('screen');if(!s)return; // /?demo&screen=... jumps straight to a screen with mock data
  if(s.startsWith('home-'))demoHome(s);
  else if(s==='welcome')openIntro();else if(s==='primer')openPrimer();else if(s==='primer-denied')openPrimer(classifyCameraError({name:'NotAllowedError'}));
  else if(s==='settings')openSettings();else if(s==='ask')go('ask');else if(s==='ask-busy'){go('ask');askState('busy','Looking for “charger”…',{label:'Cancel',run:()=>askState(null)})}
  else if(s==='notfound')renderFind(MOCK_NOTFOUND);
  else if(s==='scan-ready'){ready=true;el('scan').dataset.ph='ready';go('scan');startLight()}
  else if(s==='processing'){go('summary');el('sumsheet').classList.add('busy');el('procbox').className='procbox stalled';el('procact').hidden=false;el('proctxt').textContent='No answer from the server for a while.';el('procbar').style.width='70%';el('procpct').textContent='70%'}
  else if(s==='changes-empty'){go('changes');chgNote('Nothing to compare yet','Comparing needs two scans of the same room. Scan once, move or add something, then scan again.',{act:{label:'Scan the room',run:()=>wantCamera('scan')}})}
  else if(s==='summary')renderSummary(MOCK_SUM);else if(s==='find')renderFind(MOCK_FIND);else if(s==='guide'){renderFind(MOCK_FIND);go('guide');startGuide()}
  else if(s==='changes'){renderChanges(MOCK_CHG);go('changes')}else if(s==='scan'){ready=false;el('scan').dataset.ph='live';go('scan');demoScan(true)}
}
function demoScan(preview){
  clearInterval(st);ready=false;el('scan').dataset.ph='live';go('scan');Object.assign(cur,{bins:Array(36).fill(false),frac:0,sent:0,tyaw:0,yaw:0,tpitch:0,pitch:0,gap:null});
  const hints=['Slow down','Hold steady','Keep turning','Gap 40° to the left','Almost there'];let n=0,y=0;
  const names=[['laptop'],['mouse','cable'],['mug'],['bottle'],['charger'],['keys'],['phone'],['cable'],['lamp']];
  st=setInterval(()=>{n++;y=(y+3.6)%360;if(y>150&&y<200)y+=30;const b=cur.bins.slice();for(let i=0;i<36;i++)if(Math.abs(nd(i*10+5-y))<7)b[i]=true;
    const fr=b.filter(Boolean).length/36,show=n%3===0;cur.tyaw=y;
    onChange({phase:'scanning',sent:show?cur.sent+1:cur.sent,objects:Math.min(14,Math.floor(fr*16)),names:show?names[(n/3|0)%names.length]:[],hint:fr>.85?hints[4]:'',status:'frame '+cur.sent+' stored'},{bins:b,fraction:fr});
    if(fr>=.98||preview&&n>100)clearInterval(st)},80);
}
function demoDone(){clearInterval(st);go('summary');el('sumsheet').classList.add('busy');el('procbox').className='procbox ok';el('proctxt').textContent='Reading frames: 18 of 24';el('procbar').style.width='75%';el('procpct').textContent='75%';setTimeout(()=>renderSummary(MOCK_SUM),1600)}
function demoFind(q){const l=(MOCK_SUM.objects.find(o=>q.toLowerCase().includes(o.label))||{label:null}).label;
  if(!l)return renderFind({...MOCK_NOTFOUND,query:q,concept:q.replace(/^where is (my )?/i,'')});
  const o=MOCK_SUM.objects.find(x=>x.label===l)||MOCK_FIND.object;
  renderFind({...MOCK_FIND,concept:l,speech:l==='charger'?MOCK_FIND.speech:`The ${l} was last seen about ${Math.round(Math.abs(nd(o.yaw)))} degrees to the ${nd(o.yaw)<0?'left':'right'} of where the scan started.`,object:{id:o.id??9,label:l,yaw:o.yaw,pitch:o.pitch,n_obs:3,age_s:120}})}
function demoGuide(){ // the real guide() against the dragged pose, so the demo behaves like the phone
  clearInterval(gt);const t=guideTarget();if(!t)return;
  gt=setInterval(()=>{const g=guide(t,{yaw:cur.yaw,pitch:cur.pitch,roll:0,width:540,height:960,hfov:world.hfov},{uncertaintyDeg:t.uncertainty});setGuide({angle:g.arrowDeg,deg:g.distanceDeg,hint:g.instruction,found:g.arrived,coneDeg:g.coneDeg})},100);
}
boot();
