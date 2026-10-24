// usage: node cdp.mjs URL waitMs out.png [keyAfterMs:key]
import {spawn} from 'child_process';import fs from 'fs';
const [url,wait,out,...keys]=process.argv.slice(2);
const ch=spawn('google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=9333','--window-size=640,800','about:blank'],{stdio:'ignore'});
await new Promise(r=>setTimeout(r,2500));
const tabs=await (await fetch('http://localhost:9333/json')).json();
const ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
await new Promise(r=>ws.onopen=r);
let id=0;const pend={};ws.onmessage=m=>{const d=JSON.parse(m.data);if(d.id&&pend[d.id])pend[d.id](d.result)};
const send=(method,params={})=>new Promise(r=>{pend[++id]=r;ws.send(JSON.stringify({id,method,params}))});
await send('Page.enable');await send('Page.navigate',{url});
const t0=Date.now();
for(const k of keys){const [ms,key,a,b]=k.split(':');await new Promise(r=>setTimeout(r,Math.max(0,+ms-(Date.now()-t0))));
  if(key.startsWith('m')){ // m:move/d:down/u:up at canvas pixel (a,b) of 320x400
    const r=(await send('Runtime.evaluate',{expression:'JSON.stringify(document.getElementById("c").getBoundingClientRect())',returnByValue:true})).result.value;const R=JSON.parse(r);
    const X=R.left+(+a+0.5)/320*R.width,Y=R.top+(+b+0.5)/400*R.height;
    await send('Input.dispatchMouseEvent',{type:key==='md'?'mousePressed':key==='mu'?'mouseReleased':'mouseMoved',x:X,y:Y,button:key==='mm'?'none':'left',clickCount:1})}
  else{await send('Input.dispatchKeyEvent',{type:'keyDown',key,code:key});await send('Input.dispatchKeyEvent',{type:'keyUp',key,code:key})}}
await new Promise(r=>setTimeout(r,Math.max(0,+wait-(Date.now()-t0))));
const t=await send('Runtime.evaluate',{expression:'document.title'});console.log('title',t.result.value);
const s=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(out,Buffer.from(s.data,'base64'));
ch.kill();process.exit(0);
