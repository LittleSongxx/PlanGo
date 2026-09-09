// Preregistered real desktop actions. The root runner owns the API, DB, model and gold.
// Usage: node scripts/quality_desktop_cases.cjs PRIVATE_CONFIG.json
// Never run this against the main desktop/profile or a non-loopback backend.
const assert = require('node:assert/strict')
const http = require('node:http')
const { readFile, writeFile, mkdir, realpath, stat } = require('node:fs/promises')
const { resolve, join, relative, sep } = require('node:path')
const { createHash } = require('node:crypto')
const { _electron } = require('playwright-core')

const ROOT = resolve(__dirname, '..')
const MESSAGE = '人数改为3人，其他不变'
const LEGACY_DRIVERS = { 'DEV-05': 'edit', 'DEV-06': 'edit', 'DEV-09': 'save_restart', 'DEV-10': 'message_recovery' }
const TERMINAL = new Set(['SUCCEEDED', 'FAILED', 'PARTIAL_FAILED', 'CANCELLED', 'INFEASIBLE'])
const sleep = ms => new Promise(resolveSleep => setTimeout(resolveSleep, ms))
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const inside = (parent, child) => child !== parent && child.startsWith(parent + sep)
const jsonFile = (path, value) => writeFile(path, JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 })

function driverConfig(config, declaredCase) {
  assert(typeof config.case_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(config.case_id), 'Invalid case_id')
  const case_id = config.case_id.replace(/^DEV-?0?([0-9]+)$/, (_, id) => `DEV-${id.padStart(2, '0')}`)
  const driver = config.driver || LEGACY_DRIVERS[case_id]
  assert(['edit', 'save_restart', 'message_recovery', 'message_uncertain'].includes(driver), 'Unsupported desktop driver')
  if (declaredCase) {
    assert.equal(declaredCase.case_id, case_id, 'Declared case identity mismatch')
    assert.equal(declaredCase.environment?.driver || LEGACY_DRIVERS[case_id], driver, 'Declared driver mismatch')
  } else assert(LEGACY_DRIVERS[case_id], 'New cases require their private case-input.json declaration')
  const message = config.message ?? (case_id === 'DEV-10' ? MESSAGE : undefined)
  if (driver === 'edit') {
    assert(declaredCase && Array.isArray(config.messages) && config.messages.length >= 1 && config.messages.length <= 2, 'Edit driver requires one or two declared messages')
    assert(config.messages.every(text => typeof text === 'string' && text.trim() && text.length <= 10_000), 'Invalid declared edit message')
    assert.deepEqual(config.messages, declaredCase.agent_input?.user_turns?.map(turn => turn.message), 'Edit messages must exactly match the preregistered sequence')
  } else if (driver !== 'save_restart') {
    assert(typeof message === 'string' && message.trim() && message.length <= 10_000, 'One explicit message is required')
    if (declaredCase) {
      assert.equal(declaredCase.agent_input?.user_turns?.length, 1, 'Message driver requires exactly one declared user input')
      assert.equal(declaredCase.agent_input.user_turns[0].message, message, 'Message must equal the preregistered user input')
    }
  }
  return { ...config, case_id, driver, message }
}

function permittedMessage(config, payload, identity) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return false
  const { request_fingerprint, ...body } = payload
  return payload.text === config.message && typeof payload.request_id === 'string' && /^[A-Za-z0-9:_-]{1,200}$/.test(payload.request_id)
    && typeof request_fingerprint === 'string' && /^[a-f0-9]{64}$/.test(request_fingerprint) && hash(body) === request_fingerprint
    && Object.keys(payload).every(key => ['request_id', 'request_fingerprint', 'text', 'location_context'].includes(key))
    && (!identity || identity.request_id === payload.request_id && identity.request_fingerprint === request_fingerprint)
}

function permittedSave(payload, review) {
  return !!review && payload?.decision === 'save' && Object.keys(payload).every(key => ['decision', 'interrupt_id', 'plan_id', 'plan_version'].includes(key))
    && ['interrupt_id', 'plan_id', 'plan_version'].every(key => payload[key] === review[key])
}

