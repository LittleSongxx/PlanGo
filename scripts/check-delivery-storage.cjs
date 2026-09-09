// Actual Chromium localStorage/IndexedDB across two isolated Electron launches; no backend/network/business actions.
const assert = require('node:assert/strict')
const { mkdtempSync, writeFileSync, readFileSync, rmSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join } = require('node:path')
if (!process.versions.electron) {
  const { buildSync } = require('esbuild')
  const { spawnSync } = require('node:child_process')
  const work = mkdtempSync(join(tmpdir(), 'plango-delivery-storage-'))
  try {
    buildSync({ entryPoints: ['src/renderer/src/store.ts'], outfile: join(work, 'store.js'), bundle: true, format: 'iife', globalName: 'DeliveryStore', tsconfig: 'tsconfig.web.json' })
    writeFileSync(join(work, 'fixture.html'), '<!doctype html><title>PlanGo delivery persistence check</title>')
    const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE
    for (const phase of ['seed', 'recover']) {
      const result = spawnSync(require('electron'), ['--no-sandbox', __filename, phase, work], { env, encoding: 'utf8', timeout: 30000 })
      assert(!result.error, `${phase}: ${result.error?.message}\n${result.stderr}`)
      assert.equal(result.status, 0, `${phase}: ${result.stderr}\n${result.stdout}`)
      assert(result.stdout.includes(`${phase} completed`))
      process.stdout.write(result.stdout)
    }
  } finally { rmSync(work, { recursive: true, force: true }) }
} else {
  const { app, BrowserWindow, session } = require('electron')
  const [phase, work] = process.argv.slice(-2)
  app.setPath('userData', join(work, 'profile'))
  app.commandLine.appendSwitch('disable-gpu')
  app.whenReady().then(async () => {
    const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } })
    await window.loadFile(join(work, 'fixture.html'))
    await window.webContents.executeJavaScript(`window.fixtureRequests=[];window.plango={harness:{status:async()=>({ready:true}),listRuns:async()=>[],checkDelivery:async requestId=>({requestId,status:'not_sent'}),deliver:async request=>{window.fixtureRequests.push(request);return {requestId:request.requestId,status:'delivered',runId:'fixture-run',snapshot:{run_id:'fixture-run',input_text:request.text,phase:'SUCCEEDED',outcome:'SUCCEEDED',event_seq:1,version:1,state:{}}}}}};` + readFileSync(join(work, 'store.js'), 'utf8'))
    if (phase === 'seed') {
      const result = await window.webContents.executeJavaScript(`(async()=>{
        const store=DeliveryStore.useStore; const image='data:image/png;base64,'+btoa('a'.repeat(8000000));
        let exceeded=false;try{localStorage.setItem('capacity_probe',image)}catch{exceeded=true}finally{localStorage.removeItem('capacity_probe')}
        store.getState().setDraft({text:'8 MB 图片与要求',image});
        await store.getState().send('8 MB 图片与要求',image);
        const saved=JSON.parse(localStorage.getItem('plango_composer'));
        return {exceeded,status:store.getState().pendingDelivery.status,metadataBytes:localStorage.getItem('plango_composer').length,hasRef:!!saved.pendingDelivery.imageRef,rawImage:!!saved.pendingDelivery.request.image,requests:fixtureRequests.length};
      })()`)
      // file:// storage limits vary by Chromium build; record the real probe instead of assuming 5 MiB.
      assert.equal(result.status, 'not_sent')
      assert.equal(result.rawImage, false)
      assert.equal(result.hasRef, true)
      assert.equal(result.requests, 0)
      assert(result.metadataBytes < 2000, JSON.stringify(result))
      console.log(JSON.stringify({ phase, ...result }))
    } else {
      const result = await window.webContents.executeJavaScript(`(async()=>{
        const store=DeliveryStore.useStore; await store.getState().hydrateHarness();
        const pending=store.getState().pendingDelivery; const requestId=pending.request.requestId;
        const image=pending.request.image; const afterReadOnly=fixtureRequests.length;
        await store.getState().recoverDelivery(true);
        return {imageLength:image.length,exact:image==='data:image/png;base64,'+btoa('a'.repeat(8000000)),afterReadOnly,requests:fixtureRequests.length,sameId:fixtureRequests[0].requestId===requestId,delivered:store.getState().pendingDelivery===null,draftCleared:!store.getState().drafts[store.getState().activeSessionId].image};
      })()`)
      assert.equal(result.exact, true)
      assert.equal(result.afterReadOnly, 0)
      assert.equal(result.requests, 1)
      assert.equal(result.sameId, true)
      assert.equal(result.delivered, true)
      assert.equal(result.draftCleared, true)
      console.log(JSON.stringify({ phase, ...result }))
    }
    await session.defaultSession.flushStorageData()
    window.destroy()
    console.log(`${phase} completed`)
    app.exit(0)
  }).catch(error => { console.error(error); app.exit(1) })
}
