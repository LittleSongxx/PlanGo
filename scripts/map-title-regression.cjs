// Real Chromium + actual PlanMap, with an isolated AMap API fixture. No merchant/model calls.
const { app, BrowserWindow, session } = require('electron')
const { build } = require('esbuild')
const { mkdtempSync, writeFileSync, rmSync } = require('node:fs')
const { join } = require('node:path')
const { tmpdir } = require('node:os')
const assert = require('node:assert/strict')
const work = mkdtempSync(join(tmpdir(), 'plango-map-title-'))
app.setPath('userData', join(work, 'profile'))
app.commandLine.appendSwitch('disable-gpu')
async function main() {
  const built = await build({ stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
import React from 'react'; import {createRoot} from 'react-dom/client'; import {PlanMap} from './src/renderer/src/components/PlanMap'; import {useStore} from './src/renderer/src/store';
const clicks=[],routes=[]; useStore.setState({coords:'106.532212,29.575499'});
window.AMap={Map:class{add(){}setFitView(){}destroy(){}},Pixel:class{},Marker:class{on(_event,fn){clicks.push(fn)}setMap(){}},Polyline:class{},Walking:class{search(a,b,cb){routes.push({mode:'walking',a,b});cb('complete',{routes:[{distance:120,time:120,steps:[{path:[a,b]}]}]})}},Driving:class{constructor(){throw new Error('PlanMap used driving for a walking plan')}},InfoWindow:class{
 constructor({content}){this.content=content}open(){const node=document.createElement('div');node.id='info';if(typeof this.content==='string')node.innerHTML=this.content;else node.appendChild(this.content);document.body.appendChild(node)}
}};
const attack='<img src="invalid-image" onerror="window.__mapInjected=true">标题 & 商家';
createRoot(document.getElementById('root')).render(<PlanMap plan={{origin:{name:'新的计划起点',longitude:106.575545,latitude:29.55772},travel_mode:'walking',nodes:[{title:attack,poi:{lng:106.57,lat:29.56}}]}}/>);
window.__mapTest=(async()=>{for(let i=0;i<50&&!clicks.length;i++)await new Promise(r=>setTimeout(r,20));if(clicks.length<2)throw new Error('Map markers did not render');clicks[1]();await new Promise(r=>setTimeout(r,80));return {injected:!!window.__mapInjected,images:document.querySelectorAll('#info img').length,literal:document.getElementById('info').textContent===attack,route:routes[0],globalOrigin:useStore.getState().coords}})();
` }, bundle: true, format: 'iife', platform: 'browser', write: false, tsconfig: 'tsconfig.web.json' })
  await app.whenReady()
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => callback({ cancel: /^https?:/.test(details.url) }))
  const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } })
  writeFileSync(join(work, 'fixture.html'), '<!doctype html><div id="root"></div>')
  await window.loadFile(join(work, 'fixture.html'))
  await window.webContents.executeJavaScript(built.outputFiles[0].text)
  const result = await window.webContents.executeJavaScript('window.__mapTest')
  console.log(JSON.stringify({ scope: 'real Chromium + fixture AMap', ...result }))
  assert.deepEqual(result, { injected: false, images: 0, literal: true, route:{mode:'walking',a:[106.575545,29.55772],b:[106.57,29.56]},globalOrigin:'106.532212,29.575499' })
}
main().then(() => { rmSync(work, { recursive: true, force: true }); app.exit(0) }).catch(error => { console.error(error); rmSync(work, { recursive: true, force: true }); app.exit(1) })