function messageSequence(config) {
  const messages = config.driver === 'edit' ? config.messages : [config.message]
  const entries = new Map(), accepted = new Set()
  let armed = config.driver === 'edit' ? -1 : 0
  return {
    arm(index) {
      assert(config.driver === 'edit' && Number.isInteger(index) && index === armed + 1 && index < messages.length, 'Edit messages must be armed in order')
      assert(index === 0 || accepted.has(index - 1), 'Previous scripted input has no matching real acceptance')
      armed = index
    },
    permit(payload) {
      const previous = entries.get(payload?.request_id)
      const index = previous?.index ?? armed
      if (index < 0 || !messages[index] || (!previous && [...entries.values()].some(item => item.index === index))) return null
      if (!permittedMessage({ message: messages[index] }, payload, previous)) return null
      const entry = previous || { index, request_id: payload.request_id, request_fingerprint: payload.request_fingerprint }
      entries.set(entry.request_id, entry)
      return entry
    },
    accept(entry, response, status) {
      if (status !== 202 || response?.accepted !== true || response.run_id !== config.run_id
        || response.request_id !== entry.request_id || response.request_fingerprint !== entry.request_fingerprint) return false
      accepted.add(entry.index)
      return true
    }
  }
}

function permittedWrite(config, method, url, payload, sequence, review) {
  if (method !== 'POST' || url.search) return null
  const runPath = `/api/v1/runs/${encodeURIComponent(config.run_id)}`
  if (config.driver === 'save_restart') return url.pathname === runPath + '/draft-decision' && permittedSave(payload, review) ? { kind: 'save' } : null
  if (!['edit', 'message_recovery', 'message_uncertain'].includes(config.driver) || url.pathname !== runPath + '/messages') return null
  const identity = sequence.permit(payload)
  return identity ? { kind: 'message', identity } : null
}

function comparable(snapshot) {
  const state = snapshot.state || {}
  return { run_id: snapshot.run_id, spec: state.trip_spec, selected_plan: state.selected_plan, selected_offer: snapshot.selected_offer,
    turn_id: state.turn_id, turn_budget: state.turn_budget, model_token_count: state.model_token_count, tool_call_count: state.tool_call_count,
    model_call_count: Array.isArray(state.model_calls) ? state.model_calls.length : null,
    execution_outcome: state.execution_outcome, action_results: state.action_results }
}

function childEnvironment(config, proxyOrigin) {
  const env = { ...process.env }
  for (const key of Object.keys(env)) if (/^(PLANGO_|PLANORA_|YOYU_|XIAONIAN_|OPENAI_|AMAP_|LONGCAT_|MINIMAX_|ANTHROPIC_|AZURE_|GOOGLE_|GEMINI_|DEEPSEEK_|QWEN_|LLM_|ELECTRON_)/i.test(key)
    || /(?:KEY|TOKEN|SECRET|PASSWORD|PROXY|NODE_OPTIONS|NODE_EXTRA_CA_CERTS)/i.test(key)) delete env[key]
  return { ...env, PLANGO_BACKEND_AUTOSTART: 'false', PLANGO_BACKEND_URL: proxyOrigin,
    PLANGO_BACKEND_TOKEN: config.backend_token, PLANGO_DATA_DIR: config.client_data_dir, PLANGO_LLM_PROVIDER: 'openai',
    PLANGO_CITY: '重庆', OPENAI_API_KEY: '', OPENAI_BASE_URL: proxyOrigin, OPENAI_MODEL: '',
    AMAP_WEBSERVICE_KEY: '', AMAP_JS_KEY: '', AMAP_JS_SECURITY: '', LONGCAT_API_KEY: '', MINIMAX_API_KEY: '',
    OMP_NUM_THREADS: '1', OPENBLAS_NUM_THREADS: '1', NODE_USE_ENV_PROXY: '0' }
}

