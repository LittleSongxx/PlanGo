// Offline integration fixture, not a real-world task success measurement.
// npm run build && env -u ELECTRON_RUN_AS_NODE xvfb-run -a node_modules/.bin/electron --no-sandbox scripts/desktop-ui-smoke.cjs
const { app, BrowserWindow, session } = require('electron')
const assert = require('node:assert/strict')
const { createServer } = require('node:http')
const { mkdirSync, mkdtempSync, rmSync, writeFileSync, cpSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join, resolve } = require('node:path')
const { pathToFileURL } = require('node:url')

const root = process.cwd()
const work = mkdtempSync(join(tmpdir(), 'plango-ui-smoke-'))
app.setPath('userData', join(work, 'electron'))
cpSync(join(root, 'skills'), join(work, 'skills'), { recursive: true })
app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
app.commandLine.appendSwitch('remote-debugging-port', '0')
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
  else if (url.pathname === '/api/v1/geo/search') result = { source: 'amap', scope: 'around', pois: [], observed_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString(), cache_hit: false }
  else if (url.pathname === '/api/v1/reminders') result = { reminders: [], history: [] }
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
      execution_outcome: { status: 'satisfied', data: { scope: 'read_only', business_completed: false } },
      browser_artifacts: [{ artifact_id: 'fixture-page', type: 'menu', source: 'browser', url: data.url, title: data.title, snapshot_id: data.snapshot_id, observed_at: data.observed_at,
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
  const uiEvidence = join(root, 'eval/plango-ui')
  mkdirSync(uiEvidence, { recursive: true })
  await sleep(250)
  writeFileSync(join(uiEvidence, '01-workspace-empty.png'), (await window.webContents.capturePage()).toPNG())
  await js("document.querySelector('button[title=\"成果区\"]').click()")
  await sleep(250)
  writeFileSync(join(uiEvidence, '02-outcome-empty.png'), (await window.webContents.capturePage()).toPNG())
  await js("document.querySelector('button[title=\"浏览器\"]').click()")
  assert.equal(await js("navigator.permissions.query({name:'geolocation'}).then(value=>value.state)"), 'granted', 'The actual host has its own geolocation permission')
  const addressLocation = await js("window.plango.reportLocation({city:'重庆',coords:'106.57,29.56',source:'address',userInitiated:true})")
  assert.equal(addressLocation.source, 'address')
  assert.equal((await js('window.plango.getLocation()')).source, 'address', 'IPC preserves the source used by subsequent Harness requests')
  await js("window.plango.reportLocation({city:'上海',coords:'121.47,31.23',source:'amap-ip'})")
  assert.equal((await js('window.plango.getLocation()')).city, '重庆', 'A late background IP reply cannot overwrite a selected address')
  await fill('input[placeholder="输入网址或搜索词，回车打开…"]', fixtureUrl)
  await waitFor('real WebContentsView', () => window.contentView.children.some(view => view.webContents?.getURL().includes('/fixture')))
  const guest = window.contentView.children.find(view => view.webContents?.getURL().includes('/fixture')).webContents
  assert.equal(await guest.executeJavaScript("typeof window.plango + ':' + typeof require"), 'undefined:undefined', 'Guest page has no privileged bridge or Node')
  assert.equal(await js("typeof window.plango.browserEval + ':' + typeof window.plango.browserExecResult"), 'undefined:undefined', 'Renderer cannot execute raw scripts or manufacture observations')
  await fill('input[placeholder="输入网址或搜索词，回车打开…"]', 'javascript:alert(1)')
  await waitFor('invalid-address recovery UI', async () => await js("!!document.querySelector('[role=alert]')") && await guest.executeJavaScript('document.visibilityState') === 'hidden')
  await fill('input[placeholder="输入网址或搜索词，回车打开…"]', fixtureUrl)
  await waitFor('valid-address recovery', async () => !await js("!!document.querySelector('[role=alert]')") && await guest.executeJavaScript('document.visibilityState') === 'visible')
  await js("document.querySelector('button[title=\"设置\"]').click()")
  await waitFor('drawer hides native view', async () => await guest.executeJavaScript('document.visibilityState') === 'hidden')
  await sleep(200)
  writeFileSync(join(uiEvidence, '04-settings.png'), (await window.webContents.capturePage()).toPNG())
  await js("document.querySelector('button[aria-label=\"关闭设置\"]').click()")
  await waitFor('drawer close restores native view', async () => await guest.executeJavaScript('document.visibilityState') === 'visible')
  await js("document.querySelector('button[title=\"连接\"]').click()")
  await waitFor('connection dialog', () => js("!!document.querySelector('dialog[aria-label=\"连接与能力\"]')"))
  await sleep(200)
  writeFileSync(join(uiEvidence, '05-connection-model.png'), (await window.webContents.capturePage()).toPNG())
  await js("[...document.querySelectorAll('[role=tab]')].find(el=>el.innerText==='技能').click()")
  await waitFor('installed skills', () => js("!!document.querySelector('[role=switch]')"))
  const skillBefore = await js("({name:document.querySelector('[role=switch]').getAttribute('aria-label'), checked:document.querySelector('[role=switch]').getAttribute('aria-checked')})")
  await js("document.querySelector('[role=switch]').click()")
  await waitFor('skill toggle saves through IPC', async () => (await js("document.querySelector('[role=switch]').getAttribute('aria-checked')")) !== skillBefore.checked)
  assert.equal((await js('window.plango.listSkills()')).find(skill => skill.name === skillBefore.name).enabled, skillBefore.checked !== 'true')
  await js("document.querySelector('[role=switch]').click()")
  await waitFor('skill restored', async () => (await js("document.querySelector('[role=switch]').getAttribute('aria-checked')")) === skillBefore.checked)
  writeFileSync(join(uiEvidence, '06-connection-skills.png'), (await window.webContents.capturePage()).toPNG())
  for (const [tab, filename] of [['记忆','07-connection-memory.png'],['提醒','08-connection-reminders.png'],['微信/飞书','09-connection-social.png']]) {
    await js(`[...document.querySelectorAll('[role=tab]')].find(el=>el.innerText===${JSON.stringify(tab)}).click()`)
    await sleep(250)
    writeFileSync(join(uiEvidence, filename), (await window.webContents.capturePage()).toPNG())
  }
  await js("document.querySelector('button[aria-label=\"关闭连接与能力\"]').click()")
  await waitFor('connection close restores guest', async () => await guest.executeJavaScript('document.visibilityState') === 'visible')
  await js("document.querySelector('button[title=\"附近发现\"]').click()")
  await waitFor('discovery ready empty', () => js("document.body.innerText.includes('当前范围未返回地点')"))
  writeFileSync(join(uiEvidence, '10-discover-empty.png'), (await window.webContents.capturePage()).toPNG())
  await js("[...document.querySelectorAll('dialog button')].find(el=>el.innerText.trim()==='优惠发现').click()")
  await waitFor('deals ready empty', () => js("document.body.innerText.includes('还没有已读取的优惠')"))
  writeFileSync(join(uiEvidence, '11-deals-empty.png'), (await window.webContents.capturePage()).toPNG())
  await js("document.querySelector('button[aria-label=\"关闭地点与优惠发现\"]').click()")
  await waitFor('discovery close restores guest', async () => await guest.executeJavaScript('document.visibilityState') === 'visible')
  await fill('textarea', input)
  await waitFor('real browser observation', () => !!observation)
  await waitFor('projected menu in full UI', () => js("document.body.innerText.includes('菜单摘录') && document.body.innerText.includes('价格待核验') && document.body.innerText.includes('¥128')"))
  const before = guest.id
  await js("document.querySelector('button[title=\"浏览器\"]').click()")
  await sleep(100)
  await js("document.querySelector('button[title=\"成果区\"]').click()")
  assert.equal(guest.id, before, 'Browser survives outcome view switching')
  assert(!guest.isDestroyed(), 'View changes must not destroy the authenticated browser')
  const reads = snapshotReads
  await js("document.querySelector('button[title=\"新建对话\"]').click()")
  await js("document.querySelector('button[title=\"历史会话\"]').click()")
  await waitFor('history entry', () => js(`!![...document.querySelectorAll('[data-history-entry]')].find(el=>el.getAttribute('aria-label')==='打开会话：'+${JSON.stringify(input)})`))
  writeFileSync(join(uiEvidence, '12-history.png'), (await window.webContents.capturePage()).toPNG())
  await js(`[...document.querySelectorAll('[data-history-entry]')].find(el=>el.getAttribute('aria-label')==='打开会话：'+${JSON.stringify(input)}).click()`)
  await waitFor('durable history refetch', () => snapshotReads > reads)
  await waitFor('restored canonical cards', () => js("document.body.innerText.includes('菜单摘录') && document.body.innerText.includes('价格待核验')"))
  // capturePage observes Chromium's compositor, which can lag the DOM commit.
  await sleep(500)
  const screenshot = join(tmpdir(), 'plango-desktop-ui-smoke.png')
  writeFileSync(screenshot, (await window.webContents.capturePage()).toPNG())
  writeFileSync(join(uiEvidence, '03-outcome-observed.png'), (await window.webContents.capturePage()).toPNG())
  const hostUrl = window.webContents.getURL()
  await js(`location.href=${JSON.stringify(fixtureUrl)}`)
  await sleep(100)
  assert.equal(window.webContents.getURL(), hostUrl, 'Renderer navigation away from the fixed application document is blocked')
  await window.loadURL(fixtureUrl) // Trusted test code simulates the same WebContents displaying a foreign document.
  assert.equal(await js("window.plango.getConfig().then(()=>false,error=>error.message.includes('Untrusted IPC sender'))"), true, 'A foreign document in the former host cannot use privileged IPC')
  console.log('Desktop UI smoke passed: real WCV/IPC, DOM read, unknown price, same authenticated contents, history recovery, host navigation and IPC isolation. Shell screenshot: ' + screenshot)
}
main().then(() => { server.close(); process.chdir(root); rmSync(work, { recursive: true, force: true }); app.exit(0) }).catch(error => { console.error(error); server.close(); process.chdir(root); app.exit(1) })
