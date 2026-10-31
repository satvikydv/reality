// Camera HUD: compass tape, horizon, pitch ladder, and the world-locked overlay of remembered objects.
// The HUD furniture follows the design; objects are placed with the real projection (project.js) through the
// live pose and the server's FOV, so they stay on the things they were found on while the phone turns.
import {fit,ACCENT,rgba} from './ui.js';
import {coverFit,projectObject} from './project.js';
import {overlayConfig,showsHud} from './overlay-config.js';
import {selectLabels} from './calm.js';
import {cachedOutline} from './simplify.js';
import {directionToPixel} from './guide.js';
const A=ACCENT,G='#5EE6A0',MONO='JetBrains Mono,monospace',SANS='Sora,sans-serif';
export const nd=d=>((d%360)+540)%360-180;
const rr=(x,a,b,w,h,r)=>{x.beginPath();x.roundRect?x.roundRect(a,b,w,h,r):x.rect(a,b,w,h)};
const ease=t=>1-Math.pow(1-Math.min(1,Math.max(0,t)),3);

function brackets(x,px,py,sz,col,lw=2){const h=sz/2,L=Math.min(14,sz/3);x.strokeStyle=col;x.lineWidth=lw;x.lineCap='round';
  [[-1,-1],[1,-1],[-1,1],[1,1]].forEach(([dx,dy])=>{x.beginPath();x.moveTo(px+dx*h,py+dy*(h-L));x.lineTo(px+dx*h,py+dy*h);x.lineTo(px+dx*(h-L),py+dy*h);x.stroke()})}
// Label chip (visual system): 30px tall, dark glass, a hairline, and for the target an accent outline with an 8px dot.
const CHIP_H=30;
function chip(x,t,sub,cx,y,col){const tg=col===A,dot=tg?16:0;x.font=`600 13px ${SANS}`;const a=x.measureText(t).width;x.font=`500 11px ${MONO}`;const b=sub?x.measureText(sub).width+8:0,W=a+b+24+dot;
  rr(x,cx-W/2,y,W,CHIP_H,CHIP_H/2);x.fillStyle='rgba(7,9,11,.82)';x.fill();x.lineWidth=1;x.strokeStyle=tg?'rgba(138,164,255,.48)':'rgba(255,255,255,.24)';x.stroke();
  const L=cx-W/2+12;if(tg){x.fillStyle=A;x.beginPath();x.arc(L+4,y+CHIP_H/2,4,0,7);x.fill()}
  x.textBaseline='middle';x.textAlign='left';x.font=`600 13px ${SANS}`;x.fillStyle='#f2f4f3';x.fillText(t,L+dot,y+CHIP_H/2+.5);
  if(sub){x.font=`500 11px ${MONO}`;x.fillStyle='rgba(242,244,243,.68)';x.fillText(sub,L+dot+a+8,y+CHIP_H/2+.5)}}
const AMBER='#FFD54F';
// Text in a rounded bubble, wrapped to at most three lines. `above`: y is where the bubble ends, not where it starts.
function bubble(x,text,cx,y,col,{above=false,maxW=210}={}){
  x.font=`500 12px ${SANS}`;const lines=[];let line='';
  for(const w of String(text).split(' ')){const t=line?line+' '+w:w;if(x.measureText(t).width>maxW&&line){lines.push(line);line=w}else line=t}
  if(line)lines.push(line);const L=lines.slice(0,3);if(lines.length>3)L[2]=L[2].slice(0,-2)+'…';
  const W=Math.min(maxW,Math.max(...L.map(l=>x.measureText(l).width)))+20,H=L.length*16+14,top=above?y-H:y,bx=cx-W/2;
  rr(x,bx,top,W,H,12);x.fillStyle='rgba(7,9,11,.86)';x.fill();x.lineWidth=1.5;x.strokeStyle=col;x.stroke();
  x.fillStyle='#f2f4f3';x.textAlign='left';x.textBaseline='middle';L.forEach((l,i)=>x.fillText(l,bx+10,top+15+i*16));return H}

