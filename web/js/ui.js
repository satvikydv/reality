const A='#FFD54F',G='#5EE6A0',TAU=Math.PI*2,rad=d=>(d-90)*Math.PI/180;
export function fit(c){const d=Math.min(devicePixelRatio||1,2),r=c.getBoundingClientRect();if(c.width!==Math.round(r.width*d)){c.width=Math.round(r.width*d);c.height=Math.round(r.height*d)}const x=c.getContext('2d');x.setTransform(d,0,0,d,0,0);x.clearRect(0,0,r.width,r.height);return{x,w:r.width,h:r.height}}

// Coverage compass ring: 36 segments, N marker, heading dot + view cone.
export function drawRing(c,bins,yaw,pulse=0){
  const{x,w,h}=fit(c),cx=w/2,cy=h/2,R=Math.min(w,h)/2-18,T=16;
  x.lineCap='butt';
  for(let i=0;i<36;i++){
    const a0=rad(i*10+1.2),a1=rad(i*10+8.8),on=bins&&bins[i];
    x.beginPath();x.arc(cx,cy,R,a0,a1);x.lineWidth=T;
    x.strokeStyle=on?G:'rgba(255,255,255,.2)';x.globalAlpha=on?.95:1;x.stroke();
    if(on){x.beginPath();x.arc(cx,cy,R+T/2+3,a0,a1);x.lineWidth=1.5;x.strokeStyle=G;x.stroke()}
  }
  x.globalAlpha=1;
  // inner hairline + ticks every 30deg
  x.strokeStyle='rgba(255,255,255,.25)';x.lineWidth=1;x.beginPath();x.arc(cx,cy,R-T/2-10,0,TAU);x.stroke();
  for(let d=0;d<360;d+=30){const a=rad(d),r1=R-T/2-10,r2=r1-(d%90?5:10);x.beginPath();x.moveTo(cx+Math.cos(a)*r1,cy+Math.sin(a)*r1);x.lineTo(cx+Math.cos(a)*r2,cy+Math.sin(a)*r2);x.stroke()}
  // view cone
  const ya=rad(yaw);x.beginPath();x.moveTo(cx,cy);x.arc(cx,cy,R-T/2-10,rad(yaw-30),rad(yaw+30));x.closePath();
  const g=x.createRadialGradient(cx,cy,0,cx,cy,R);g.addColorStop(0,'rgba(255,213,79,0)');g.addColorStop(1,'rgba(255,213,79,.22)');x.fillStyle=g;x.fill();
  // N marker
  x.fillStyle=A;x.beginPath();const ny=cy-R-T/2-4;x.moveTo(cx,ny+1);x.lineTo(cx-6,ny-10);x.lineTo(cx+6,ny-10);x.closePath();x.fill();
  x.font='700 12px JetBrains Mono,monospace';x.textAlign='center';x.fillText('N',cx,ny-16);
  // heading dot
  const hx=cx+Math.cos(ya)*R,hy=cy+Math.sin(ya)*R;
  x.beginPath();x.arc(hx,hy,13+pulse*10,0,TAU);x.fillStyle=`rgba(255,213,79,${.25*(1-pulse)+.12})`;x.fill();
  x.beginPath();x.arc(hx,hy,8,0,TAU);x.fillStyle=A;x.fill();x.lineWidth=3;x.strokeStyle='#07090b';x.stroke();
}

