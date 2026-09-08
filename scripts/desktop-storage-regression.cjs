// Real Chromium persistence across two app identities, using isolated fixture data and the basic encryption backend.
// Run: npm run build && xvfb-run -a node scripts/desktop-storage-regression.cjs
const assert = require('node:assert/strict')
const { existsSync, mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } = require('node:fs')
const { join, resolve } = require('node:path')
const { tmpdir } = require('node:os')

if (!process.versions.electron) {
  const { buildSync } = require('esbuild')
  const { spawnSync } = require('node:child_process')
  const work = mkdtempSync(join(tmpdir(), 'plango-chromium-migration-'))
  try {
    buildSync({ entryPoints: ['src/main/storageMigration.ts'], outfile: join(work, 'migration.cjs'), bundle: true, platform: 'node', format: 'cjs' })
    buildSync({ entryPoints: ['src/renderer/src/lib/storageMigration.ts'], outfile: join(work, 'renderer.js'), bundle: true, format: 'iife', globalName: 'Migration' })
    writeFileSync(join(work, 'fixture.html'), '<!doctype html><title>PlanGo storage migration fixture</title>')
    mkdirSync(join(work, 'yoyu/harness'), { recursive: true })
    writeFileSync(join(work, 'yoyu/harness/desktop-identity.json'), '{"token":"fixture-token","browserSessionId":"unchanged-session"}')
    writeFileSync(join(work, 'yoyu/harness/browser-receipts.json'), '[["pending",{"command":{"command_id":"pending"},"result":{"outcome":"unknown"}}]]')
    const packageDirectory = join(work, 'application')
    mkdirSync(packageDirectory)
    writeFileSync(join(packageDirectory, 'package.json'), JSON.stringify({ name: 'plango', productName: 'PlanGo', main: __filename }))
    const env = { ...process.env, XDG_CONFIG_HOME: work }; delete env.ELECTRON_RUN_AS_NODE
    for (const phase of ['seed', 'verify']) {
      const result = spawnSync(require('electron'), ['--no-sandbox', phase === 'seed' ? __filename : packageDirectory, phase, work], { env, encoding: 'utf8', timeout: 25000 })
      assert(!result.error, `${phase}: ${result.error?.message}\n${result.stderr}\n${result.stdout}`)
      assert.equal(result.status, 0, `${phase}: ${result.stderr}\n${result.stdout}`)
      assert(result.stdout.includes(`${phase} completed`), 'Each phase must finish its persistence assertions')
    }
    const conflicting = join(work, 'conflict')
    mkdirSync(join(conflicting, 'yoyu/harness'), { recursive: true })
    mkdirSync(join(conflicting, 'PlanGo'), { recursive: true })
    writeFileSync(join(conflicting, 'yoyu/harness/desktop-identity.json'), '{"browserSessionId":"preserved"}')
    writeFileSync(join(conflicting, 'PlanGo/Preferences'), '{}')
    writeFileSync(join(packageDirectory, 'package.json'), JSON.stringify({ name: 'plango', productName: 'PlanGo', main: resolve('out/main/index.js') }))
    const failure = spawnSync(require('electron'), ['--no-sandbox', packageDirectory], { env: { ...env, XDG_CONFIG_HOME: conflicting }, cwd: work, encoding: 'utf8', timeout: 10000 })
    assert(!failure.error, 'Conflicting profiles must terminate rather than leave an error dialog alive')
    assert.equal(failure.status, 1)
    assert(failure.stderr.includes('[plango] Desktop initialization failed:'))
    assert(!failure.stdout.includes('[plango] Desktop ready'))
    assert.equal(JSON.parse(readFileSync(join(conflicting, 'yoyu/harness/desktop-identity.json'))).browserSessionId, 'preserved')
    console.log('Real Chromium migration passed: cookie/login token, renderer localStorage, original browserSessionId and UNKNOWN receipt survived profile + partition + app rename')
  } finally { rmSync(work, { recursive: true, force: true }) }
} else {
  const { app, BrowserWindow, crashReporter, session } = require('electron')
  const [phase, work] = process.argv.slice(-2)
  app.commandLine.appendSwitch('password-store', 'basic')
  app.commandLine.appendSwitch('disable-gpu')
  const { prepareDesktopStorage } = require(join(work, 'migration.cjs'))
  if (phase === 'verify' && process.platform === 'linux') {
    assert.equal(app.getPath('userData'), join(work, 'PlanGo'))
    // Use Electron's own crash service to reproduce the bootstrap left by a previous failed launch.
    crashReporter.start({ uploadToServer: false })
    assert(existsSync(join(work, 'PlanGo/Crashpad')), 'Exercise Electron-created productName bootstrap before entrypoint migration')
  }
  const directory = phase === 'seed' ? join(work, 'yoyu') : prepareDesktopStorage(work, process.platform === 'linux' ? app.getPath('userData') : join(work, 'plango'))
  app.setName(phase === 'seed' ? 'yoyu' : 'PlanGo')
  app.setPath('userData', directory)
  app.setPath('sessionData', directory)
  app.whenReady().then(async () => {
    const browser = session.fromPartition(phase === 'seed' ? 'persist:xiaonian' : 'persist:plango')
    const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } })
    await window.loadFile(resolve(work, 'fixture.html'))
    if (phase === 'seed') {
      await browser.cookies.set({ url: 'https://fixture.invalid', name: 'login', value: 'same-login-token', expirationDate: Math.floor(Date.now() / 1000) + 3600 })
      await window.webContents.executeJavaScript("localStorage.setItem('xy_active_run','same-active-run');localStorage.setItem('xy_sessions','[{\"runId\":\"same-active-run\"}]')")
    } else {
      const cookies = await browser.cookies.get({ url: 'https://fixture.invalid' })
      assert.equal(cookies.find(cookie => cookie.name === 'login')?.value, 'same-login-token')
      await window.webContents.executeJavaScript(readFileSync(join(work, 'renderer.js'), 'utf8') + ';Migration.migrateLocalStorage(localStorage)')
      assert.deepEqual(await window.webContents.executeJavaScript("[localStorage.getItem('plango_active_run'),localStorage.getItem('xy_active_run'),localStorage.getItem('plango_sessions')]"), ['same-active-run', null, '[{"runId":"same-active-run"}]'])
      assert.equal(JSON.parse(readFileSync(join(directory, 'harness/desktop-identity.json'))).browserSessionId, 'unchanged-session')
      assert.equal(JSON.parse(readFileSync(join(directory, 'harness/browser-receipts.json')))[0][1].result.outcome, 'unknown')
    }
    await browser.cookies.flushStore()
    await browser.flushStorageData()
    await session.defaultSession.flushStorageData()
    console.log(`${phase} completed`)
    app.exit(0)
  }).catch(error => { console.error(error); app.exit(1) })
}