// Notes people left in the room, at the direction they were left (or on the object they were left on).
function notes(x,w,s,at){
  for(const o of s.notes||[]){const p=at(o.yaw,o.pitch);if(!p||p[0]<24||p[0]>w-24||p[1]<s.top+30||p[1]>s.h-s.bot)continue;
    x.fillStyle=AMBER;x.beginPath();x.arc(p[0],p[1],5,0,7);x.fill();x.strokeStyle='rgba(7,9,11,.85)';x.lineWidth=2;x.stroke();
    bubble(x,(o.author?o.author+': ':'')+o.text,Math.min(w-110,Math.max(110,p[0])),p[1]-12,AMBER,{above:true})}}

// Ghosts: what an earlier scan had in a direction where the room's memory has nothing now.
function ghosts(x,w,s,at){
  for(const g of s.ghosts||[]){const p=at(g.yaw,g.pitch);if(!p||p[0]<24||p[0]>w-24||p[1]<s.top||p[1]>s.h-s.bot-40)continue;
    x.save();x.globalAlpha=g.unsure?.55:.95;x.setLineDash([5,5]);x.strokeStyle='rgba(255,255,255,.9)';x.lineWidth=2;x.beginPath();x.arc(p[0],p[1],28,0,7);x.stroke();x.setLineDash([]);
    chip(x,g.label,g.unsure?'was here?':'was here',Math.min(w-62,Math.max(62,p[0])),p[1]+36,'rgba(255,255,255,.92)');x.restore()}}

// Arrow at the screen edge toward something that is out of view; dx, dy are degrees right and up of the view centre.
function edge(x,w,s,dx,dy,col,label,deg,t){const vx=dx*s.ppd,vy=-dy*s.ppd,hw=w/2-28,hh=vy>0?s.h-s.bot-s.cy:s.cy-s.top,
  f=Math.min(hw/(Math.abs(vx)||1e-6),hh/(Math.abs(vy)||1e-6)),px=w/2+vx*f,py=s.cy+vy*f,a=Math.atan2(vy,vx),p=(Math.sin(t*5)+1)/2;
  x.save();x.translate(px,py);x.rotate(a);x.fillStyle=col;x.shadowColor=col;x.shadowBlur=14;
  [[0,1],[-12,.45]].forEach(([o,al])=>{x.globalAlpha=al*(.7+.3*p);x.beginPath();x.moveTo(o+12,0);x.lineTo(o-5,-10);x.lineTo(o-5,10);x.closePath();x.fill()});x.restore();
  x.globalAlpha=1;chip(x,label,Math.round(deg)+'°',Math.min(w-62,Math.max(62,px-Math.cos(a)*62)),py-12-Math.sin(a)*22,col)}

function tape(x,w,s){const y=s.tapeY,H=40,pt=5,half=w/2/pt+2;
  rr(x,10,y,w-20,H,16);x.fillStyle='rgba(9,11,13,.68)';x.fill();x.lineWidth=1;x.strokeStyle='rgba(255,255,255,.14)';x.stroke();
  x.save();rr(x,11,y,w-22,H,16);x.clip();
  for(let d=Math.floor((s.yaw-half)/5)*5;d<=s.yaw+half;d+=5){const n=((d%360)+360)%360,px=w/2+(d-s.yaw)*pt,maj=n%30===0,mid=n%10===0;
    if(s.bins&&mid){const i=n/10,gp=s.gapBins&&s.gapBins.has(i);x.fillStyle=s.bins[i]?G:gp?rgba(A,.7):'rgba(255,255,255,.12)';x.fillRect(px,y+H-5,pt*10-1,3)}
    x.strokeStyle=n===0?A:`rgba(255,255,255,${maj?.85:mid?.45:.2})`;x.lineWidth=1;x.beginPath();x.moveTo(px,y+7);x.lineTo(px,y+7+(maj?12:mid?7:4));x.stroke();
    if(maj){x.fillStyle=n===0?A:'rgba(255,255,255,.85)';x.font=`500 10px ${MONO}`;x.textAlign='center';x.textBaseline='alphabetic';x.fillText(n===0?'N':n,px,y+H-11)}}
  (s.markers||[]).forEach(o=>{const dx=nd(o.yaw-s.yaw);if(Math.abs(dx)*pt>w/2-18)return;const px=w/2+dx*pt;x.fillStyle=o.hl?A:'rgba(255,255,255,.9)';x.beginPath();x.moveTo(px,y+3);x.lineTo(px+4,y+7);x.lineTo(px,y+11);x.lineTo(px-4,y+7);x.fill()});
  x.restore();
  x.strokeStyle=A;x.lineWidth=2;x.beginPath();x.moveTo(w/2,y-3);x.lineTo(w/2,y+H+3);x.stroke();
  const yy=String(Math.round(((s.yaw%360)+360)%360)).padStart(3,'0')+'°';rr(x,w/2-32,y+H+6,64,26,13);x.fillStyle='rgba(7,9,11,.82)';x.fill();x.strokeStyle='rgba(138,164,255,.48)';x.lineWidth=1;x.stroke();
  x.fillStyle=A;x.font=`700 12px ${MONO}`;x.textAlign='center';x.textBaseline='middle';x.fillText(yy,w/2,y+H+19.5)}

