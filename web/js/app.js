import {drawRing,drawMap,drawReplay,drawPhoto} from './ui.js';
import {drawAR,drawBearing,nd} from './ar.js';
import {guide} from './guide.js';
import {WorldLayer} from './world.js';
const $=id=>document.getElementById(id),el=$,P=new URLSearchParams(location.search);
const body=document.body;let demo=P.has('demo'),scrn='home';
const cur={yaw:0,tyaw:0,pitch:0,tpitch:0,roll:0,bins:Array(36).fill(false),frac:0,pulse:0,sent:0,shutter:9,gap:null};
let summary=null,shownSummary=null,find=null,img=null,rt=0,mapT=0,arrowA=0,M={top:150,bot:200,tapeY:70};
const world=new WorldLayer({intervalMs:2000});
let arOn=true;try{arOn=localStorage.getItem('ros.ar')!=='0'}catch{}
const buzz=ms=>{try{navigator.vibrate&&navigator.vibrate(ms)}catch{}};
const speak=t=>{try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(t);u.rate=1;speechSynthesis.speak(u)}catch{}};
const toast=(m,ms=3500)=>{const e=el('toast');e.textContent=m;e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display='none',ms)};
function measure(){ // where the HUD may draw: below the top bar, above the bottom controls
  const hud=el('hud').getBoundingClientRect(),tp=document.querySelector(`#${scrn} .top`),bot=document.getElementById(scrn)?.querySelector('.bot')?.getBoundingClientRect();
  M.tapeY=tp?tp.getBoundingClientRect().bottom+4:70;M.top=M.tapeY+80;M.bot=bot?hud.height-bot.top+14:200}
function go(s){const was=scrn;scrn=s;body.dataset.screen=s;if(s==='find'){rt=0}if(s==='summary')mapT=0;if(was==='guide'&&s!=='guide')stopGuide();
  if(s==='home')renderQuick();syncWorld();requestAnimationFrame(measure)} // speak() after go(): leaving the guide cancels speech
addEventListener('resize',measure);
const showsWorld=()=>arOn&&(scrn==='home'||scrn==='scan'||scrn==='guide');
function syncWorld(){if(demo)return;if(showsWorld()||scrn==='find')world.start();else world.stop()} // keeps /api/overlay fresh only while it is on screen

/* ---------- ScanController callback ---------- */
function gapInfo(b){const n=36;let best=null;for(let i=0;i<n;i++){if(b[i]||!b[(i+n-1)%n])continue;let j=0;while(j<n&&!b[(i+j)%n])j++;if(j<n&&(!best||j>best.len))best={start:i,len:j}}
  return best&&{...best,mid:(best.start+best.len/2)*10,size:best.len*10}}
export function onChange(st,cov){
  if(cov){cur.bins=cov.bins;cur.frac=cov.fraction;cur.gap=cur.frac>.12?gapInfo(cur.bins):null}
  if(st.sent>cur.sent){cur.sent=st.sent;cur.pulse=1;cur.shutter=0;el('flash').classList.remove('go');void el('flash').offsetWidth;el('flash').classList.add('go');buzz(12)}
  if(st.sent<cur.sent)cur.sent=st.sent;
  const ph=st.phase,f=cur.frac;
  if(ph==='starting'||ph==='scanning'){if(scrn!=='scan'){if(demo&&scrn!=='home')return;go('scan')}el('scanstatus').textContent=ph==='starting'?'Starting':'Scanning'}
  if(ph==='finishing'){if(scrn!=='summary')go('summary');el('sumsheet').classList.add('busy');el('proctxt').textContent=st.status||'Reading frames…'}
  if(ph==='done'&&scrn==='scan')go('summary');
  if(ph==='idle'&&scrn==='scan')go('home');
  if(ph==='error'){toast(st.error||'Error');if(scrn==='scan'||scrn==='summary')go('home')}
  el('pct').innerHTML=Math.round(f*100)+'<i>%</i>';
  el('fcount').textContent=st.sent+' frames';el('nfr').textContent=st.sent;
  el('nob').textContent=st.objects??0;
  el('nnm').textContent=(st.names&&st.names.length)?st.names.join(' · '):'—';
  el('scanline').textContent=st.hint||coach(f);el('hint').textContent=ph==='scanning'?(st.status||''):'';
  if(st.summary&&st.summary!==shownSummary){shownSummary=st.summary;handleEvent({type:'scan_summary',...st.summary})}
}
function coach(f){if(cur.gap&&f>.5&&f<.97){const d=nd(cur.gap.mid-cur.yaw);return`Gap ${Math.round(cur.gap.size)}° to the ${d<0?'left':'right'}`}return f<.05?'Turn slowly to the left':f>.9?'Almost there':f>.6?'Hold steady':'Keep turning'}