async function makeProxy(config, result, review) {
  const counts = result.transport_counts
  let lookupBroken = false, dropNext = ['message_recovery', 'message_uncertain'].includes(config.driver)
  const sequence = messageSequence(config)
  const sockets = new Set(), upstreams = new Set()
  const messagePath = `/api/v1/runs/${encodeURIComponent(config.run_id)}/messages`
  const savePath = `/api/v1/runs/${encodeURIComponent(config.run_id)}/draft-decision`
  const server = http.createServer(async (request, response) => {
    try {
      if (!request.url?.startsWith('/api/v1/') || request.url.startsWith('//')) { response.writeHead(403).end(); return }
      const url = new URL(request.url, config.backend_url)
      const method = request.method || 'GET'
      const messagePost = method === 'POST' && url.pathname === messagePath
      const savePost = method === 'POST' && url.pathname === savePath
      const lookup = method === 'GET' && url.pathname.startsWith('/api/v1/requests/')
      if (messagePost) counts.message_post_attempts++
      if (savePost) counts.draft_save_attempts++
      if (lookup) counts.lookup_attempts++
      if (method === 'POST' && url.pathname === '/api/v1/runs') counts.create_run_attempts++
      const chunks = []; let size = 0
      for await (const chunk of request) { size += chunk.length; if (size > 1_000_000) throw new Error('oversize_request'); chunks.push(chunk) }
      const body = Buffer.concat(chunks)
      let requestIdentity = null
      if (!['GET', 'HEAD'].includes(method)) {
        let payload; try { payload = JSON.parse(body.toString()) } catch { payload = {} }
        const permitted = permittedWrite(config, method, url, payload, sequence, review)
        if (!permitted) { counts.unexpected_write_attempts++; response.writeHead(403, { 'content-type': 'application/json' }).end('{"detail":"quality_driver_write_scope"}'); return }
        if (messagePost) { counts.forwarded_message_posts++; requestIdentity = permitted.identity }
        if (savePost) counts.forwarded_draft_saves++
      }
      if (lookup && lookupBroken) { counts.injected_lookup_503++; response.writeHead(503, { 'content-type': 'application/json' }).end('{"detail":"controlled_lookup_outage"}'); return }
      if (lookup) counts.forwarded_lookups++
      const upstream = http.request(url, { method, headers: { ...request.headers, host: new URL(config.backend_url).host } }, incoming => {
        if (messagePost) {
          const bytes = []
          incoming.on('data', chunk => bytes.push(chunk))
          incoming.on('end', () => {
            let accepted; try { accepted = JSON.parse(Buffer.concat(bytes).toString()) } catch { accepted = null }
            const valid = sequence.accept(requestIdentity, accepted, incoming.statusCode)
            const receipt = { script_step: requestIdentity.index + 1, request_id: requestIdentity.request_id, request_fingerprint: requestIdentity.request_fingerprint,
              run_id: accepted?.run_id || null, upstream_http_status: incoming.statusCode, accepted: valid, replayed: accepted?.replayed === true, at: new Date().toISOString() }
            ;(result.message_responses ||= []).push(receipt)
            if (valid) {
              ;(result.acceptances ||= []).push(receipt)
              result.acceptance = receipt // Legacy single-message evidence field.
            }
            if (valid && dropNext) {
              counts.accepted_responses_dropped++; lookupBroken = true; dropNext = false
              result.driver_actions.push({ action: 'drop_first_real_accepted_response', at: new Date().toISOString() })
              response.destroy(); return
            }
            if (!valid && dropNext) {
              result.fault_not_injected = { upstream_http_status: incoming.statusCode || null, reason: 'Expected a real matching 202 acceptance before dropping a response' }
            }
            response.writeHead(incoming.statusCode || 502, incoming.headers); response.end(Buffer.concat(bytes))
          })
          incoming.on('error', () => response.destroy())
        } else { response.writeHead(incoming.statusCode || 502, incoming.headers); incoming.pipe(response) }
      })
      upstreams.add(upstream)
      upstream.once('close', () => upstreams.delete(upstream))
      response.once('close', () => upstream.destroy())
      upstream.on('error', () => { if (response.destroyed) return; if (!response.headersSent) response.writeHead(502, { 'content-type': 'application/json' }); response.end('{"detail":"upstream_unavailable"}') })
      upstream.setTimeout(35_000, () => upstream.destroy(new Error('upstream_timeout')))
      upstream.end(body)
    } catch { if (!response.headersSent) response.writeHead(502); response.end() }
  })
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)) })
  await new Promise((resolveListen, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolveListen) })
  return { origin: `http://127.0.0.1:${server.address().port}`, armEdit: index => sequence.arm(index), recover: () => { assert.equal(config.driver, 'message_recovery'); lookupBroken = false },
    close: async () => {
      // Closing downstream sockets does not close the API's long-lived SSE
      // responses. Own and terminate both halves before declaring cleanup done.
      const closed = [...upstreams].map(request => new Promise(resolveClose => { request.once('close', resolveClose); request.destroy() }))
      for (const socket of sockets) socket.destroy()
      await Promise.all([...closed, new Promise(resolveClose => server.close(resolveClose))])
    } }
}