function ladder(x,w,s){const lx=w-14,ppd=s.ppd;
  for(let p=-60;p<=60;p+=10){const py=s.cy-(p-s.pitch)*ppd,d=Math.abs(py-s.cy);if(d>120)continue;const al=1-d/130;
    x.strokeStyle=`rgba(255,255,255,${.7*al})`;x.lineWidth=1;x.beginPath();x.moveTo(lx,py);x.lineTo(lx-(p%20?6:11),py);x.stroke();
    if(p%20===0){x.fillStyle=`rgba(255,255,255,${.75*al})`;x.font=`500 10px ${MONO}`;x.textAlign='right';x.textBaseline='middle';x.fillText((p>0?'+':'')+p,lx-15,py)}}
  x.fillStyle=A;x.beginPath();x.moveTo(lx-20,s.cy);x.lineTo(lx-30,s.cy-5);x.lineTo(lx-30,s.cy+5);x.fill()}

const bbox=pts=>{let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;for(const[a,b]of pts){if(a<x0)x0=a;if(a>x1)x1=a;if(b<y0)y0=b;if(b>y1)y1=b}return{x0,y0,x1,y1}};

// Outlines of the remembered objects, locked to the world. Returns how many were drawn and how many were hidden.
function world(x,w,s,cur,fitv,t){
  let drawn=0,hidden=0;const hasHl=s.world.some(o=>o.hl),soft=s.world.length<=16; // canvas shadows are the costly part: only when few outlines
  for(const o of s.world){
    const pr=projectObject(o,cur,fitv);
    const dx=nd(o.yaw-s.yaw),dy=o.pitch-s.pitch;
    if(!pr){hidden++;if(o.hl&&s.mode!=='scan')edge(x,w,s,dx,dy,A,o.label,Math.hypot(dx,dy),t);continue}
    const bb=pr.outline?bbox(pr.outline):null,[ax,ay]=pr.anchor||[(bb.x0+bb.x1)/2,(bb.y0+bb.y1)/2];
    const on=bb?(bb.x1>0&&bb.x0<w&&bb.y1>s.top&&bb.y0<s.h-s.bot):(ax>0&&ax<w&&ay>s.top&&ay<s.h-s.bot);
    if(!on){hidden++;if(o.hl&&s.mode!=='scan')edge(x,w,s,dx,dy,A,o.label,Math.hypot(dx,dy),t);continue}
    const born=o.born!=null?(s.now-o.born)/700:2,fresh=born<1;
    const col=o.hl||fresh?A:(s.mode==='scan'?G:'rgba(255,255,255,.92)');
    x.save();x.globalAlpha=pr.alpha*(fresh?.4+.6*ease(born):(o.hl?1:hasHl?.45:1));
    if(pr.outline&&s.detail!=='labels'){ // "labels only" (Settings, overlay detail) skips the outlines
      x.beginPath();pr.outline.forEach(([px,py],i)=>i?x.lineTo(px,py):x.moveTo(px,py));x.closePath();
      x.fillStyle=col===A?rgba(A,fresh?.16*(1-born):.10):'rgba(255,255,255,.07)';x.fill();
      x.lineJoin='round';x.lineWidth=o.hl?3:2;x.strokeStyle=col;x.setLineDash(pr.source==='footprint'?[6,5]:[]);if(soft){x.shadowColor='rgba(0,0,0,.55)';x.shadowBlur=5}x.stroke();x.setLineDash([]);x.shadowBlur=0;
    }
    if(o.hl){const p=(t*1.4)%1;x.strokeStyle=A;x.globalAlpha*=1-p;x.lineWidth=2;x.beginPath();x.arc(ax,ay,30+p*40,0,7);x.stroke();x.globalAlpha=pr.alpha}
    if(pr.anchor){x.fillStyle=col;x.beginPath();x.arc(ax,ay,o.hl?3.5:2.5,0,7);x.fill()}
    const ly=bb?Math.min(bb.y1+6,s.h-s.bot-30):ay+28;
    chip(x,o.label,(s.detail==='debug'?Math.round((o.confidence??0)*100)+'% ±'+Math.round(o.uncertainty_deg??0)+'°':o.hl?Math.round(Math.hypot(dx,dy))+'°':''),Math.min(w-50,Math.max(50,bb?(bb.x0+bb.x1)/2:ax)),Math.max(s.top,ly),col===G?G:col);
    x.restore();drawn++;
  }
  return{drawn,hidden};
}