/* ---------- server events ---------- */
export function handleEvent(m){
  const t=m.type||m.event;
  if(t==='scan_summary'){summary=m;renderSummary(m);world.refresh()}
  if(t==='find_result')renderFind(m);
  if(t==='guide_request'){renderFind(m.result);go('guide');startGuide()} // another agent sent this phone to an object
  if(t==='error')toast(m.message||'Server error');
}
function renderSummary(m){
  // Real summaries carry frames {received, stored, ...} and coverage {covered_deg, gaps}; the demo data uses plain numbers.
  const frames=typeof m.frames==='object'?m.frames.stored:m.frames,cover=typeof m.coverage==='object'?m.coverage.covered_deg/360:m.coverage;
  summary=m;el('sumsheet').classList.remove('busy');el('sumtext').textContent=m.speech||'';
  const c={};m.objects.forEach(o=>c[o.label]=(c[o.label]||0)+1);
  el('chips').replaceChildren(...Object.entries(c).map(([k,n])=>{const s=mk('span','chip'),e=mk('em',null,n);s.append(e,k);return s}));
  el('mf').textContent=frames;el('ms').textContent=Math.round(m.seconds);el('mc').textContent=Math.round((cover||0)*100)+'%';el('mu').textContent='$'+(m.cost_usd||0).toFixed(3);
  mapT=0;go('summary');speak(m.speech);buzz([20,40,20]);
}
function renderQuick(){
  const u=world.labels(2),q=el('quick');q.replaceChildren();
  el('memp').textContent=world.objects.length+' REMEMBERED';
  if(u.length)q.append(mk('span','label','Ask'));
  for(const l of u){const b=mk('button',null,l);b.onclick=()=>{el('q').value='where is my '+l;doFind(l)};q.append(b)}
  const ch=mk('button',null,'What changed?');ch.onclick=openChanges;q.append(ch);
}
const dirTxt=(y,p)=>{y=nd(y);return{yaw:Math.round(Math.abs(y))+'° '+(y<0?'L':'R'),pit:Math.round(Math.abs(p||0))+'° '+((p||0)>=0?'↑':'↓')}};
const ago=s=>s<90?'just now':s<5400?Math.round(s/60)+' min ago':s<172800?Math.round(s/3600)+' h ago':Math.round(s/86400)+' days ago';
function renderFind(m){
  find=m;el('resulttext').textContent=m.speech;el('resq').textContent='“'+(m.query||m.concept||'')+'”';
  const o=m.object||{},off=m.from_scan_start,d=dirTxt(off?off.dyaw:(o.yaw||0),off?off.dpitch:(o.pitch||0));el('dyaw').textContent=d.yaw;el('dpit').textContent=d.pit;
  el('dseen').textContent=o.n_obs!=null?`${o.n_obs} sighting${o.n_obs===1?'':'s'} · last ${ago(o.age_s??0)}`:'–';
  world.highlight(m.found!==false?o.id:null);
  el('rtag').textContent=((m.concept||'')+' · FRAME '+(m.replay?.frame_id??'–')).toUpperCase();
  el('guidebtn').style.display=m.found===false?'none':'';el('gobj').textContent=m.concept||'Object';
  img=null;if(m.replay&&!demo){img=new Image();img.src='/world/frame/'+m.replay.frame_id+'.jpg'}
  rt=0;go('find');speak(m.speech);buzz(25);
}

