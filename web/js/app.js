import {drawRing,drawMap,drawReplay,drawPhoto} from './ui.js';
import {guide} from './guide.js';
const $=id=>document.getElementById(id),P=new URLSearchParams(location.search);
const body=document.body;let demo=P.has('demo'),scrn='home';
const cur={yaw:0,tyaw:0,bins:Array(36).fill(false),frac:0,pulse:0,sent:0};
let summary=null,shownSummary=null,find=null,img=null,rt=0,mapT=0,arrowA=0;
const buzz=ms=>{try{navigator.vibrate&&navigator.vibrate(ms)}catch{}};
const speak=t=>{try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(t);u.rate=1;speechSynthesis.speak(u)}catch{}};
const toast=m=>{const e=$('toast');e.textContent=m;e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display='none',3500)};
function go(s){const was=scrn;scrn=s;body.dataset.screen=s;if(s==='find'){rt=0;}if(s==='summary')mapT=0;if(was==='guide'&&s!=='guide')stopGuide()} // speak() after go(): leaving the guide cancels speech
const el=x=>document.getElementById(x);

/* ---------- ScanController callback ---------- */
export function onChange(st,cov){
  if(cov){cur.bins=cov.bins;cur.frac=cov.fraction}
  if(st.sent>cur.sent){cur.sent=st.sent;cur.pulse=1;el('flash').classList.remove('go');void el('flash').offsetWidth;el('flash').classList.add('go');buzz(12)}
  if(st.sent<cur.sent)cur.sent=st.sent;
  const ph=st.phase,f=cur.frac;
  if(ph==='starting'||ph==='scanning'){if(scrn!=='scan')go('scan');el('scanstatus').textContent=ph==='starting'?'Starting':'Scanning'}
  if(ph==='finishing'){if(scrn!=='summary')go('summary');el('sumsheet').classList.add('busy');el('proctxt').textContent=st.status||'Reading frames…'}
  if(ph==='done'&&scrn==='scan')go('summary');
  if(ph==='idle'&&scrn==='scan')go('home');
  if(ph==='error'){toast(st.error||'Error');if(scrn==='scan'||scrn==='summary')go('home')}
  el('pct').innerHTML=Math.round(f*100)+'<i>%</i>';
  el('fcount').textContent=st.sent+' frames';el('nfr').textContent=st.sent;
  el('nob').textContent=st.objects??0;
  el('nnm').textContent=(st.names&&st.names.length)?st.names.join(', '):'—';
  el('scanline').textContent=st.hint||coach(f);el('hint').textContent=ph==='scanning'?(st.status||''):'';
  if(st.summary&&st.summary!==shownSummary){shownSummary=st.summary;handleEvent({type:'scan_summary',...st.summary})}
}
function coach(f){return f<.05?'Turn slowly to the left':f>.9?'Almost there':f>.6?'Hold steady':'Keep turning'}

/* ---------- server events ---------- */
export function handleEvent(m){
  const t=m.type||m.event;
  if(t==='scan_summary'){summary=m;renderSummary(m)}
  if(t==='find_result')renderFind(m);
  if(t==='guide_request'){renderFind(m.result);go('guide');startGuide()} // another agent sent this phone to an object
  if(t==='error')toast(m.message||'Server error');
}
function renderSummary(m){
  // Real summaries carry frames {received, stored, ...} and coverage {covered_deg, gaps}; the demo data uses plain numbers.
  const frames=typeof m.frames==='object'?m.frames.stored:m.frames,cover=typeof m.coverage==='object'?m.coverage.covered_deg/360:m.coverage;
  summary=m;el('sumsheet').classList.remove('busy');el('sumtext').textContent=m.speech||'';
  const c={};m.objects.forEach(o=>c[o.label]=(c[o.label]||0)+1);
  el('chips').innerHTML=Object.entries(c).map(([k,n])=>`<span class="chip"><em>${n}</em>${k}</span>`).join('');
  el('mf').textContent=frames;el('ms').textContent=Math.round(m.seconds);el('mc').textContent=Math.round((cover||0)*100)+'%';el('mu').textContent='$'+(m.cost_usd||0).toFixed(3);
  mapT=0;go('summary');speak(m.speech);buzz([20,40,20]);
}
const dirTxt=(y,p)=>{y=((y+540)%360)-180;return{yaw:Math.round(Math.abs(y))+'° '+(y<0?'L':'R'),pit:Math.round(Math.abs(p||0))+'° '+((p||0)>=0?'↑':'↓')}};
function renderFind(m){
  find=m;el('resulttext').textContent=m.speech;el('resq').textContent='“'+(m.query||m.concept||'')+'”';
  const o=m.object||{},off=m.from_scan_start,d=dirTxt(off?off.dyaw:(o.yaw||0),off?off.dpitch:(o.pitch||0));el('dyaw').textContent=d.yaw;el('dpit').textContent=d.pit;
  el('rtag').textContent=((m.concept||'')+' · FRAME '+(m.replay?.frame_id??'–')).toUpperCase();
  el('guidebtn').style.display=m.found===false?'none':'';el('gobj').textContent=m.concept||'Object';
  img=null;if(m.replay&&!demo){img=new Image();img.src='/world/frame/'+m.replay.frame_id+'.jpg'}
  rt=0;go('find');speak(m.speech);buzz(25);
}