// Calm overlay: a handful of labels chosen by confidence x centrality x recency (calm.js, thresholds in overlay-config.js), duplicates
// grouped ("3 bottles"), labels kept apart, and ONE outline: the Find / Guide target, else the object under the reticle, with its mask
// simplified and rounded. Everything else stays quiet.
function calmWorld(x,w,s,cur,fitv,t){
  const cfg=overlayConfig('calm'),view={w,h:s.h,top:s.top,bot:s.bot};
  const items=s.world.map(o=>{const pr=projectObject(o,cur,fitv,{maxUncertainty:999,outline:false});return{o,at:pr&&pr.anchor}}); // positions only: no mask is warped for objects that will not be outlined
  const r=selectLabels(items,view,cfg);
  for(const l of r.labels){
    x.save();x.globalAlpha=l.target?1:.92;
    x.fillStyle=l.target?A:'rgba(255,255,255,.9)';x.beginPath();x.arc(l.at[0],l.at[1],l.target?3.5:2.5,0,7);x.fill(); // what the label belongs to
    chip(x,l.text,'',l.x+l.w/2,l.y,l.target?A:'rgba(255,255,255,.92)');x.restore()}
  if(r.outlineId!=null){
    const o=s.world.find(q=>q.id===r.outlineId);
    if(o){const o2=o.mask&&o.mask.poly?{...o,mask:{...o.mask,poly:cachedOutline(o.id,o.mask.poly,cfg.outline)}}:o,pr=projectObject(o2,cur,fitv,{maxUncertainty:999});
      if(pr&&pr.outline){const col=o.hl?A:'rgba(255,255,255,.95)';
        x.save();x.globalAlpha=pr.alpha;x.beginPath();pr.outline.forEach(([px,py],i)=>i?x.lineTo(px,py):x.moveTo(px,py));x.closePath();
        x.fillStyle=o.hl?rgba(A,.12):'rgba(255,255,255,.06)';x.fill();x.lineJoin='round';x.lineWidth=o.hl?3:2;x.strokeStyle=col;x.setLineDash(pr.source==='footprint'?[6,5]:[]);
        x.shadowColor='rgba(0,0,0,.55)';x.shadowBlur=5;x.stroke();x.restore()}
      if(o.hl&&pr&&pr.anchor){const p=(t*1.4)%1;x.save();x.strokeStyle=A;x.globalAlpha=1-p;x.lineWidth=2;x.beginPath();x.arc(pr.anchor[0],pr.anchor[1],30+p*40,0,7);x.stroke();x.restore()}}}
  return{drawn:r.shown,hidden:s.world.length-r.shown,labels:r.labels.length,hiddenBy:r.hidden,calm:r};
}