/* ---------- guide ---------- */
let found=false,lastBand=99;
export function setGuide({angle=0,deg=0,hint='',found:f=false}){
  let d=((angle-arrowA+540)%360)-180;arrowA+=d;
  el('arrow').style.transform=`rotate(${arrowA}deg)`;el('gnum').textContent=Math.round(deg);el('ghint').textContent=hint;
  const was=el('guide').classList.contains('found');found=f;
  el('guide').classList.toggle('found',f);if(f){el('ghint').textContent='Found it';el('gfound').style.display='block';if(!was){buzz([30,60,30,60,80]);speak('Found it')}}else el('gfound').style.display='none';
}
const guideTarget=()=>find?.object&&find.found!==false?{yaw:find.object.yaw,pitch:find.object.pitch||0,label:find.concept}:null;
// Live guidance: the stored anchor (compass yaw, pitch) against the current camera pose, ten times a second.
let guideT=null;
function startGuide(){
  stopGuide();found=false;lastBand=99;
  const t=guideTarget();if(!t||(!orient&&!demo))return toast('Nothing to guide to');
  if(demo)return demoGuide();
  let lastSpoken='',lastAt=0;
  const tick=()=>{
    const p=pose();if(!p)return setGuide({angle:0,deg:0,hint:'Waiting for the sensors'});
    const v=el('v'),g=guide(t,{yaw:p.yaw,pitch:p.pitch,roll:p.roll,width:v.videoWidth||540,height:v.videoHeight||960,hfov:world.hfov});
    setGuide({angle:g.arrowDeg,deg:g.distanceDeg,hint:g.instruction,found:g.arrived});
    const band=g.distanceDeg<6?0:g.distanceDeg<15?1:g.distanceDeg<30?2:3;if(band<lastBand&&band<3)buzz(band===0?40:12);lastBand=band;
    const now=performance.now();if(!g.arrived&&g.instruction!==lastSpoken&&now-lastAt>2500){lastSpoken=g.instruction;lastAt=now;speak(g.instruction)}
  };
  tick();guideT=setInterval(tick,100);
}
function stopGuide(){clearInterval(guideT);guideT=null;try{speechSynthesis.cancel()}catch{}}

