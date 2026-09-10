// Controlled forward-version persistence check. No app backend, merchant site, or original profile is used.
// xvfb-run -a node scripts/check-electron-upgrade.cjs --old-electron /old/electron/electron --report /new/path/result.json
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const { createHash, randomUUID } = require('node:crypto')
const { accessSync, constants, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { dirname, join, resolve } = require('node:path')

if (!process.versions.electron) {
  const { values } = require('node:util').parseArgs({ options: {
    'old-electron': { type: 'string' }, 'new-electron': { type: 'string', default: 'node_modules/electron/dist/electron' }, report: { type: 'string' }
  } })
  assert(values['old-electron'], 'Provide --old-electron pointing to the complete extracted old Electron directory')
  const oldBinary = resolve(values['old-electron']), newBinary = resolve(values['new-electron'])
  for (const binary of [oldBinary, newBinary]) accessSync(binary, constants.X_OK)
  const version = binary => readFileSync(join(dirname(binary), 'version'), 'utf8').trim().replace(/^v/, '')
  const beforeVersion = version(oldBinary), afterVersion = version(newBinary)
  assert(/^\d+\.\d+\.\d+$/.test(beforeVersion) && /^\d+\.\d+\.\d+$/.test(afterVersion), 'Stable source and target versions are required')
  const beforeParts = beforeVersion.split('.').map(Number), afterParts = afterVersion.split('.').map(Number)
  const firstChange = beforeParts.findIndex((part, index) => part !== afterParts[index])
  assert(firstChange >= 0 && afterParts[firstChange] > beforeParts[firstChange], 'Only a forward Electron upgrade can be tested')
  const report = values.report ? resolve(values.report) : undefined
  if (report) { assert(!existsSync(report), 'Report already exists; preserve earlier evidence'); mkdirSync(dirname(report), { recursive: true, mode: 0o700 }) }
  const work = mkdtempSync(join(report ? dirname(report) : tmpdir(), 'plango-electron-upgrade-'))
  const profile = join(work, 'profile'), nonce = randomUUID()
  mkdirSync(join(profile, 'harness'), { recursive: true, mode: 0o700 })
  writeFileSync(join(work, 'fixture.html'), '<!doctype html><title>PlanGo Electron upgrade fixture</title><p>Only controlled local persistence is checked.</p>')
  const originals = {
    'desktop-identity.json': JSON.stringify({ token: 'fixture-only-' + nonce, browserSessionId: 'fixture-session-' + nonce }),
    'browser-receipts.json': JSON.stringify([['fixture-command', { command: { command_id: 'fixture-command', run_id: 'fixture-run', browser_session_id: 'fixture-session-' + nonce, operation: 'click', arguments: { idx: 0 } }, result: { command_id: 'fixture-command', ok: false, outcome: 'unknown', error_kind: 'desktop_restarted_during_command' }, delivered: false }]])
  }
  for (const [name, content] of Object.entries(originals)) writeFileSync(join(profile, 'harness', name), content, { mode: 0o600 })
  writeFileSync(join(work, 'fixture.json'), JSON.stringify({ nonce, beforeVersion, afterVersion }), { mode: 0o600 })
  const result = { format: 1, status: 'failed', created_at: new Date().toISOString(), source_electron: beforeVersion, target_electron: afterVersion,
    scope: 'isolated_profile_compatibility', profile, phases: [],
    limitations: ['Controlled cookie uses the basic encryption backend; actual system keyring and merchant login are not validated.', 'No application task, backend, or business action is executed.'] }
  try {
    const env = { ...process.env, XDG_CONFIG_HOME: work }
    for (const key of ['ELECTRON_RUN_AS_NODE', 'ELECTRON_RENDERER_URL', 'ELECTRON_OVERRIDE_DIST_PATH']) delete env[key]
    for (const [phase, binary] of [['seed', oldBinary], ['old-reopen', oldBinary], ['new-reopen', newBinary]]) {
      const child = spawnSync(binary, ['--no-sandbox', __filename, phase, work], { cwd: work, env, encoding: 'utf8', timeout: 35000 })
      writeFileSync(join(work, phase + '.log'), (child.stdout || '') + (child.stderr || ''), { mode: 0o600 })
      assert(!child.error && child.status === 0, `${phase} failed; isolated evidence retained at ${work}`)
      result.phases.push(JSON.parse(readFileSync(join(work, phase + '.json'), 'utf8')))
      for (const [name, content] of Object.entries(originals)) assert.equal(readFileSync(join(profile, 'harness', name), 'utf8'), content, `${name} changed during ${phase}`)
    }
    assert.equal(result.phases[0].electron, beforeVersion)
    assert.equal(result.phases[1].electron, beforeVersion)
    assert.equal(result.phases[2].electron, afterVersion)
    result.identity_sha256 = createHash('sha256').update(originals['desktop-identity.json']).digest('hex')
    result.unknown_receipt_sha256 = createHash('sha256').update(originals['browser-receipts.json']).digest('hex')
    result.status = 'passed'
  } catch (error) {
    result.error = String(error.message || error)
    process.exitCode = 1
  } finally {
    const destination = report || join(work, 'result.json')
    writeFileSync(destination, JSON.stringify(result, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
    console.log(`Electron ${beforeVersion} -> ${afterVersion}: ${result.status}; ${destination}`)
  }
} else {
  const { app, BrowserWindow, session } = require('electron')
  const [phase, work] = process.argv.slice(-2)
  assert(['seed', 'old-reopen', 'new-reopen'].includes(phase) && existsSync(join(work, 'fixture.json')), 'Only the orchestrator-created fixture may run')
  const fixture = JSON.parse(readFileSync(join(work, 'fixture.json'), 'utf8'))
  app.setName('PlanGo')
  app.setPath('userData', join(work, 'profile'))
  app.setPath('sessionData', join(work, 'profile'))
  app.commandLine.appendSwitch('password-store', 'basic')
  app.commandLine.appendSwitch('disable-gpu')
  app.commandLine.appendSwitch('disable-background-networking')
  app.commandLine.appendSwitch('no-proxy-server')
  app.commandLine.appendSwitch('host-resolver-rules', 'MAP * ~NOTFOUND')
  let window
  const watchdog = setTimeout(() => { window?.destroy(); app.exit(1) }, 25000)
  app.whenReady().then(async () => {
    const browser = session.fromPartition('persist:plango')
    let blockedRequests = 0
    for (const partition of [session.defaultSession, browser]) {
      partition.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
      partition.webRequest.onBeforeRequest((details, callback) => {
        const blocked = !details.url.startsWith('file:') && details.url !== 'about:blank'
        if (blocked) blockedRequests++
        callback({ cancel: blocked })
      })
    }
    window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } })
    await window.loadFile(join(work, 'fixture.html'))
    const storage = { plango_active_run: 'fixture-run-' + fixture.nonce, plango_composer: JSON.stringify({ activeSessionId: 'fixture-run-' + fixture.nonce, drafts: {}, pendingDelivery: null }) }
    if (phase === 'seed') {
      await browser.cookies.set({ url: 'https://plango-upgrade.invalid', name: 'fixture_login', value: fixture.nonce, secure: true, httpOnly: true, sameSite: 'lax', expirationDate: Math.floor(Date.now() / 1000) + 86400 })
      await window.webContents.executeJavaScript(`Object.entries(${JSON.stringify(storage)}).forEach(([key,value])=>localStorage.setItem(key,value))`)
    }
    assert.equal((await browser.cookies.get({ url: 'https://plango-upgrade.invalid' })).find(cookie => cookie.name === 'fixture_login')?.value, fixture.nonce)
    const current = await window.webContents.executeJavaScript(`Object.fromEntries(${JSON.stringify(Object.keys(storage))}.map(key=>[key,localStorage.getItem(key)]))`)
    assert.deepEqual(current, storage)
    // This deliberate request must fail in the session hook before any network connection.
    await assert.rejects(browser.fetch('https://plango-upgrade.invalid/blocked-fixture'))
    assert(blockedRequests > 0)
    await browser.cookies.flushStore()
    await browser.flushStorageData()
    await session.defaultSession.flushStorageData()
    writeFileSync(join(work, phase + '.json'), JSON.stringify({ phase, electron: process.versions.electron, chromium: process.versions.chrome,
      cookie_preserved: true, local_storage_preserved: true, external_request_blocked: true, browser_session: 'persist:plango' }))
    clearTimeout(watchdog)
    window.destroy()
    app.quit()
  }).catch(error => { console.error(error); clearTimeout(watchdog); window?.destroy(); app.exit(1) })
}