/* ---------- guide ---------- */
export function setGuide({angle=0,deg=0,hint='',found=false}){
  let d=((angle-arrowA+540)%360)-180;arrowA+=d;
  el('arrow').style.transform=`rotate(${arrowA}deg)`;el('gnum').textContent=Math.round(deg);el('ghint').textContent=hint;
  const was=el('guide').classList.contains('found');
  el('guide').classList.toggle('found',found);if(found){el('ghint').textContent='Found it';el('gfound').style.display='block';if(!was){buzz([30,60,30]);speak('Found it')}}else el('gfound').style.display='none';
}
// Live guidance: the stored anchor (compass yaw, pitch) against the current camera pose, ten times a second.
let guideT=null;
function startGuide(){
  stopGuide();
  const t=find?.object;if(!t||!orient)return toast('Nothing to guide to');
  let lastSpoken='',lastAt=0;
  const tick=()=>{
    const s=orient.latest.absolute||orient.latest.relative;if(!s)return setGuide({angle:0,deg:0,hint:'Waiting for the sensors'});
    const v=el('v'),roll=(s.roll||0)+(window.screen.orientation?.angle||0);
    const g=guide({yaw:t.yaw,pitch:t.pitch},{yaw:s.yaw,pitch:s.pitch,roll,width:v.videoWidth||540,height:v.videoHeight||960});
    setGuide({angle:g.arrowDeg,deg:g.distanceDeg,hint:g.instruction,found:g.arrived});
    const now=performance.now();if(!g.arrived&&g.instruction!==lastSpoken&&now-lastAt>2500){lastSpoken=g.instruction;lastAt=now;speak(g.instruction)}
  };
  tick();guideT=setInterval(tick,100);
}
function stopGuide(){clearInterval(guideT);guideT=null;try{speechSynthesis.cancel()}catch{}}

/* ---------- changes ---------- */
// `d` is the /api/changes answer (same as the Reality API's what_changed). Model text only goes in via textContent.
const CHG_GROUPS=[['moved','↔','var(--amber)','Moved'],['removed','−','var(--red)','Removed'],['added','+','var(--green)','Added']];
const mk=(tag,cls,txt)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e};
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

/* ---------- render loop ---------- */
let last=performance.now();
function frame(now){
  const dt=Math.min(.05,(now-last)/1000);last=now;
  const s=orient&&(orient.latest.absolute||orient.latest.relative);if(s)cur.tyaw=s.yaw;
  let d=((cur.tyaw-cur.yaw+540)%360)-180;cur.yaw=(cur.yaw+d*Math.min(1,dt*10)+360)%360;cur.pulse=Math.max(0,cur.pulse-dt*3);
  if(scrn==='scan')drawRing($('ring'),cur.bins,cur.yaw,1-cur.pulse<1?cur.pulse:0);
  if(scrn==='home')el('homeyaw').textContent='YAW '+String(Math.round(cur.yaw)).padStart(3,'0')+'°';
  if(scrn==='summary'&&summary&&!el('sumsheet').classList.contains('busy')){mapT+=dt;drawMap($('mapc'),summary.objects.map((o,i)=>({...o,age:Math.min(1,Math.max(0,mapT*2-i*.08))})))}
  if(scrn==='find'&&find?.replay){rt+=dt*1.6;drawReplay($('replay'),img,find.replay,rt)}
  requestAnimationFrame(frame)}
requestAnimationFrame(frame);

