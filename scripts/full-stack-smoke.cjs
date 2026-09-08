// Actual YOYU Python backend + Electron + local real DOM. --deployed uses the configured Docker/model service.
// npm run build && env -u ELECTRON_RUN_AS_NODE xvfb-run -a node_modules/.bin/electron --no-sandbox scripts/full-stack-smoke.cjs
const { app, BrowserWindow, session } = require('electron')
const assert = require('node:assert/strict')
const { createServer } = require('node:http')
const { spawn, spawnSync } = require('node:child_process')
const { mkdtempSync, rmSync, writeFileSync, readFileSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { delimiter, join, resolve } = require('node:path')
const { pathToFileURL } = require('node:url')

const root = process.cwd(), work = mkdtempSync(join(tmpdir(), 'yoyu-full-stack-'))
const deployed = process.argv.includes('--deployed')
const deployment = deployed ? Object.fromEntries(readFileSync(join(root, '.env'), 'utf8').split(/\r?\n/).filter(line => /^[A-Z_]+=/.test(line)).map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1).trim()])) : {}
const python = process.env.YOYU_PYTHON || join(process.env.CONDA_PREFIX, process.platform === 'win32' ? 'python.exe' : 'bin/python')
const token = deployed ? deployment.YOYU_BACKEND_TOKEN : 'isolated-full-stack-smoke-only'
assert(token, 'Deployed smoke requires the configured YOYU control token')
const input = (deployed ? `部署验收${Date.now().toString(36).slice(-6)}：` : '') + '读取当前浏览器页面的真实菜单'
const historyTitle = input.length > 26 ? input.slice(0, 26) + '…' : input
const fixture = '<!doctype html><html><head><title>完整链路菜单样本</title></head><body><h1>完整链路菜单样本</h1><table><tr><th>菜品</th><th>价格</th></tr><tr><td>双人菜单样本</td><td>128 元</td></tr><tr><td>时价菜</td><td>询价</td></tr></table><input placeholder="搜索"></body></html>'
const server = createServer((_req, res) => { res.setHeader('content-type', 'text/html; charset=utf-8'); res.end(fixture) })
const shareCollision = createServer()
const nativeFetch = globalThis.fetch
let backend, backendLogs = '', backendUrl, fixtureUrl, window, runId, modelEnabled = false
let phase = 'initializing', finishing = false
function stage(value) { phase = value; console.log('[full-stack] ' + value) }
function bounded(promise, label, milliseconds = 5000) {
  let timer
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Timed out: ' + label)), milliseconds) })]).finally(() => clearTimeout(timer))
}
const watchdog = setTimeout(() => { console.error('Full-stack watchdog expired at: ' + phase); void finish(1) }, 90000)
process.on('uncaughtException', error => { console.error('Full-stack uncaught exception at ' + phase, error); void finish(1) })
process.on('unhandledRejection', error => { console.error('Full-stack unhandled rejection at ' + phase, error); void finish(1) })
app.setPath('userData', join(work, 'electron'))
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('no-proxy-server')
process.env.YOYU_BACKEND_AUTOSTART = 'false'
process.env.YOYU_BACKEND_TOKEN = token
process.env.YOYU_DATA_DIR = join(work, 'data')
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function waitFor(label, check, timeout = 25000) {
  const start = Date.now()
  while (Date.now() - start < timeout) { if (await bounded(Promise.resolve().then(check), label + ' condition', Math.min(5000, timeout - (Date.now() - start)))) return; await sleep(100) }
  throw new Error(`Timed out: ${label}\n${backendLogs.slice(-4000)}`)
}
async function api(path) {
  const response = await nativeFetch(backendUrl + path, { headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(3000) })
  if (!response.ok) throw new Error(`Backend ${response.status}: ${await response.text()}`)
  return bounded(response.json(), 'backend response body', 3000)
}
async function startBackend(port) {
  if (deployed) {
    await waitFor('Docker API readiness', async () => {
      try {
        const status = await api('/api/v1/health/ready')
        assert.equal(status.runtime_profile, 'service')
        modelEnabled = status.model_enabled
        return status.ready
      } catch { return false }
    })
    return
  }
  backend = spawn(python, ['-m', 'uvicorn', 'yoyu.app:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
    cwd: work,
    env: { PATH: process.env.PATH, LANG: 'C.UTF-8', PYTHONPATH: [join(root, 'backend'), join(root, 'vendor/planora/backend')].join(delimiter), PYTHONDONTWRITEBYTECODE: '1',
      YOYU_BACKEND_TOKEN: token, YOYU_DATA_DIR: process.env.YOYU_DATA_DIR, YOYU_RUNTIME_PROFILE: 'desktop', YOYU_REDIS_URL: 'local://',
      OPENAI_API_KEY: '', OPENAI_BASE_URL: 'http://127.0.0.1:9/v1', EMBEDDING_API_KEY: '', EMBEDDING_BASE_URL: 'http://127.0.0.1:9/v1', AMAP_WEBSERVICE_KEY: '' },
    stdio: ['ignore', 'ignore', 'pipe']
  })
  backend.stderr.on('data', bytes => { backendLogs = (backendLogs + bytes).slice(-12000) })
  backend.on('error', error => { backendLogs += error.message })
  await waitFor('owned Python backend readiness', async () => {
    if (backend.exitCode !== null || backend.signalCode !== null) throw new Error('Backend exited: ' + backendLogs)
    try { const status = await api('/api/v1/health/ready'); assert.equal(status.model_enabled, false); return status.status === 'ready' } catch { return false }
  })
}
async function stopBackend() {
  if (!backend || backend.exitCode !== null || backend.signalCode !== null) return
  const owned = backend
  await new Promise(resolve => {
    const hardStop = setTimeout(() => { owned.kill('SIGKILL'); resolve() }, 6000)
    const terminate = setTimeout(() => owned.kill('SIGKILL'), 5000)
    owned.once('exit', () => { clearTimeout(terminate); clearTimeout(hardStop); resolve() })
    owned.kill('SIGTERM')
  })
}
async function js(code) { return bounded(window.webContents.executeJavaScript(code, true), 'renderer JavaScript at ' + phase, 5000) }
async function fill(selector, value) {
  await js(`(() => {const el=document.querySelector(${JSON.stringify(selector)}); Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`)
  await sleep(50)
  await js(`document.querySelector(${JSON.stringify(selector)}).dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))`)
}
async function main() {
  await new Promise((resolve, reject) => {
    shareCollision.once('error', error => error.code === 'EADDRINUSE' ? resolve() : reject(error))
    shareCollision.listen(8799, '127.0.0.1', resolve)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  fixtureUrl = `http://127.0.0.1:${server.address().port}/menu`
  const reservation = createServer()
  await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve))
  const port = reservation.address().port
  await new Promise(resolve => reservation.close(resolve))
  backendUrl = deployed ? deployment.YOYU_BACKEND_URL || 'http://127.0.0.1:8011' : `http://127.0.0.1:${port}`
  process.env.YOYU_BACKEND_URL = backendUrl
  stage(deployed ? 'connecting deployed Docker backend' : 'starting owned Python backend')
  await startBackend(port)
  const existingRuns = new Set((await api('/api/v1/runs?user_id=desktop')).runs.map(run => run.run_id))
  stage('Python ready; waiting for Electron readiness')
  globalThis.fetch = (url, options) => String(url).startsWith(backendUrl + '/') ? nativeFetch(url, options) : Promise.reject(new Error('Full-stack smoke blocks external network'))
  await bounded(app.whenReady(), 'Electron app readiness', 15000)
  for (const current of [session.defaultSession, session.fromPartition('persist:xiaonian')]) current.webRequest.onBeforeRequest((details, callback) => callback({ cancel: /^https?:/.test(details.url) && !details.url.startsWith(fixtureUrl) && !details.url.startsWith(backendUrl + '/') }))
  process.chdir(work) // Do not load a developer's .env or settings.
  stage('importing real built main')
  await bounded(import(pathToFileURL(resolve(root, 'out/main/index.js')).href), 'built application import', 10000)
  stage('waiting for application window')
  await waitFor('real application window', () => { window = BrowserWindow.getAllWindows()[0]; return !!window })
  stage('waiting for renderer hydration')
  await waitFor('renderer ready', () => js("!!document.querySelector('textarea')&&!document.body.innerText.includes('未连接服务')"))
  stage('opening actual fixture webview')
  await fill('input[placeholder="输入网址或搜索词，回车打开…"]', fixtureUrl)
  await waitFor('real webview', () => js("!!document.querySelector('webview')&&document.querySelector('webview').getURL().includes('/menu')"))
  stage('sending menu request through UI')
  await fill('textarea', input)
  stage('waiting for backend/browser task completion')
  await waitFor('durable completed run', async () => {
    const runs = (await api('/api/v1/runs?user_id=desktop')).runs
    const current = runs.find(run => !existingRuns.has(run.run_id))
    if (!current) return false
    runId = current.run_id
    if (current.phase === 'FAILED') throw new Error('Run failed: ' + current.phase)
    return current.phase === 'SUCCEEDED'
  }, deployed ? 60000 : 25000)
  const completed = await api('/api/v1/runs/' + runId)
  const artifact = completed.state.browser_artifacts.find(a => a.data.menu?.length)
  assert(artifact, 'Real backend must persist typed menu extraction')
  assert.equal(artifact.source, 'browser')
  assert.equal(artifact.url, fixtureUrl)
  assert.equal(artifact.data.menu[0].price, 128)
  assert.equal(artifact.data.menu[1].price, null)
  if (modelEnabled) assert(completed.state.model_token_count > 0, 'The deployed model must actually respond')
  else assert.equal(completed.state.model_token_count, 0, 'No paid model calls in offline mode')
  assert((await api('/api/v1/runs/' + runId + '/events')).events.some(e => e.event_type === 'BROWSER_OBSERVATION'))
  await waitFor('real-backend menu projected into UI', () => js("document.body.innerText.includes('菜单摘录')&&document.body.innerText.includes('¥128')&&document.body.innerText.includes('价格待核验')"))
  // Restart only the owned backend, retaining its independent SQLite/checkpoint files.
  stage('restarting owned backend with persisted state')
  if (deployed) {
    const restarted = spawnSync('docker', ['compose', 'restart', 'api', 'worker'], { cwd: root, encoding: 'utf8', timeout: 25000 })
    assert.equal(restarted.status, 0, 'YOYU API/worker restart failed')
  } else await stopBackend()
  await startBackend(port)
  const recovered = await api('/api/v1/runs/' + runId)
  assert.equal(recovered.run_id, runId)
  assert.equal(recovered.phase, 'SUCCEEDED')
  assert.deepEqual(recovered.state.browser_artifacts, completed.state.browser_artifacts)
  stage('restoring canonical history after backend restart')
  await js("document.querySelector('button[title=\"新建对话\"]').click()")
  await js("document.querySelector('button[title=\"历史会话\"]').click()")
  await waitFor('history entry', () => js(`!![...document.querySelectorAll('div')].find(el=>el.className.includes('truncate pr-6')&&el.textContent===${JSON.stringify(historyTitle)})`))
  await js(`[...document.querySelectorAll('div')].find(el=>el.className.includes('truncate pr-6')&&el.textContent===${JSON.stringify(historyTitle)}).click()`)
  await waitFor('UI recovery after Python restart', () => js("document.body.innerText.includes('菜单摘录')&&document.body.innerText.includes('价格待核验')&&!document.body.innerText.includes('未连接服务')"))
  let commands = (await api('/api/v1/runs/' + runId + '/events')).events.filter(e => e.event_type === 'BROWSER_OBSERVATION').length
  if (!deployed) {
    const databaseCheck = spawnSync(python, ['-c', 'import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); rows=c.execute("select result from yoyu_browser_command").fetchall(); assert len(rows)>=1; results=[json.loads(r[0]) for r in rows]; assert all(r["ok"] for r in results); print(len(rows))', join(work, 'data', 'runs.sqlite')], { encoding: 'utf8', timeout: 5000 })
    assert.equal(databaseCheck.status, 0, databaseCheck.stderr)
    commands = Number(databaseCheck.stdout.trim())
  }
  await sleep(300)
  const screenshot = join(tmpdir(), deployed ? 'yoyu-deployed-smoke.png' : 'yoyu-full-stack-smoke.png')
  writeFileSync(screenshot, (await bounded(window.webContents.capturePage(), 'final screenshot')).toPNG())
  if (deployed) writeFileSync(join(root, 'eval/deployed_desktop_checks.json'), JSON.stringify({ date: new Date().toISOString(), backend: 'Docker PostgreSQL/Redis API and worker', python_environment: 'planora', real_electron_dom: true, page: 'controlled local menu fixture', real_model: modelEnabled, model_tokens: completed.state.model_token_count, run_id: runId, observed_events: commands, prices: [128, null], api_worker_restart_recovered: true, history_ui_restored: true, screenshot }, null, 2) + '\n')
  console.log(`Full-stack smoke passed: ${deployed ? 'Docker PostgreSQL/Redis' : 'owned Python'} backend, real Electron DOM, ${commands} observations, menu prices/unknowns, backend restart + history recovery, ${completed.state.model_token_count} model tokens. Screenshot: ${screenshot}`)
}
async function finish(code) {
  if (finishing) return
  finishing = true
  clearTimeout(watchdog)
  await stopBackend()
  if (deployed && /^[a-f0-9]{32}$/.test(runId || '')) {
    // Retain this run as deployment evidence without mixing it into the real desktop user's history.
    const archived = spawnSync('docker', ['compose', 'exec', '-T', 'postgres', 'psql', '-U', 'yoyu', '-d', 'yoyu', '-c', `UPDATE agent_run SET user_id='yoyu-deployment-checks' WHERE run_id='${runId}' AND input_text LIKE '部署验收%';`], { cwd: root, encoding: 'utf8', timeout: 5000 })
    if (archived.status !== 0) { console.error('Could not isolate the deployment test run'); code = 1 }
  }
  server.close()
  if (shareCollision.listening) shareCollision.close()
  process.chdir(root)
  if (!code) rmSync(work, { recursive: true, force: true })
  else console.error('Failure artifacts retained: ' + work)
  app.exit(code)
}
main().then(() => finish(0)).catch(async error => {
  console.error(error)
  if (window && !window.isDestroyed()) {
    try { writeFileSync(join(work, 'failure.png'), (await bounded(window.webContents.capturePage(), 'failure screenshot')).toPNG()) } catch {}
  }
  return finish(1)
})