// Radial room map. yaw clockwise from scan start (top), pitch up = toward centre.
export function drawMap(c,objs,o={}){
  const{x,w,h}=fit(c),cx=w/2,cy=h/2,R=Math.min(w,h)/2-(o.pad??34),big=o.big;
  x.lineWidth=1;x.strokeStyle='rgba(255,255,255,.14)';
  for(const f of[.33,.66,1]){x.beginPath();x.arc(cx,cy,R*f,0,TAU);x.stroke()}
  for(let d=0;d<360;d+=30){const a=rad(d);x.beginPath();x.moveTo(cx+Math.cos(a)*R*.12,cy+Math.sin(a)*R*.12);x.lineTo(cx+Math.cos(a)*R,cy+Math.sin(a)*R);x.strokeStyle=d%90?'rgba(255,255,255,.06)':'rgba(255,255,255,.16)';x.stroke()}
  x.fillStyle=A;x.font=`700 ${big?20:12}px JetBrains Mono,monospace`;x.textAlign='center';x.textBaseline='middle';x.fillText('N',cx,cy-R-(big?22:14));
  x.fillStyle='rgba(255,255,255,.4)';x.font=`400 ${big?15:10}px JetBrains Mono,monospace`;
  [['E',90],['S',180],['W',270]].forEach(([t,d])=>x.fillText(t,cx+Math.cos(rad(d))*(R+(big?22:14)),cy+Math.sin(rad(d))*(R+(big?22:14))));
  // sweep wedge
  if(o.yaw!=null){x.beginPath();x.moveTo(cx,cy);x.arc(cx,cy,R,rad(o.yaw-30),rad(o.yaw+30));x.closePath();x.fillStyle='rgba(255,213,79,.12)';x.fill()}
  const placed=[];x.textBaseline='middle';
  objs.forEach(ob=>{
    const r=R*Math.max(.18,Math.min(.95,.62-(ob.pitch||0)/110)),a=rad(ob.yaw),px=cx+Math.cos(a)*r,py=cy+Math.sin(a)*r;
    const k=ob.hl?1:Math.min(1,(ob.age??1)),hl=ob.hl;
    x.globalAlpha=k;
    if(k<1||o.ping){x.beginPath();x.arc(px,py,(big?7:5)+(1-k)*22,0,TAU);x.strokeStyle=A;x.globalAlpha=1-k;x.stroke();x.globalAlpha=k}
    x.beginPath();x.arc(px,py,hl?(big?9:7):(big?6:4.5),0,TAU);x.fillStyle=hl?A:G;x.fill();
    if(hl){x.beginPath();x.arc(px,py,(big?18:13),0,TAU);x.strokeStyle=A;x.lineWidth=1.5;x.stroke();x.lineWidth=1}
    const right=Math.cos(a)>=0,lx=px+(right?1:-1)*(big?16:10);let ly=py;
    for(const p of placed)if(Math.abs(p-ly)<(big?20:13))ly+=(big?20:13);placed.push(ly);
    x.textAlign=right?'left':'right';x.font=`${hl?700:400} ${big?17:11}px Sora,sans-serif`;x.fillStyle=hl?A:'#f2f4f3';x.fillText(ob.label,lx,ly);x.globalAlpha=1;
  });
}

// Replay photo with animated box. t: 0..1 progress.
export function drawReplay(c,img,rp,t){
  const{x,w,h}=fit(c);
  if(img&&img.complete&&img.naturalWidth){const s=Math.max(w/img.naturalWidth,h/img.naturalHeight),iw=img.naturalWidth*s,ih=img.naturalHeight*s;x.drawImage(img,(w-iw)/2,(h-ih)/2,iw,ih);var sx=iw/img.naturalWidth,ox=(w-iw)/2,oy=(h-ih)/2}
  else{x.fillStyle='#14171a';x.fillRect(0,0,w,h);x.strokeStyle='rgba(255,255,255,.05)';for(let i=-h;i<w;i+=22){x.beginPath();x.moveTo(i,0);x.lineTo(i+h,h);x.stroke()}var sx=w/(rp.width||640),ox=0,oy=0}
  const e=1-Math.pow(1-Math.min(1,t),3),[a,b,c2,d]=rp.box,X=ox+a*sx,Y=oy+b*sx,W=(c2-a)*sx,H=(d-b)*sx;
  const pad=(1-e)*26,bx=X-pad,by=Y-pad,bw=W+pad*2,bh=H+pad*2,L=Math.min(22,bw/3,bh/3);
  x.fillStyle='rgba(0,0,0,'+.45*e+')';x.beginPath();x.rect(0,0,w,h);x.rect(bx,by,bw,bh);x.fill('evenodd');
  x.strokeStyle=A;x.lineWidth=3;x.lineCap='square';x.shadowColor='rgba(0,0,0,.6)';x.shadowBlur=6;
  [[bx,by,1,1],[bx+bw,by,-1,1],[bx,by+bh,1,-1],[bx+bw,by+bh,-1,-1]].forEach(([px,py,dx,dy])=>{x.beginPath();x.moveTo(px,py+dy*L);x.lineTo(px,py);x.lineTo(px+dx*L,py);x.stroke()});
  x.shadowBlur=0;x.lineWidth=1;x.globalAlpha=.5*e;x.strokeRect(bx,by,bw,bh);x.globalAlpha=1;
}