/* ---------- changes ---------- */
// `d` is the /api/changes answer (same as the Reality API's what_changed). Model text only goes in via textContent.
const CHG_GROUPS=[['moved','↔','var(--accent)','Moved'],['removed','−','var(--red)','Removed'],['added','+','var(--green)','Added']];
function mk(tag,cls,txt){const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
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
async function openChanges(){
  if(demo){renderChanges(MOCK_CHG);go('changes');return speak(MOCK_CHG.summary)}
  let r;
  try{r=await(await fetch('/api/changes')).json()}catch{return toast('Server not reachable')}
  if(r.status!=='ok')return toast(r.message||r.detail||'Cannot compare yet');
  renderChanges(r);go('changes');speak(r.summary);buzz(25);
}
const SHOT={frame_id:1,box:[180,190,330,300],width:640,height:480};
const MOCK_CHG={status:'ok',scan_before:3,scan_after:4,summary:'The mug moved: it was 40 degrees left, now 80 degrees right. A water bottle is new. 1 object could not be checked because that part of the room was not scanned both times.',
  moved:[{label:'mug',speech:'The mug moved: it was 40 degrees left, near the laptop, now 80 degrees right, on the desk.',from:{replay:SHOT},to:{replay:SHOT}}],
  removed:[{label:'keys',speech:'The keys are gone; they were 150 degrees right, near the door.',replay:SHOT}],
  added:[{label:'bottle',speech:'A bottle is new, 40 degrees right, on the desk.',replay:SHOT}],
  unverified:[{label:'phone',reason:'that direction was not rescanned'}]};

/* ---------- pose and the AR overlay ---------- */
let ctl=null,sock=null,cam=null,orient=null,facing='environment',streaming=null;
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
function setAr(on){arOn=on;try{localStorage.setItem('ros.ar',on?'1':'0')}catch{}
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
  if(scrn==='home'||scrn==='scan'||scrn==='guide'){
    const ar=el('ar'),tg=guideTarget(),sc=scrn==='scan',t0=performance.now();
    const objs=world.objects;
    const s={mode:scrn,now,yaw:cur.yaw,pitch:cur.pitch,roll:cur.roll,view:{...viewSize(),hfov:world.hfov},world:objs,showWorld:arOn,
      bins:sc?cur.bins:null,gapBins:sc&&cur.gap?new Set(Array.from({length:cur.gap.len},(_,i)=>(cur.gap.start+i)%36)):null,gap:sc?cur.gap:null,
      markers:scrn==='guide'?(tg?[{yaw:tg.yaw,hl:true}]:[]):objs,target:tg,found,label:tg?.label,pulse:cur.pulse,shutter:cur.shutter,tapeY:M.tapeY,top:M.top,bot:M.bot};
    drawAR(ar,s);arStats=s.stats;drawMs+=((performance.now()-t0)-drawMs)*.1;
  }
  if(now-lastInfo>500){lastInfo=now;const s=window.__ar.stats();
    el('arinfo').textContent=`AR ${arOn?'on':'off'} · ${s.fps.toFixed(0)} fps · draw ${s.drawMs.toFixed(1)} ms · ${s.drawn} shown, ${s.hidden} hidden · FOV ${s.hfov}°${s.calibrated?'':' (not calibrated)'}`;
    if(scrn==='home'||scrn==='scan')el('memp').textContent=world.objects.length+' REMEMBERED'}
  if(scrn==='scan')drawRing(el('ring'),cur.bins,cur.yaw,cur.pulse);
  if(scrn==='summary'&&summary&&!el('sumsheet').classList.contains('busy')){mapT+=dt;drawMap(el('mapc'),summary.objects.map((o,i)=>({...o,age:Math.min(1,Math.max(0,mapT*2-i*.08))})))}
  if(scrn==='find'&&find?.replay){rt+=dt*1.6;drawReplay(el('replay'),img,find.replay,rt)}
  if(scrn==='find'){const o=find?.object||{};drawBearing(el('bear'),o.yaw||0,o.pitch||0)}
  requestAnimationFrame(frame)}
requestAnimationFrame(frame);

/* ---------- drag to look around (demo / desktop preview) ---------- */
let dragging=false,px=0,py=0;const ppd=()=>innerWidth/60;
addEventListener('pointerdown',e=>{if(!demo||e.target.closest('button,input,summary,details,.chg'))return;dragging=true;px=e.clientX;py=e.clientY});
addEventListener('pointermove',e=>{if(!dragging||scrn==='scan')return;cur.tyaw=(cur.tyaw-(e.clientX-px)/ppd()+360)%360;cur.tpitch=Math.max(-70,Math.min(70,cur.tpitch+(e.clientY-py)/ppd()));px=e.clientX;py=e.clientY});
addEventListener('pointerup',()=>dragging=false);

/* ---------- wiring ---------- */
const camMeta=()=>({facing,video_w:el('v').videoWidth,video_h:el('v').videoHeight,screen_angle:window.screen.orientation?.angle??null});
const active=()=>ctl&&['starting','scanning','finishing'].includes(ctl.phase);
function onSockState(s){
  const ok=s==='connected';el('stat').textContent=ok?'Connected':'Reconnecting…';el('dotc').classList.toggle('bad',!ok);
  if(!ok&&active())ctl.abort('Connection lost, the scan stopped. Objects found so far are kept.');
}
async function startCam(){
  try{await cam.startCamera(el('v'),facing)}
  catch(e){toast((e.name||'Camera')+': '+e.message)}
}
async function boot(){
  setAr(arOn);
  if(demo){body.classList.add('demo');return startDemo()}
  try{
    const [c,w,o,k,s]=await Promise.all(['camera','ws','orientation','keyframe','scan'].map(n=>import('/js/'+n+'.js')));
    cam=c;orient=new o.OrientationTracker();
    sock=new w.FrameSocket(r=>{if(r.type==='find_result'||r.type==='guide_request')return handleEvent(r);if(r.type)return ctl.onEvent(r)},onSockState);
    ctl=new s.ScanController({video:el('v'),orient,sock,grabJpeg:c.grabJpeg,camera:camMeta,onChange});
    orient.start().catch(()=>{});
    await startCam();
    world.start();renderQuick();measure();
  }catch(e){console.warn(e);demo=true;body.classList.add('demo');toast('Backend not found, demo mode');startDemo()}
}
el('scanbtn').onclick=async()=>{buzz(20);if(demo)return demoScan();if(!el('v').srcObject)await startCam();orient.start().catch(()=>{});summary=null;shownSummary=null;ctl.start()};
el('donebtn').onclick=()=>{if(demo)return demoDone();ctl.done()};
function doFind(label){
  const q=(typeof label==='string'?label:el('q').value).trim();if(!q&&!demo)return;
  if(demo)return demoFind(q);
  if(!sock.sendJson({type:'find',query:q}))toast('Not connected to the server');
}
el('findbtn').onclick=()=>doFind();el('q').onkeydown=e=>e.key==='Enter'&&doFind();
el('mic').onclick=()=>{const R=window.SpeechRecognition||window.webkitSpeechRecognition;if(!R)return toast('Voice input unavailable');const r=new R();r.onresult=e=>{el('q').value=e.results[0][0].transcript;doFind()};r.start()};
el('resback').onclick=el('cback').onclick=()=>go('home');el('sumback').onclick=()=>go('home');el('sumfind').onclick=()=>{go('home');el('q').focus()};
el('guidebtn').onclick=()=>{go('guide');startGuide()};el('gback').onclick=()=>{clearInterval(gt);go('find')};el('gfound').onclick=()=>{clearInterval(gt);world.highlight(null);go('home')};
el('artog').onclick=el('artog2').onclick=()=>setAr(!arOn);
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{const s=b.dataset.go;
  if(s==='summary'&&!summary)renderSummary(MOCK_SUM);else if(s==='summary')go('summary');else if(s==='find')renderFind(MOCK_FIND);else if(s==='guide'){if(!find)renderFind(MOCK_FIND);go('guide');startGuide()}
  else if(s==='changes'){renderChanges(MOCK_CHG);go('changes')}else if(s==='scan'){go('scan');demoScan(true)}else go(s);el('dev').open=false});