/* ---------- wiring ---------- */
let ctl=null,sock=null,cam=null,orient=null,facing='environment',streaming=null;
const camMeta=()=>({facing,video_w:el('v').videoWidth,video_h:el('v').videoHeight,screen_angle:window.screen.orientation?.angle??null});
const active=()=>ctl&&['starting','scanning','finishing'].includes(ctl.phase);
function onSockState(s){
  const ok=s==='connected';el('stat').textContent=ok?'Connected · ok':'Reconnecting…';el('dotc').classList.toggle('bad',!ok);
  if(!ok&&active())ctl.abort('Connection lost, the scan stopped. Objects found so far are kept.');
}
async function startCam(){
  try{await cam.startCamera(el('v'),facing)}
  catch(e){toast((e.name||'Camera')+': '+e.message)}
}
async function boot(){
  if(demo){body.classList.add('demo');return startDemo()}
  try{
    const [c,w,o,k,s]=await Promise.all(['camera','ws','orientation','keyframe','scan'].map(n=>import('/js/'+n+'.js')));
    cam=c;orient=new o.OrientationTracker();
    sock=new w.FrameSocket(r=>{if(r.type==='find_result'||r.type==='guide_request')return handleEvent(r);if(r.type)return ctl.onEvent(r)},onSockState);
    ctl=new s.ScanController({video:el('v'),orient,sock,grabJpeg:c.grabJpeg,camera:camMeta,onChange});
    orient.start().catch(()=>{});
    await startCam();
  }catch(e){console.warn(e);demo=true;body.classList.add('demo');toast('Backend not found, demo mode');startDemo()}
}
el('scanbtn').onclick=async()=>{buzz(20);if(demo)return demoScan();if(!el('v').srcObject)await startCam();orient.start().catch(()=>{});summary=null;shownSummary=null;ctl.start()};
el('donebtn').onclick=()=>{if(demo)return demoDone();ctl.done()};
const doFind=()=>{
  const q=el('q').value.trim();if(!q&&!demo)return;
  if(demo)return demoFind(q);
  if(!sock.sendJson({type:'find',query:q}))toast('Not connected to the server');
};
el('findbtn').onclick=doFind;el('q').onkeydown=e=>e.key==='Enter'&&doFind();
el('mic').onclick=()=>{const R=window.SpeechRecognition||window.webkitSpeechRecognition;if(!R)return toast('Voice input unavailable');const r=new R();r.onresult=e=>{el('q').value=e.results[0][0].transcript;doFind()};r.start()};
el('resback').onclick=el('cback').onclick=()=>go('home');el('sumback').onclick=()=>go('home');el('sumfind').onclick=()=>{go('home');el('q').focus()};
el('guidebtn').onclick=()=>{go('guide');if(demo)demoGuide();else startGuide()};el('gback').onclick=el('gfound').onclick=()=>{clearInterval(gt);go('find')};
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{const s=b.dataset.go;if(s==='summary'&&!summary)renderSummary(MOCK_SUM);else if(s==='find')renderFind(MOCK_FIND);else if(s==='guide'){go('guide');demoGuide()}else if(s==='changes'){renderChanges(MOCK_CHG);go('changes')}else if(s==='scan'){go('scan');demoScan(true)}else go(s);el('dev').open=false});
el('chgbtn').onclick=openChanges;
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
window.__scan=()=>ctl;window.__ui={cur,go};

/* ---------- demo (open /?demo, or automatic when the backend is not reachable) ---------- */
const MOCK_SUM={speech:'I found 14 objects: 3 laptops, 3 cables, 2 water bottles, a charger, a mug, keys and a phone.',coverage:.97,frames:42,seconds:31,cost_usd:.084,objects:[['laptop',-20,5],['laptop',35,0],['laptop',200,8],['cable',60,-25],['cable',-75,-30],['cable',150,-20],['bottle',95,-5],['bottle',250,0],['charger',-60,-10],['mug',120,2],['keys',300,-15],['phone',10,-12],['monitor',-5,20],['lamp',330,35]].map(([label,yaw,pitch])=>({label,yaw:(yaw+360)%360,pitch,n_obs:3}))};
const MOCK_FIND={speech:'The charger was last seen about 60 degrees to the left of where the scan started, below the laptop.',concept:'charger',found:true,object:{label:'charger',yaw:300,pitch:-10},replay:{frame_id:14,box:[180,190,330,300],width:640,height:480}};
let st=null,gt=null;
function startDemo(){ // /?demo&screen=summary|find|guide|changes|scan jumps straight to a screen with mock data
  const s=P.get('screen');if(!s)return;
  if(s==='summary')renderSummary(MOCK_SUM);else if(s==='find')renderFind(MOCK_FIND);else if(s==='guide'){go('guide');demoGuide()}
  else if(s==='changes'){renderChanges(MOCK_CHG);go('changes')}else if(s==='scan'){go('scan');demoScan(true)}
}
function demoScan(preview){
  clearInterval(st);go('scan');Object.assign(cur,{bins:Array(36).fill(false),frac:0,sent:0,tyaw:0,yaw:0});
  const hints=['Slow down','Hold steady','Keep turning','Gap 40° to the left','Almost there'];let n=0,y=0;
  const names=['laptop','mouse, cable','mug','bottle','charger','keys'];
  st=setInterval(()=>{n++;y=(y+4.2)%360;const b=cur.bins.slice();b[Math.floor(y/10)]=true;
    const fr=b.filter(Boolean).length/36;cur.tyaw=y;
    onChange({phase:'scanning',sent:n,objects:Math.min(14,Math.floor(fr*15)),names:[names[n%6]],hint:fr>.85?hints[4]:hints[Math.floor(n/12)%3],status:'frame '+n+' stored',yaw:y},{bins:b,fraction:fr});
    if(fr>=.98||preview&&n>60)clearInterval(st)},90);
}
function demoDone(){clearInterval(st);go('summary');el('sumsheet').classList.add('busy');setTimeout(()=>renderSummary(MOCK_SUM),1600)}
function demoFind(){renderFind(MOCK_FIND)}
function demoGuide(){clearInterval(gt);let a=0;const s=[[120,60,'Turn right'],[70,34,'Turn right'],[30,18,'Tilt up'],[-12,6,'Almost there, a little left'],[0,0,'',1]];setGuide({angle:150,deg:90,hint:'Turn right'});
  gt=setInterval(()=>{const x=s[a++];if(!x)return clearInterval(gt);setGuide({angle:x[0],deg:x[1],hint:x[2],found:!!x[3]})},1500)}
boot();
