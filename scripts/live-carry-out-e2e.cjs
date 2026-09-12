// Real Electron window: send a task, show a dated plan, click carry-out actions.
// Does not use the main user service. Requires a built out/ tree.
// env -u ELECTRON_RUN_AS_NODE xvfb-run -a node_modules/.bin/electron --no-sandbox scripts/live-carry-out-e2e.cjs
const { app, BrowserWindow, clipboard, dialog, session } = require('electron')
const assert = require('node:assert/strict')
const { createServer } = require('node:http')
const { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, cpSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join, resolve } = require('node:path')
const { pathToFileURL } = require('node:url')

const root = process.cwd()
const work = mkdtempSync(join(tmpdir(), 'plango-carry-out-e2e-'))
const evidence = join(root, 'output/live-carry-out-e2e', String(Date.now()))
const exportsDir = join(evidence, 'exports')
mkdirSync(exportsDir, { recursive: true, mode: 0o700 })
app.setPath('userData', join(work, 'electron'))
cpSync(join(root, 'skills'), join(work, 'skills'), { recursive: true })
app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
app.commandLine.appendSwitch('remote-debugging-port', '0')
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('no-proxy-server')
process.env.PLANGO_BACKEND_AUTOSTART = 'false'
process.env.PLANGO_BACKEND_TOKEN = 'carry-out-e2e-only'
process.env.PLANGO_DATA_DIR = join(work, 'harness')

const input = '明天4个人去南滨路吃饭'
const runId = 'carry-out-e2e-run'
const snapshot = {
  run_id: runId,
  input_text: input,
  phase: 'SUCCEEDED',
  outcome: 'SUCCEEDED',
  event_seq: 0,
  version: 2,
  interrupt_id: null,
  state: {
    trip_spec: {
      visit_date: '2026-09-13',
      timezone: 'Asia/Shanghai',
      party_size: 4,
      travel_mode: 'transit',
      location: { name: '重庆', latitude: 29.55, longitude: 106.57 }
    },
    selected_plan: {
      plan_id: 'plan-e2e',
      version: 2,
      label: '周末下午',
      total_cost: 128,
      party_size: 4,
      stops: [{
        place_id: 'shop-1',
        name: '真实店铺',
        category: '餐厅',
        start_minute: 840,
        end_minute: 960,
        unit_price: 32,
        estimated_wait_min: 10,
        supply_source: 'amap'
      }]
    },
    place_candidates: [{
      place_id: 'shop-1',
      name: '真实店铺',
      address: '南滨路 1 号',
      source: 'amap',
      longitude: 106.58,
      latitude: 29.55,
      price_known: true,
      average_price: 32
    }],
    verifier: {
      plan_id: 'plan-e2e',
      executable: false,
      unknown_evidence: [{ name: 'queue', detail: '排队和路线证据尚未取得' }]
    }
  }
}

let receipt
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1')
  if (req.headers.authorization !== 'Bearer carry-out-e2e-only') { res.writeHead(401); res.end('{}'); return }
  let body = ''
  for await (const part of req) body += part
  const data = body ? JSON.parse(body) : {}
  let result = {}
  if (/health\/(live|ready)$/.test(url.pathname)) {
    result = { ready: true, input_delivery_version: 1, status: 'ready', app: 'PlanGo', world_provider: 'browser' }
  } else if (url.pathname === '/api/v1/runs' && req.method === 'GET') {
    result = { runs: receipt ? [snapshot] : [] }
  } else if (url.pathname === '/api/v1/runs' && req.method === 'POST') {
    assert.equal(data.input_text, input)
    receipt = { run_id: runId, accepted: true, request_id: data.request_id, request_fingerprint: data.request_fingerprint }
    result = receipt
  } else if (url.pathname === `/api/v1/runs/${runId}`) {
    result = snapshot
  } else if (url.pathname.startsWith('/api/v1/requests/')) {
    assert.equal(decodeURIComponent(url.pathname.slice('/api/v1/requests/'.length)), receipt.request_id)
    result = receipt
  } else if (url.pathname.endsWith('/events')) {
    result = { events: [] }
  } else if (url.pathname === '/api/v1/browser/commands') {
    result = { commands: [], cursor: 0 }
  } else if (url.pathname === '/api/v1/reminders/due') {
    result = { reminders: [] }
  } else if (url.pathname === '/api/v1/reminders' && req.method === 'POST') {
    assert.match(String(data.text || ''), /可以带着走/)
    assert.match(String(data.at || ''), /^\d{4}-\d{2}-\d{2}T/)
    result = { reminders: [{ id: 'e2e-reminder', text: data.text, at: data.at }], history: [] }
  } else if (url.pathname === '/api/v1/reminders') {
    result = { reminders: [], history: [] }
  } else if (url.pathname === '/api/v1/memory/profile') {
    result = { summaries: [], preferences: [], favorites: [] }
  } else if (url.pathname === '/api/v1/geo/search') {
    result = { source: 'amap', scope: 'around', pois: [], observed_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString(), cache_hit: false }
  } else {
    res.writeHead(404)
    res.end(JSON.stringify({ detail: 'unexpected test path ' + url.pathname }))
    return
  }
  res.setHeader('content-type', 'application/json')
  res.end(JSON.stringify(result))
})