function bootstrap(config, proxyOrigin) {
  return `const {app,session,shell}=require('electron');
const {join}=require('node:path');const {pathToFileURL}=require('node:url');
const allowedOrigins=new Set(${JSON.stringify([proxyOrigin, config.backend_url])});
app.setPath('userData',${JSON.stringify(config.profile_dir)});app.setPath('sessionData',${JSON.stringify(config.profile_dir)});
app.commandLine.appendSwitch('disable-gpu');app.commandLine.appendSwitch('no-proxy-server');
app.commandLine.appendSwitch('disable-background-networking');
process.chdir(${JSON.stringify(config.case_dir)});
global.__qualityBlockedRequests=0;
function protect(s){
 const register=s.webRequest.onBeforeRequest.bind(s.webRequest);
 s.webRequest.onBeforeRequest=(...args)=>{
  const listener=typeof args.at(-1)==='function'?args.pop():null;
  return register(...args,(req,cb)=>{let allow=false;try{const u=new URL(req.url);allow=['file:','data:','about:','devtools:'].includes(u.protocol)||allowedOrigins.has(u.origin)}catch{};
   if(!allow){global.__qualityBlockedRequests++;cb({cancel:true});return}if(listener)listener(req,cb);else cb({cancel:false})});
 };
 s.webRequest.onBeforeRequest((req,cb)=>cb({cancel:false}));
}
app.on('session-created',protect);
// Keep the bundled empty share server on an owned dynamic loopback port.
const net=require('node:net'),listen=net.Server.prototype.listen;
net.Server.prototype.listen=function(...args){if(args[0]===8799)return listen.call(this,0,'127.0.0.1',...args.slice(1));return listen.apply(this,args)};
shell.openExternal=async()=>{global.__qualityBlockedRequests++;throw new Error('External navigation disabled for this controlled case')};
import(pathToFileURL(${JSON.stringify(join(ROOT, 'out/main/index.js'))}).href);
`
}