// An answer to "Ask the room": only the objects it names, each outlined and labelled with the reason; the others are not drawn. One off screen
// gets an arrow toward it.
function askedWorld(x,w,s,cur,fitv,t){
  let drawn=0;
  for(const o of s.world){const reason=s.asked.get(o.id);if(reason===undefined)continue;
    const pr=projectObject(o,cur,fitv,{maxUncertainty:999}),dx=nd(o.yaw-s.yaw),dy=o.pitch-s.pitch;
    const on=pr&&pr.anchor&&pr.anchor[0]>0&&pr.anchor[0]<w&&pr.anchor[1]>s.top&&pr.anchor[1]<s.h-s.bot;
    if(!on){edge(x,w,s,dx,dy,A,o.label,Math.hypot(dx,dy),t);continue}
    if(pr.outline){x.save();x.globalAlpha=pr.alpha;x.beginPath();pr.outline.forEach(([px,py],i)=>i?x.lineTo(px,py):x.moveTo(px,py));x.closePath();
      x.fillStyle=rgba(A,.14);x.fill();x.lineJoin='round';x.lineWidth=3;x.strokeStyle=A;x.shadowColor='rgba(0,0,0,.55)';x.shadowBlur=5;x.stroke();x.restore()}
    const[ax,ay]=pr.anchor,cx=Math.min(w-60,Math.max(60,ax)),ly=Math.min(ay+14,s.h-s.bot-CHIP_H-70);
    x.fillStyle=A;x.beginPath();x.arc(ax,ay,3.5,0,7);x.fill();
    chip(x,o.label,'',cx,ly,A);if(reason)bubble(x,reason,cx,ly+CHIP_H+6,'rgba(138,164,255,.48)');
    drawn++}
  return{drawn,hidden:s.world.length-drawn,labels:drawn,asked:true}}

// s: {mode, now, yaw, pitch, roll, view:{width,height,hfov} (the camera image), world:[objects], showWorld, detail ('calm' | 'normal' | 'debug'), bins, gapBins, gap,
//     markers, target, found, label, pulse, shutter, tapeY, top, bot}
export function drawAR(c,s){
  const{x,w,h}=fit(c);s.w=w;s.h=h;s.cy=h/2;const t=s.now/1000,v=s.view;
  const fitv=coverFit(v.width,v.height,w,h),cur={yaw:s.yaw,pitch:s.pitch,roll:s.roll||0,width:v.width,height:v.height,hfov:v.hfov};
  s.ppd=(v.width/2/Math.tan(v.hfov*Math.PI/360))*fitv.s*Math.PI/180; // screen pixels per degree at the image centre
  const at=(yaw,pitch)=>{const p=directionToPixel(yaw,pitch,v.width,v.height,cur.yaw,cur.pitch,cur.roll,v.hfov);return p&&[fitv.ox+p[0]*fitv.s,fitv.oy+p[1]*fitv.s]};
  const hud=showsHud(s.detail,s.mode); // Calm: the tape, horizon and ladder only while guiding
  if(hud)tape(x,w,s);
  // horizon: where pitch 0 is, tilted with the phone's roll
  const h1=hud&&at(s.yaw-35,0),h2=hud&&at(s.yaw+35,0);
  if(h1&&h2){x.setLineDash([3,7]);x.strokeStyle='rgba(255,255,255,.28)';x.lineWidth=1;x.beginPath();x.moveTo(h1[0],h1[1]);x.lineTo(h2[0],h2[1]);x.stroke();x.setLineDash([])}
  if(hud)ladder(x,w,s);
  s.stats=s.showWorld?(s.asked&&s.asked.size?askedWorld(x,w,s,cur,fitv,t):s.detail==='calm'?calmWorld(x,w,s,cur,fitv,t):world(x,w,s,cur,fitv,t)):{drawn:0,hidden:0};
  s.stats.hud=hud;
  if(s.showWorld){ghosts(x,w,s,at);notes(x,w,s,at)}
  // scan: gap indicator
  if(s.mode==='scan'&&s.gap){const dx=nd(s.gap.mid-s.yaw);if(Math.abs(dx)>v.hfov/2-4)edge(x,w,s,dx,0,A,'gap',s.gap.size,t)}
  // guide target
  if(s.mode==='guide'&&s.target){const dx=nd(s.target.yaw-s.yaw),dy=s.target.pitch-s.pitch,d=Math.hypot(dx,dy),pt=at(s.target.yaw,s.target.pitch),col=s.found?G:A;
    if(pt&&pt[0]>20&&pt[0]<w-20&&pt[1]>s.top&&pt[1]<h-s.bot){const[px,py]=pt,r=s.found?34:Math.max(34,d*s.ppd*.9);
      if(s.target.uncertainty>0){x.save();x.strokeStyle=rgba(col,.55);x.fillStyle=rgba(col,.10);x.lineWidth=1.5;x.setLineDash([2,5]);x.beginPath();x.arc(px,py,Math.max(14,s.target.uncertainty*s.ppd),0,7);x.fill();x.stroke();x.restore()}x.strokeStyle=col;x.shadowColor=col;x.shadowBlur=16;x.lineWidth=2;x.setLineDash(s.found?[]:[6,6]);x.beginPath();x.arc(px,py,r,0,7);x.stroke();x.setLineDash([]);x.shadowBlur=0;
      brackets(x,px,py,s.found?70:46,col,3);chip(x,s.label||'target',s.found?'FOUND':Math.round(d)+'°',px,py+(s.found?48:34),col)}
    else edge(x,w,s,dx,dy,A,s.label||'target',d,t)}
  // reticle: the camera's optical centre
  const rcx=w/2,rcy=s.cy,pl=s.pulse||0;x.strokeStyle=s.found?G:'rgba(255,255,255,.9)';x.lineWidth=1.5;x.shadowColor='#000';x.shadowBlur=4;
  [[0,-1],[1,0],[0,1],[-1,0]].forEach(([a,b])=>{x.beginPath();x.moveTo(rcx+a*13,rcy+b*13);x.lineTo(rcx+a*22,rcy+b*22);x.stroke()});x.shadowBlur=0;
  x.fillStyle=A;x.beginPath();x.arc(rcx,rcy,2,0,7);x.fill();
  if(s.mode==='scan'&&pl>0){x.strokeStyle=`rgba(94,230,160,${pl})`;x.lineWidth=2;x.beginPath();x.arc(rcx,rcy,28+(1-pl)*22,0,7);x.stroke()}
  // shutter sweep
  if(s.shutter<1){const p=s.shutter,sy=s.top+(s.h-s.bot-s.top)*p,g=x.createLinearGradient(0,sy-70,0,sy);g.addColorStop(0,'rgba(94,230,160,0)');g.addColorStop(1,`rgba(94,230,160,${.3*(1-p)})`);x.fillStyle=g;x.fillRect(0,sy-70,w,70);x.fillStyle=`rgba(94,230,160,${.9*(1-p)})`;x.fillRect(0,sy,w,1.5)}
}

