// Offline integration fixture, not a real-world task success measurement.
// npm run build && env -u ELECTRON_RUN_AS_NODE xvfb-run -a node_modules/.bin/electron --no-sandbox scripts/desktop-ui-smoke.cjs
const { app, BrowserWindow, session } = require('electron')
const assert = require('node:assert/strict')
const { createServer } = require('node:http')
const { mkdtempSync, rmSync, writeFileSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join, resolve } = require('node:path')
const { pathToFileURL } = require('node:url')

const root = process.cwd()
const work = mkdtempSync(join(tmpdir(), 'plango-ui-smoke-'))
app.setPath('userData', join(work, 'electron'))
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('no-proxy-server')
process.env.PLANGO_BACKEND_AUTOSTART = 'false'
process.env.PLANGO_BACKEND_TOKEN = 'offline-smoke-only'
process.env.PLANGO_DATA_DIR = join(work, 'harness')
let snapshot, command, observation, snapshotReads = 0
const input = '读取当前浏览器页面的真实菜单'
const fixture = '<!doctype html><html><head><title>菜单界面回归样本</title></head><body><h1>菜单界面回归样本</h1><table><tr><th>菜品</th><th>价格</th></tr><tr><td>真实读取的双人套餐</td><td>128 元</td></tr><tr><td>时价菜</td><td>询价</td></tr></table><input placeholder="搜索"></body></html>'
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1')
  if (url.pathname === '/fixture') { res.setHeader('content-type', 'text/html; charset=utf-8'); res.end(fixture); return }
  if (req.headers.authorization !== 'Bearer offline-smoke-only') { res.writeHead(401); res.end('{}'); return }
  let body = ''; for await (const part of req) body += part
  const data = body ? JSON.parse(body) : {}
  let result = {}
  if (/health\/(live|ready)$/.test(url.pathname)) result = { ready: true, status: 'ready', app: 'PlanGo', world_provider: 'browser' }
  else if (url.pathname === '/api/v1/memory/profile') result = { summaries: [], preferences: [], favorites: [] }
  else if (url.pathname === '/api/v1/runs' && req.method === 'GET') result = { runs: snapshot ? [snapshot] : [] }
  else if (url.pathname === '/api/v1/runs' && req.method === 'POST') {
    assert.equal(data.input_text, input)
    snapshot = { run_id: 'smoke-run', input_text: data.input_text, phase: 'REQUIREMENTS_READY', event_seq: 1, version: 1, interrupt_id: 'browser:smoke-command', state: { browser_wait: { type: 'browser', command_id: 'smoke-command', message: '读取本地回归页面' } } }
    command = { command_id: 'smoke-command', run_id: snapshot.run_id, browser_session_id: data.browser_session_id, operation: 'extract', arguments: {}, expires_at: new Date(Date.now() + 60000).toISOString() }
    result = { run_id: snapshot.run_id, accepted: true }
  } else if (url.pathname === '/api/v1/runs/smoke-run') { snapshotReads++; result = snapshot }
  else if (url.pathname.endsWith('/events')) result = { events: [] }
  else if (url.pathname === '/api/v1/browser/commands') result = { commands: command && !observation ? [command] : [], cursor: observation ? 1 : 0 }
  else if (url.pathname === '/api/v1/browser/commands/smoke-command/result') {
    assert.equal(data.ok, true, JSON.stringify(data))
    assert.equal(data.tables[0].rows[0][1], '128 元')
    assert.equal(data.url, fixtureUrl)
    observation = data
    snapshot = { ...snapshot, phase: 'SUCCEEDED', outcome: 'SUCCEEDED', version: 2, event_seq: 2, interrupt_id: null, state: {
      messages: [{ type: 'human', content: input }], reason: '页面观测已保存', browser_wait: null,
      browser_artifacts: [{ artifact_id: 'fixture-page', type: 'menu', source: 'browser', url: data.url, title: data.title, snapshot_id: data.snapshot_id,
        data: { text: data.text, tables: data.tables, menu: data.tables[0].rows.map(([name, raw]) => ({ name, price: raw === '128 元' ? 128 : null, quote: name + ' ' + raw })) } }]
    } }
    result = { accepted: true }
  } else { res.writeHead(404); res.end(JSON.stringify({ detail: 'unexpected test path ' + url.pathname })); return }
  res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(result))
})
let fixtureUrl, window
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function waitFor(label, test, timeout = 20000) {
  const started = Date.now()
  while (Date.now() - started < timeout) { if (await test()) return; await sleep(100) }
  throw new Error('Timed out: ' + label)
}
async function js(code) { return window.webContents.executeJavaScript(code, true) }
async function fill(selector, value) {
  await js(`(() => { const el=document.querySelector(${JSON.stringify(selector)}); if(!el)throw new Error('missing input'); Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(value)}); el.dispatchEvent(new Event('input',{bubbles:true})); })()`)
  await sleep(50)
  await js(`document.querySelector(${JSON.stringify(selector)}).dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))`)
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  process.env.PLANGO_BACKEND_URL = `http://127.0.0.1:${server.address().port}`
  fixtureUrl = process.env.PLANGO_BACKEND_URL + '/fixture'
  // No model, location provider, remote image, or analytics calls leave this test.
  const nativeFetch = globalThis.fetch
  globalThis.fetch = (url, options) => String(url).startsWith(process.env.PLANGO_BACKEND_URL + '/') ? nativeFetch(url, options) : Promise.reject(new Error('offline smoke blocks external network'))
  await app.whenReady()
  for (const current of [session.defaultSession, session.fromPartition('persist:plango')]) {
    current.webRequest.onBeforeRequest((details, callback) => callback({ cancel: /^https?:/.test(details.url) && !details.url.startsWith(process.env.PLANGO_BACKEND_URL + '/') }))
  }
  // Prevent loading a developer's .env/config, while loading the real built application.
  process.chdir(work)
  await import(pathToFileURL(resolve(root, 'out/main/index.js')).href)
  await waitFor('application window', () => { window = BrowserWindow.getAllWindows()[0]; return !!window })
  await waitFor('renderer hydration', () => js("!!document.querySelector('textarea') && !document.body.innerText.includes('未连接服务')"))
  assert.equal(await js("navigator.permissions.query({name:'geolocation'}).then(value=>value.state)"), 'granted', 'The actual host has its own geolocation permission')
  const addressLocation = await js("window.plango.reportLocation({city:'重庆',coords:'106.57,29.56',source:'address',userInitiated:true})")
  assert.equal(addressLocation.source, 'address')
  assert.equal((await js('window.plango.getLocation()')).source, 'address', 'IPC preserves the source used by subsequent Harness requests')
  await js("window.plango.reportLocation({city:'上海',coords:'121.47,31.23',source:'amap-ip'})")
  assert.equal((await js('window.plango.getLocation()')).city, '重庆', 'A late background IP reply cannot overwrite a selected address')
  await fill('input[placeholder="输入网址或搜索词，回车打开…"]', fixtureUrl)
  await waitFor('real webview', () => js("!!document.querySelector('webview') && document.querySelector('webview').getURL().includes('/fixture')"))
  await fill('textarea', input)
  await waitFor('real browser observation', () => !!observation)
  await waitFor('projected menu in full UI', () => js("document.body.innerText.includes('菜单摘录') && document.body.innerText.includes('价格待核验') && document.body.innerText.includes('¥128')"))
  const before = await js("document.querySelector('webview').getWebContentsId()")
  await js("document.querySelector('button[title=\"浏览器\"]').click()")
  await sleep(100)
  await js("document.querySelector('button[title=\"成果区\"]').click()")
  assert.equal(await js("document.querySelector('webview').getWebContentsId()"), before, 'Browser survives outcome view switching')
  const reads = snapshotReads
  await js("document.querySelector('button[title=\"新建对话\"]').click()")
  await js("document.querySelector('button[title=\"历史会话\"]').click()")
  await waitFor('history entry', () => js(`!![...document.querySelectorAll('div')].find(el=>el.className.includes('truncate pr-6')&&el.textContent===${JSON.stringify(input)})`))
  await js(`[...document.querySelectorAll('div')].find(el=>el.className.includes('truncate pr-6')&&el.textContent===${JSON.stringify(input)}).click()`)
  await waitFor('durable history refetch', () => snapshotReads > reads)
  await waitFor('restored canonical cards', () => js("document.body.innerText.includes('菜单摘录') && document.body.innerText.includes('价格待核验')"))
  // capturePage observes Chromium's compositor, which can lag the DOM commit.
  await sleep(500)
  const screenshot = join(tmpdir(), 'plango-desktop-ui-smoke.png')
  writeFileSync(screenshot, (await window.webContents.capturePage()).toPNG())
  console.log('Desktop UI smoke passed: real preload/IPC, browser DOM read, unknown price, persistent webview, canonical history restore. Screenshot: ' + screenshot)
}
main().then(() => { server.close(); process.chdir(root); rmSync(work, { recursive: true, force: true }); app.exit(0) }).catch(error => { console.error(error); server.close(); process.chdir(root); app.exit(1) })