async function runDriver(config) {
  const result = { schema: 'plango.quality-desktop.v1', case_id: config.case_id, driver: config.driver, completion_scope: 'driver_actions_only', run_id: config.run_id, started_at: new Date().toISOString(),
    status: 'running', driver_actions: [], checkpoints: [], transport_counts: { message_post_attempts: 0, forwarded_message_posts: 0, draft_save_attempts: 0, forwarded_draft_saves: 0,
      lookup_attempts: 0, forwarded_lookups: 0, injected_lookup_503: 0, accepted_responses_dropped: 0, unexpected_write_attempts: 0, create_run_attempts: 0 },
    backend_restarted: false, desktop_restarted: false, cleanup: {}, limitations: ['The root runner must independently inspect persisted DB state and restart the backend; this driver never sets completion state.'] }
  let proxy, electron, page, stopped = false
  const record = (action, details = {}) => result.driver_actions.push({ action, at: new Date().toISOString(), ...details })
  const onSignal = () => { stopped = true; if (electron) void electron.close().catch(() => {}) }
  process.once('SIGTERM', onSignal); process.once('SIGINT', onSignal)
  const wait = async (label, predicate, timeout = 30_000) => {
    const until = Date.now() + timeout
    while (Date.now() < until) {
      if (stopped) throw new Error('driver_interrupted')
      if (result.transport_counts.unexpected_write_attempts) throw new Error('unexpected_write_blocked')
      if (await predicate()) return
      await sleep(250)
    }
    throw new Error(`timeout:${label}`)
  }
  const api = async path => {
    assert(path.startsWith('/api/v1/'))
    const response = await fetch(config.backend_url + path, { headers: { authorization: `Bearer ${config.backend_token}` }, signal: AbortSignal.timeout(8_000) })
    if (!response.ok) throw new Error(`read_only_api:${response.status}`)
    return response.json()
  }
  const snapshot = () => api(`/api/v1/runs/${encodeURIComponent(config.run_id)}`)
  const capture = async name => {
    const run = await snapshot(), events = []; let cursor = 0
    while (cursor < run.event_seq) {
      const reply = await api(`/api/v1/runs/${encodeURIComponent(config.run_id)}/events?after=${cursor}`)
      const batch = (reply.events || []).filter(event => event.run_id === config.run_id && event.seq > cursor && event.seq <= run.event_seq)
      if (!batch.length) break
      events.push(...batch); cursor = batch.at(-1).seq
    }
    const file = `${String(result.checkpoints.length + 1).padStart(2, '0')}-${name}`
    const checkpoint = { id: name, captured_at: new Date().toISOString() }
    await jsonFile(join(config.case_dir, `${file}.snapshot.json`), { snapshot: run, events, checkpoint })
    await jsonFile(join(config.case_dir, `${file}.events.json`), { events })
    const entry = { ...checkpoint, snapshot: `${file}.snapshot.json`, events: `${file}.events.json`, ui: null, screenshot: null,
      event_seq: run.event_seq, version: run.version, phase: run.phase }
    // Preserve the captured checkpoint reference even if the later screenshot
    // fails or the desktop closes; never silently replace it with a final GET.
    result.checkpoints.push(entry)
    const ui = page ? await page.evaluate(() => ({ active_run: localStorage.getItem('plango_active_run'), text: document.body.innerText,
      pending_status: document.querySelector('[aria-label="消息发送状态"]')?.textContent || null })).catch(() => null) : null
    await jsonFile(join(config.case_dir, `${file}.ui.json`), ui)
    entry.ui = `${file}.ui.json`
    if (page) { await page.screenshot({ path: join(config.case_dir, `${file}.png`) }); entry.screenshot = `${file}.png` }
    return run
  }
  const closeDesktop = async () => {
    if (!electron) return
    const owned = electron, child = owned.process(); page = null
    try { result.blocked_external_requests = (result.blocked_external_requests || 0) + await owned.evaluate(() => global.__qualityBlockedRequests || 0) } catch {}
    let timer
    try { await Promise.race([owned.close(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('desktop_close_timeout')), 12_000) })]) }
    catch { if (child.exitCode === null) child.kill('SIGTERM'); await sleep(1000); if (child.exitCode === null) child.kill('SIGKILL') }
    finally { clearTimeout(timer) }
    for (let attempt = 0; attempt < 40 && child.exitCode === null && child.signalCode === null; attempt++) await sleep(250)
    assert(child.exitCode !== null || child.signalCode !== null, 'Owned desktop did not exit')
    electron = null
    record('close_owned_desktop', { exited: true, profile_preserved: true })
  }
  const openDesktop = async name => {
    assert(!electron, 'An existing desktop must exit before another consumer starts')
    const bootFile = join(config.case_dir, `desktop-bootstrap-${name}.cjs`)
    await writeFile(bootFile, bootstrap(config, proxy.origin), { flag: 'wx', mode: 0o600 })
    electron = await _electron.launch({ executablePath: join(ROOT, 'node_modules/electron/dist/electron'), args: ['--no-sandbox', bootFile],
      cwd: config.case_dir, env: childEnvironment(config, proxy.origin), timeout: 30_000 })
    page = await electron.firstWindow(); await page.getByRole('textbox', { name: '任务输入', exact: true }).waitFor({ timeout: 25_000 })
    await wait('service_ready', async () => (await page.locator('body').innerText()).includes('运行服务已连接'))
    record('open_owned_desktop', { launch: name, pid: electron.process().pid })
    await wait('history_run_available', async () => page.evaluate(runId => {
      try { return JSON.parse(localStorage.getItem('plango_sessions') || '[]').some(item => item.runId === runId) } catch { return false }
    }, config.run_id))
    const active = await page.evaluate(() => localStorage.getItem('plango_active_run'))
    if (active !== config.run_id) {
      const title = await page.evaluate(runId => JSON.parse(localStorage.getItem('plango_sessions') || '[]').find(item => item.runId === runId)?.title, config.run_id)
      await page.getByTitle('历史会话', { exact: true }).click()
      const button = page.getByRole('button', { name: `打开会话：${title}`, exact: true })
      assert.equal(await button.count(), 1, 'History target must be unambiguous')
      await button.click(); record('open_existing_run_from_history')
    } else record('automatic_same_profile_restore')
    await wait('active_original_run', async () => await page.evaluate(() => localStorage.getItem('plango_active_run')) === config.run_id)
    const outcomeButton = page.getByRole('button', { name: '成果区', exact: true })
    if (await outcomeButton.count()) await outcomeButton.click()
  }
  const waitUiSummary = async run => {
    if (run.state?.trip_spec?.party_size && run.version) await wait('canonical_requirements_rendered', async () => {
      const people = page.getByLabel('同行人数', { exact: true })
      return await people.count() === 1 && await people.inputValue() === String(run.state.trip_spec.party_size)
    })
    const reason = String(run.state?.clarification?.question || run.state?.browser_wait?.message || run.state?.reason || '').replace(/\s+/g, '')
    if (reason) await wait('current_summary_in_ui', async () => (await page.locator('[aria-label="对话记录"]').innerText()).replace(/\s+/g, '').includes(reason))
  }
  try {
    const initial = await snapshot()
    assert.equal(initial.run_id, config.run_id)
    assert(!initial.command_pending && !initial.state?.browser_receipt_pending && !initial.state?.browser_wait, 'Seed must have no pending command/browser work')
    assert(!(initial.state?.action_results || []).some(action => ['RUNNING', 'UNKNOWN'].includes(action.status)), 'Seed must have no uncertain actions')
    assert(initial.draft_review && !initial.outcome, 'Seed must be a real unsaved reviewable draft')
    assert.equal(initial.draft_review.scope, 'draft_ready')
    proxy = await makeProxy(config, result, initial.draft_review)
    await openDesktop('initial')
    await wait('real_draft_save_button', async () => await page.getByRole('button', { name: '保存草案', exact: true }).isVisible())
    await capture('initial-reviewable-draft')
    if (config.driver === 'edit') {
      result.turn_results = []; result.stop_reason = 'script_finished'
      for (const [index, message] of config.messages.entries()) {
        const before = await snapshot()
        proxy.armEdit(index)
        await page.getByRole('textbox', { name: '任务输入', exact: true }).fill(message)
        await page.getByRole('button', { name: '发送消息', exact: true }).click()
        record('send_preregistered_edit', { script_step: index + 1 })
        await wait('edit_acceptance_response', async () => result.message_responses?.some(row => row.script_step === index + 1))
        const receipt = result.message_responses.find(row => row.script_step === index + 1)
        if (!receipt.accepted) {
          await capture(`t${index + 1}`); result.stop_reason = 'input_not_accepted'; break
        }
        await wait('edit_delivery_retrieved', async () => !(await page.locator('[aria-label="消息发送状态"]').count()))
        await wait('edit_actual_stop', async () => {
          const run = await snapshot()
          return !run.command_pending && run.event_seq > before.event_seq
            && (Number(run.state?.turn_id) > Number(before.state?.turn_id || 1) || TERMINAL.has(run.phase))
            && (run.draft_review || run.state?.clarification || run.state?.browser_wait || run.interrupt_id || TERMINAL.has(run.phase))
        }, 330_000)
        await waitUiSummary(await snapshot())
        const current = await capture(`t${index + 1}`)
        result.turn_results.push({ script_step: index + 1, request_id: receipt.request_id, request_fingerprint: receipt.request_fingerprint,
          phase: current.phase, turn_id: current.state?.turn_id, draft_review: !!current.draft_review })
        const blocked = current.state?.browser_wait ? 'unexpected_browser_wait'
          : current.state?.clarification && !current.draft_review ? 'unscripted_clarification'
            : current.interrupt_id && !current.draft_review ? 'unexpected_approval'
              : TERMINAL.has(current.phase) && current.phase !== 'SUCCEEDED' ? 'product_failure'
                : !current.draft_review && index < config.messages.length - 1 ? 'unexpected_stop_before_scripted_next_turn' : null
        if (blocked) { result.stop_reason = blocked; record('stop_without_unscripted_answer', { reason: blocked }); break }
      }
      assert.equal(result.transport_counts.accepted_responses_dropped, 0)
      assert.equal(result.transport_counts.injected_lookup_503, 0)
      assert.equal(result.transport_counts.forwarded_draft_saves, 0)
    } else if (config.driver === 'save_restart') {
      await page.getByRole('button', { name: '保存草案', exact: true }).click(); record('click_existing_save_draft')
      await wait('saved_draft_ready', async () => { const run = await snapshot(); return !run.command_pending && run.phase === 'SUCCEEDED' && run.state?.execution_outcome?.data?.scope === 'draft_ready' })
      await waitUiSummary(await snapshot())
      const saved = await capture('saved-draft')
      result.saved_baseline = { state_hash: hash(comparable(saved)), values: comparable(saved) }
      await closeDesktop(); await openDesktop('restarted'); result.desktop_restarted = true
      await waitUiSummary(await snapshot())
      const restored = await capture('desktop-restarted')
      result.restored_comparison = { baseline_hash: result.saved_baseline.state_hash, restored_hash: hash(comparable(restored)), equal: hash(comparable(restored)) === result.saved_baseline.state_hash }
      assert(result.restored_comparison.equal, 'Same-profile desktop restart changed original task/spec/plan/offer/usage')
      assert.equal(result.transport_counts.forwarded_draft_saves, 1)
      assert.equal(result.transport_counts.message_post_attempts, 0)
    } else {
      await page.getByRole('textbox', { name: '任务输入', exact: true }).fill(config.message)
      await page.getByRole('button', { name: '发送消息', exact: true }).click(); record('send_single_fixed_user_message')
      await wait('delivery_unconfirmed', async () => {
        if (result.fault_not_injected) throw new Error('fault_injection_not_observed')
        return result.transport_counts.accepted_responses_dropped === 1 && await page.getByRole('button', { name: '核对送达并取回', exact: true }).isVisible()
      })
      await capture('accepted-response-lost')
      if (config.driver === 'message_uncertain') {
        record('leave_delivery_unconfirmed', { lookup_outage_preserved: true, recovery_clicked: false })
        result.recovery = { performed: false, delivery_state: 'unconfirmed', lookup_outage_preserved: true }
        assert.equal(result.transport_counts.forwarded_lookups, 0)
      } else {
        proxy.recover(); record('restore_receipt_lookup')
        await page.getByRole('button', { name: '核对送达并取回', exact: true }).click(); record('click_existing_delivery_lookup')
        await wait('original_delivery_retrieved', async () => !(await page.locator('[aria-label="消息发送状态"]').count()))
        await wait('new_draft_or_terminal', async () => { const run = await snapshot(); return Number(run.state?.turn_id) > Number(initial.state?.turn_id || 1) && !run.command_pending && (!!run.draft_review || TERMINAL.has(run.phase)) }, 330_000)
        await waitUiSummary(await snapshot())
        const final = await capture('same-request-recovered')
        result.recovery = { performed: true, run_id_preserved: final.run_id === initial.run_id, before_turn: initial.state?.turn_id, after_turn: final.state?.turn_id,
          before_party_size: initial.state?.trip_spec?.party_size, after_party_size: final.state?.trip_spec?.party_size, phase: final.phase }
        assert(result.transport_counts.forwarded_lookups > 0)
      }
      assert.equal(result.transport_counts.forwarded_message_posts, 1, 'Lookup recovery must not resend the message')
      assert.equal(result.transport_counts.accepted_responses_dropped, 1)
      assert(result.transport_counts.injected_lookup_503 > 0)
      assert.equal(result.transport_counts.forwarded_draft_saves, 0)
    }
    assert.equal(result.transport_counts.create_run_attempts, 0)
    assert.equal(result.transport_counts.unexpected_write_attempts, 0)
    result.status = 'completed'
  } catch (error) {
    result.status = 'failed'; result.error = String(error?.message || error).split(config.backend_token).join('[redacted]').slice(0, 1000)
    if (proxy) try { await capture('failure') } catch { result.failure_capture_unavailable = true }
  } finally {
    try { await closeDesktop(); result.cleanup.desktop_closed = true } catch { result.cleanup.desktop_closed = false; result.status = 'failed' }
    try { await proxy?.close(); result.cleanup.proxy_closed = true } catch { result.cleanup.proxy_closed = false; result.status = 'failed' }
    result.cleanup.backend_untouched = true; result.cleanup.profile_preserved = true; result.finished_at = new Date().toISOString()
    process.removeListener('SIGTERM', onSignal); process.removeListener('SIGINT', onSignal)
    await jsonFile(join(config.case_dir, 'desktop-result.json'), result)
  }
  return result
}