el('flipbtn').onclick=async()=>{if(!cam)return;facing=facing==='environment'?'user':'environment';await startCam()};
// dev: stream frames to the server (echo only) and save full-size samples with their orientation (M0 measurements)
el('streambtn').onclick=()=>{
  if(!sock)return;
  if(streaming){clearInterval(streaming);streaming=null;el('streambtn').textContent='Stream';return}
  el('streambtn').textContent='Stop';
  streaming=setInterval(async()=>{if(!sock.ready||sock.backlog>512*1024)return;const b=await cam.grabJpeg(el('v'),960,.8);if(b)sock.send(b)},500);
};
el('savebtn').onclick=async()=>{
  if(!sock||!sock.ready)return toast('Not connected to the server');
  const b=await cam.grabJpeg(el('v'),1920,.9);if(!b)return;
  await sock.send(b,{save:true,meta:{orientation:orient.snapshot(),camera:camMeta()}});toast('Sample saved');
};
window.__scan=()=>ctl;window.__ui={cur,go,world};

/* ---------- demo (open /?demo, or automatic when the backend is not reachable) ---------- */
const MOCK_SUM={speech:'I found 14 objects: 3 laptops, 3 cables, 2 water bottles, a charger, a mug, keys and a phone.',coverage:.97,frames:42,seconds:31,cost_usd:.084,objects:[['laptop',-20,5],['laptop',35,0],['laptop',200,8],['cable',60,-25],['cable',-75,-30],['cable',150,-20],['bottle',95,-5],['bottle',250,0],['charger',-60,-10],['mug',120,2],['keys',300,-15],['phone',10,-12],['monitor',-5,20],['lamp',330,35]].map(([label,yaw,pitch],i)=>({id:i+1,label,yaw:(yaw+360)%360,pitch,n_obs:3}))};
const MOCK_FIND={speech:'The charger was last seen about 60 degrees to the left of where the scan started, below the laptop.',concept:'charger',found:true,object:{id:9,label:'charger',yaw:300,pitch:-10,n_obs:3,age_s:120},replay:{frame_id:14,box:[180,190,330,300],width:640,height:480}};
// demo objects for the overlay: half with a mask outline captured facing them, half with only a footprint
function demoWorld(){
  const blob=(rx,ry)=>Array.from({length:10},(_,i)=>{const a=i/10*Math.PI*2;return[270+Math.cos(a)*rx*(1+.12*Math.sin(a*3)),480+Math.sin(a)*ry*(1+.12*Math.cos(a*2))]});
  world.apply({hfov_deg:P.has('fov')?+P.get('fov'):60,fov_calibrated:false,scan_id:3,objects:MOCK_SUM.objects.map((o,i)=>({
    id:o.id,label:o.label,yaw:o.yaw,pitch:o.pitch,uncertainty_deg:i===5?30:i%4,n_obs:o.n_obs,confidence:.9,age_s:[5,5,20,400,900,5,60,5,5,5,5,5,5,5][i],
    footprint:[o.yaw,-7,7,o.pitch-6,o.pitch+6],
    mask:i%2?null:{poly:blob(60+i*3,46),yaw:o.yaw,pitch:o.pitch,roll:0,width:540,height:960,hfov:60}}))});
}
let st=null,gt=null;
function startDemo(){
  demoWorld();renderQuick();measure();
  if(P.has('yaw'))cur.tyaw=cur.yaw=+P.get('yaw');if(P.has('pitch'))cur.tpitch=cur.pitch=+P.get('pitch');
  const s=P.get('screen');if(!s)return; // /?demo&screen=summary|find|guide|changes|scan jumps straight to a screen with mock data
  if(s==='summary')renderSummary(MOCK_SUM);else if(s==='find')renderFind(MOCK_FIND);else if(s==='guide'){renderFind(MOCK_FIND);go('guide');startGuide()}
  else if(s==='changes'){renderChanges(MOCK_CHG);go('changes')}else if(s==='scan'){go('scan');demoScan(true)}
}
function demoScan(preview){
  clearInterval(st);go('scan');Object.assign(cur,{bins:Array(36).fill(false),frac:0,sent:0,tyaw:0,yaw:0,tpitch:0,pitch:0,gap:null});
  const hints=['Slow down','Hold steady','Keep turning','Gap 40° to the left','Almost there'];let n=0,y=0;
  const names=[['laptop'],['mouse','cable'],['mug'],['bottle'],['charger'],['keys'],['phone'],['cable'],['lamp']];
  st=setInterval(()=>{n++;y=(y+3.6)%360;if(y>150&&y<200)y+=30;const b=cur.bins.slice();for(let i=0;i<36;i++)if(Math.abs(nd(i*10+5-y))<7)b[i]=true;
    const fr=b.filter(Boolean).length/36,show=n%3===0;cur.tyaw=y;
    onChange({phase:'scanning',sent:show?cur.sent+1:cur.sent,objects:Math.min(14,Math.floor(fr*16)),names:show?names[(n/3|0)%names.length]:[],hint:fr>.85?hints[4]:'',status:'frame '+cur.sent+' stored'},{bins:b,fraction:fr});
    if(fr>=.98||preview&&n>100)clearInterval(st)},80);
}
function demoDone(){clearInterval(st);go('summary');el('sumsheet').classList.add('busy');setTimeout(()=>renderSummary(MOCK_SUM),1600)}
function demoFind(q){const l=(MOCK_SUM.objects.find(o=>q.toLowerCase().includes(o.label))||{label:'charger'}).label,o=MOCK_SUM.objects.find(x=>x.label===l)||MOCK_FIND.object;
  renderFind({...MOCK_FIND,concept:l,speech:l==='charger'?MOCK_FIND.speech:`The ${l} was last seen about ${Math.round(Math.abs(nd(o.yaw)))} degrees to the ${nd(o.yaw)<0?'left':'right'} of where the scan started.`,object:{id:o.id??9,label:l,yaw:o.yaw,pitch:o.pitch,n_obs:3,age_s:120}})}
function demoGuide(){ // the real guide() against the dragged pose, so the demo behaves like the phone
  clearInterval(gt);const t=guideTarget();if(!t)return;
  gt=setInterval(()=>{const g=guide(t,{yaw:cur.yaw,pitch:cur.pitch,roll:0,width:540,height:960,hfov:world.hfov});setGuide({angle:g.arrowDeg,deg:g.distanceDeg,hint:g.instruction,found:g.arrived})},100);
}
boot();