let window
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function waitFor(label, test, timeout = 20000) {
  const started = Date.now()
  while (Date.now() - started < timeout) { if (await test()) return; await sleep(100) }
  throw new Error('Timed out: ' + label)
}
async function bounded(label, operation, timeout = 15000) {
  let timer
  try { return await Promise.race([operation, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} exceeded ${timeout} ms`)), timeout) })]) }
  finally { clearTimeout(timer) }
}
async function js(code) { return bounded('host renderer', window.webContents.executeJavaScript(code, true)) }
async function setInput(selector, value) {
  await js(`(() => { const el=document.querySelector(${JSON.stringify(selector)}); if(!el)throw new Error('missing input'); Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(value)}); el.dispatchEvent(new Event('input',{bubbles:true})); })()`)
  await sleep(50)
}
function clickText(text) {
  return js(`(() => { const el=[...document.querySelectorAll('button')].find(btn=>btn.innerText.replace(/\\s+/g,' ').includes(${JSON.stringify(text)})); if(!el) throw new Error('missing button '+${JSON.stringify(text)}); el.scrollIntoView({block:'center'}); el.click(); })()`)
}

async function main() {
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen))
  process.env.PLANGO_BACKEND_URL = `http://127.0.0.1:${server.address().port}`
  const nativeFetch = globalThis.fetch
  globalThis.fetch = (url, options) => String(url).startsWith(process.env.PLANGO_BACKEND_URL + '/') ? nativeFetch(url, options) : Promise.reject(new Error('carry-out e2e blocks external network'))
  await app.whenReady()
  for (const current of [session.defaultSession, session.fromPartition('persist:plango')]) {
    current.webRequest.onBeforeRequest((details, callback) => callback({ cancel: /^https?:/.test(details.url) && !details.url.startsWith(process.env.PLANGO_BACKEND_URL + '/') }))
  }
  process.chdir(work)
  await import(pathToFileURL(resolve(root, 'out/main/index.js')).href)
  await waitFor('application window', () => { window = BrowserWindow.getAllWindows()[0]; return !!window })
  await waitFor('renderer hydration', () => js("!!document.querySelector('textarea') && !document.body.innerText.includes('未连接服务')"))
  await Promise.resolve(clipboard.clear())
  dialog.showSaveDialog = async (_parent, options) => {
    const name = String(options?.defaultPath || 'export.bin').split(/[\\/]/).pop()
    return { canceled: false, filePath: join(exportsDir, name) }
  }

  await setInput('textarea', input)
  await js("document.querySelector('button[aria-label=\"发送消息\"]').click()")
  await waitFor('carry-out bar', () => js("!!document.querySelector('[aria-label=\"带走这次安排\"]') && document.body.innerText.includes('可以带着走') && document.body.innerText.includes('真实店铺')"))
  await js("document.querySelector('[aria-label=\"带走这次安排\"]').scrollIntoView({block:'center'})")
  await sleep(250)
  writeFileSync(join(evidence, '01-plan-ready.png'), (await window.webContents.capturePage()).toPNG())

  await clickText('复制安排')
  await waitFor('copy status', () => js("document.body.innerText.includes('已复制，可直接粘贴到微信')"))
  const copied = await Promise.resolve(clipboard.readText())
  assert.match(copied, /^可以带着走/)
  assert(copied.includes('周末下午'))
  assert(copied.includes('2026-09-13'))
  assert(copied.includes('真实店铺'))
  assert(copied.includes('南滨路 1 号'))
  assert(copied.includes('这不是预约或支付回执'))
  assert(!/已预订|已支付|已下单|履约成功|业务已完成/.test(copied))
  writeFileSync(join(evidence, 'copied.txt'), copied)

  await clickText('加入日历')
  await waitFor('ics status', () => js("[...document.querySelectorAll('[role=status]')].some(el=>el.innerText.includes('已保存到') && el.innerText.includes('.ics'))"))
  const icsPath = join(exportsDir, 'PlanGo-2026-09-13-周末下午-v2.ics')
  const ics = readFileSync(icsPath, 'utf8')
  assert(ics.includes('BEGIN:VCALENDAR'))
  assert(ics.includes('DTSTART;TZID=Asia/Shanghai:20260913T140000'))
  assert(ics.includes('这不是预约或支付回执'))

  await clickText('保存图片')
  await waitFor('png status', () => js("[...document.querySelectorAll('[role=status]')].some(el=>el.innerText.includes('已保存到') && el.innerText.includes('.png'))"), 30000)
  const pngPath = join(exportsDir, 'PlanGo-2026-09-13-周末下午-v2.png')
  const png = readFileSync(pngPath)
  assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
  assert(png.byteLength > 80)

  const hasReminder = await js("[...document.querySelectorAll('button')].some(el=>el.innerText.includes('出门前一小时'))")
  if (hasReminder) {
    await clickText('出门前一小时')
    await waitFor('reminder status', () => js("document.body.innerText.includes('已写入提醒')"))
  }
  writeFileSync(join(evidence, '02-after-actions.png'), (await window.webContents.capturePage()).toPNG())
  writeFileSync(join(evidence, 'report.json'), JSON.stringify({
    scope: 'real Electron carry-out actions after a dated plan is shown',
    passed: true,
    runId,
    evidence,
    copied_chars: copied.length,
    ics: icsPath,
    png_bytes: png.byteLength,
    reminder: hasReminder,
    limitations: ['受控后端样本，不是商家履约或真实模型规划质量测评', '未使用主用户 8011 服务']
  }, null, 2) + '\n')
  console.log(JSON.stringify({ scope: 'carry-out e2e', passed: true, evidence, copied_chars: copied.length, png_bytes: png.byteLength, reminder: hasReminder }))
}

main().then(() => { server.close(); process.chdir(root); rmSync(work, { recursive: true, force: true }); app.exit(0) }).catch(async error => {
  console.error(error)
  try { writeFileSync(join(evidence, 'error.txt'), String(error.stack || error)) } catch { /* keep the original failure */ }
  try {
    if (window && !window.isDestroyed()) writeFileSync(join(evidence, 'fail.png'), (await window.webContents.capturePage()).toPNG())
  } catch { /* best-effort */ }
  server.close()
  process.chdir(root)
  app.exit(1)
})