async function main() {
  assert.equal(process.argv.length, 3, 'Usage: node scripts/quality_desktop_cases.cjs CONFIG.json')
  let config = JSON.parse(await readFile(resolve(process.argv[2]), 'utf8'))
  const url = new URL(config.backend_url)
  assert(url.protocol === 'http:' && url.hostname === '127.0.0.1' && Number(url.port) >= 1024 && !['8011', '8012', '5432', '6379', '8799'].includes(url.port)
    && !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash, 'Owned dynamic loopback API required')
  config.backend_url = url.origin
  for (const name of ['case_dir', 'client_data_dir', 'profile_dir']) config[name] = resolve(config[name])
  assert(inside(join(ROOT, 'output'), config.case_dir), 'Private case_dir must be under this repository output/')
  assert(inside(config.case_dir, config.client_data_dir) && inside(config.case_dir, config.profile_dir) && config.client_data_dir !== config.profile_dir)
  const caseInput = resolve(config.case_input_path || join(config.case_dir, 'case-input.json'))
  assert(inside(config.case_dir, caseInput) && caseInput.endsWith('.json'), 'Case declaration must be private case-owned JSON')
  const declaration = await readFile(caseInput, 'utf8').then(JSON.parse).catch(error => { if (error.code !== 'ENOENT') throw error; return null })
  config = driverConfig(config, declaration?.case || declaration)
  assert(typeof config.backend_token === 'string' && config.backend_token.length >= 8 && typeof config.browser_session_id === 'string' && config.browser_session_id)
  assert(typeof config.run_id === 'string' && /^[a-zA-Z0-9:_-]+$/.test(config.run_id))
  for (const name of ['case_dir', 'client_data_dir', 'profile_dir']) { await mkdir(config[name], { recursive: true, mode: 0o700 }); assert.equal(await realpath(config[name]), config[name], 'Symlinked case resources are not allowed') }
  for (const name of ['.env', '.env.example', 'desktop-result.json']) assert(!(await stat(join(config.case_dir, name)).catch(() => null)), 'Existing case config/result must not be overwritten or loaded')
  assert(!(await stat(join(config.profile_dir, 'plango-config.json')).catch(() => null)), 'Fresh isolated profile required; existing runtime override could contain keys')
  const identityPath = join(config.client_data_dir, 'desktop-identity.json'), identity = { token: config.backend_token, browserSessionId: config.browser_session_id }
  const existing = await readFile(identityPath, 'utf8').catch(error => { if (error.code !== 'ENOENT') throw error; return null })
  if (existing) assert.deepEqual(JSON.parse(existing), identity, 'Existing identity mismatch; never replace it')
  else await jsonFile(identityPath, identity)
  const result = await runDriver(config)
  const summary = JSON.stringify({ case_id: result.case_id, status: result.status, checkpoints: result.checkpoints.length, desktop_restarted: result.desktop_restarted, backend_restarted: false, cleanup: result.cleanup })
  const exitCode = result.status === 'completed' ? 0 : 1
  // Result writes and owned resource shutdown have already completed. Flush the
  // final summary before exiting so library handles cannot strand the runner.
  if (result.cleanup.desktop_closed && result.cleanup.proxy_closed) process.stdout.write(summary + '\n', () => process.exit(exitCode))
  else { console.log(summary); process.exitCode = 1 }
}
if (require.main === module) main().catch(() => { console.error('Desktop case driver failed validation or could not preserve its private result; no existing result was overwritten.'); process.exitCode = 1 })
module.exports = { childEnvironment, comparable, makeProxy, bootstrap, driverConfig, permittedMessage, permittedSave, messageSequence, permittedWrite }