// The dial's cone is the object's direction uncertainty (half-angle, degrees); at least a few degrees so it stays visible.
export function drawBearing(c,yaw,pitch,uncertaintyDeg=0){const{x,w,h}=fit(c),cx=w/2,cy=h/2,R=w/2-6,a=(yaw-90)*Math.PI/180,half=Math.max(4,Math.min(60,uncertaintyDeg))*Math.PI/180;
  x.strokeStyle='rgba(255,255,255,.25)';x.lineWidth=1;x.beginPath();x.arc(cx,cy,R,0,7);x.stroke();
  for(let d=0;d<360;d+=90){const b=(d-90)*Math.PI/180;x.beginPath();x.moveTo(cx+Math.cos(b)*(R-5),cy+Math.sin(b)*(R-5));x.lineTo(cx+Math.cos(b)*R,cy+Math.sin(b)*R);x.stroke()}
  x.fillStyle=A;x.font=`700 10px ${MONO}`;x.textAlign='center';x.textBaseline='middle';x.fillText('N',cx,cy-R+11);
  x.beginPath();x.moveTo(cx,cy);x.arc(cx,cy,R-4,a-half,a+half);x.closePath();x.fillStyle=rgba(A,.22);x.fill();
  x.strokeStyle=A;x.lineWidth=2;x.beginPath();x.moveTo(cx,cy);x.lineTo(cx+Math.cos(a)*(R-6),cy+Math.sin(a)*(R-6));x.stroke();
  x.beginPath();x.arc(cx+Math.cos(a)*(R-6),cy+Math.sin(a)*(R-6),5,0,7);x.fill();x.beginPath();x.arc(cx,cy,3,0,7);x.fillStyle='#fff';x.fill()}
