// Run: env -u ELECTRON_RUN_AS_NODE xvfb-run -a node_modules/.bin/electron --no-sandbox scripts/browser-regression.cjs
// A real Chromium DOM fixture, with only the Electron webview transport and UI store replaced.
const { app, BrowserWindow, ipcMain, webContents } = require('electron')
const { createServer } = require('node:http')
const { readFileSync } = require('node:fs')
const { resolve } = require('node:path')
const { build } = require('esbuild')

app.commandLine.appendSwitch('host-resolver-rules', 'MAP fixture.meituan.com 127.0.0.1')
app.commandLine.appendSwitch('no-proxy-server')
app.commandLine.appendSwitch('disable-gpu')

const fixture = '<!doctype html><html><body><h1>真实菜单测试页</h1><table><tr><th>菜品</th><th>价格</th></tr><tr><td>双人套餐</td><td>128 元</td></tr></table><button id="submit" onclick="window.submits=(window.submits||0)+1">预约</button><input id="search" placeholder="搜索"><p id="result"></p></body></html>'
const server = createServer((req, res) => {
  if (req.url === '/redirect') { res.writeHead(302, { Location: '/redirected' }); res.end(); return }
  res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(fixture)
})
let window

async function main() {
  await app.whenReady()
  await new Promise((done) => server.listen(0, '127.0.0.1', done))
  const url = `http://fixture.meituan.com:${server.address().port}/`
  ipcMain.handle('fixture:browser-eval', async (event, id, code) => {
    const target = webContents.fromId(id)
    if (event.sender !== window.webContents || target?.getType() !== 'webview' || target.hostWebContents !== window.webContents) throw new Error('invalid fixture target')
    return target.executeJavaScriptInIsolatedWorld(1001, [{ code }])
  })
  const script = `
import { registerWebview, setActiveWebview, setTabOpener, cancelRendererBrowserRun, releaseRendererBrowserRun, activateRendererBrowserRun, executeRendererBrowserCommand as execute } from './src/renderer/src/lib/browserBridge'
import { validateBrowserCommand, browserCommandGuard, allowedBrowserSite } from './src/shared/browser'
globalThis.__browserTestResult = (async () => {
  let checks = 0
  const assert = (ok, label) => { if (!ok) throw new Error(label); checks++ }
  const st = { tabs: [], activeTabId: null, aiBrowsing: {}, setView() {}, setAiBrowsing() {}, setActiveTab(id) { this.activeTabId = id } }
  globalThis.__browserTestStore = st
  window.plango = { browserEval: (id, code) => require('electron').ipcRenderer.invoke('fixture:browser-eval', id, code) }
  const makeTab = async (id) => {
    const frame = document.createElement('webview'); frame.style.cssText = 'width:700px;height:500px';
    frame.partition = 'fixture-browser';
    frame.src = ${JSON.stringify(url)} + id
    st.tabs.push({ id })
    const ready = new Promise(resolve => frame.addEventListener('dom-ready', resolve, { once: true })); document.body.appendChild(frame); await ready
    const wv = {
      getWebContentsId: () => frame.getWebContentsId(),
      loadURL: url => frame.loadURL(url),
      getURL: () => frame.getURL(), getTitle: () => frame.getTitle(), isLoading: () => frame.isLoading()
    }
    registerWebview(id, wv); return { wv, frame, page: code => frame.executeJavaScript(code) }
  }
  const a = await makeTab('tab-a'), b = await makeTab('tab-b')
  setActiveWebview(a.wv, 'tab-a')
  let seq = 0
  const command = (operation, args = {}, extra = {}) => ({ command_id: 'test-' + ++seq, run_id: 'run-a', browser_session_id: 'session-a', operation, arguments: args, ...extra })
  assert(!allowedBrowserSite('https://meituan.com.evil.test/'), 'hostname suffix bypass')
  assert(!allowedBrowserSite('https://evil.test/?q=meituan.com'), 'URL substring bypass')
  assert(allowedBrowserSite('https://www.meituan.com/'), 'legitimate host')
  const normalized = validateBrowserCommand(command('extract', {}, { tab_id: null, expected_snapshot_id: null, approved_action_id: null, expires_at: '2030-01-01T00:00:00.123456+00:00' }))
  assert(normalized.tab_id === undefined, 'Python nullable command fields accepted')
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:password@meituan.com/']) {
    let failed = false; try { validateBrowserCommand(command('navigate', { url })) } catch { failed = true }
    assert(failed, 'unsafe URL blocked')
  }
  let badIdx = false; try { validateBrowserCommand(command('click', { idx: '0;alert(1)' })) } catch { badIdx = true }
  assert(badIdx, 'index injection blocked')
  const injectedCode = await execute(command('snapshot', { code: 'window.injected = true' }))
  assert(injectedCode.error_kind === 'invalid_command' && !(await a.page('window.injected')), 'model-supplied JavaScript is rejected')
  const page = await execute(command('snapshot'))
  assert(page.ok && page.tab_id === 'tab-a' && page.snapshot_id && page.elements.length === 2, 'snapshot creates stable refs: ' + JSON.stringify(page))
  assert(page.text.includes('128 元'), 'real page text extracted')
  const pin = { tab_id: page.tab_id, expected_snapshot_id: page.snapshot_id }
  const unapproved = await execute(command('click', { idx: 0 }, pin))
  assert(unapproved.error_kind === 'approval_required' && !(await a.page('window.submits')), 'missing hint cannot bypass approval')
  const invalidHint = await execute(command('click', { idx: 0, hint: '' }, { ...pin, approved_action_id: 'approved' }))
  assert(invalidHint.error_kind === 'invalid_command', 'model hint is not accepted as authority')
  setActiveWebview(b.wv, 'tab-b')
  const readPinned = await execute(command('extract_tables', {}, { tab_id: 'tab-a' }))
  assert(readPinned.ok && readPinned.tab_id === 'tab-a' && readPinned.tables[0].rows[0][1] === '128 元', 'tab switching preserves command target')
  assert(await a.page("typeof window.__plangoSnapshot === 'undefined' && typeof require === 'undefined'"), 'remote page cannot access isolated snapshot or Node')
  const conflict = await execute(command('snapshot', {}, { run_id: 'run-b', tab_id: 'tab-a' }))
  assert(conflict.error_kind === 'tab_session_mismatch', 'other run cannot steal tab')
  await a.page("window.__plangoSnapshot={dirty:false,refs:[document.querySelector('#search')],observer:{disconnect(){},takeRecords(){return []}}};window.__plangoSnapshot.observer.disconnect();document.querySelector('#submit').textContent = '支付';window.__plangoSnapshot.dirty=false")
  await Promise.resolve()
  const stale = await execute(command('click', { idx: 0 }, { ...pin, expected_snapshot_id: readPinned.snapshot_id, approved_action_id: 'approved' }))
  assert(stale.error_kind === 'stale_snapshot' && !(await a.page('window.submits')), 'remote snapshot forgery cannot hide DOM mutation')
  const fresh = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  const clicked = await execute(command('click', { idx: 0 }, { tab_id: 'tab-a', expected_snapshot_id: fresh.snapshot_id, approved_action_id: 'approved' }))
  assert(clicked.ok && clicked.outcome === 'executed' && (await a.page('window.submits')) === 1 && !clicked.receipt, 'real click has no invented receipt')
  const repeat = await execute(command('click', { idx: 0 }, { tab_id: 'tab-a', expected_snapshot_id: fresh.snapshot_id, approved_action_id: 'approved' }))
  assert(repeat.error_kind === 'stale_snapshot' && (await a.page('window.submits')) === 1, 'snapshot cannot repeat a write')
  const expired = command('snapshot', {}, { expires_at: '2000-01-01T00:00:00Z' })
  assert(browserCommandGuard(expired) === 'command_expired', 'expired command blocked')
  const inputPage = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  await a.page("document.querySelector('#search').value = '人工修改'; document.querySelector('#search').dispatchEvent(new Event('input', { bubbles: true }))")
  const manual = await execute(command('click', { idx: 0 }, { tab_id: 'tab-a', expected_snapshot_id: inputPage.snapshot_id, approved_action_id: 'approved' }))
  assert(manual.error_kind === 'stale_snapshot', 'manual form edits invalidate snapshot')
  const silentPage = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  await a.page("document.querySelector('#search').value = '静默篡改'")
  const silentClick = await execute(command('click', { idx: 0 }, { tab_id: 'tab-a', expected_snapshot_id: silentPage.snapshot_id, approved_action_id: 'approved' }))
  assert(silentClick.error_kind === 'stale_snapshot' && (await a.page('window.submits')) === 1, 'silent input.value change blocks click')
  const silentType = await execute(command('type', { idx: 1, text: '不得覆盖' }, { tab_id: 'tab-a', expected_snapshot_id: silentPage.snapshot_id, approved_action_id: 'approved' }))
  assert(silentType.error_kind === 'stale_snapshot' && (await a.page("document.querySelector('#search').value")) === '静默篡改', 'silent input.value change blocks type')
  const typePage = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  const typed = await execute(command('type', { idx: 1, text: '双人套餐' }, { tab_id: 'tab-a', expected_snapshot_id: typePage.snapshot_id, approved_action_id: 'approved' }))
  assert(typed.ok && (await a.page("document.querySelector('#search').value")) === '双人套餐', 'approved type uses actual DOM setter')
  await a.page("document.querySelector('#search').oninput = function(){window.inputAttempts=(window.inputAttempts||0)+1;this.value='受控旧值'};true")
  const controlledPage = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  const controlled = await execute(command('type', { idx: 1, text: '新的输入' }, { tab_id: 'tab-a', expected_snapshot_id: controlledPage.snapshot_id, approved_action_id: 'approved' }))
  assert(!controlled.ok && controlled.outcome === 'unknown' && controlled.error_kind === 'input_not_applied' && (await a.page('window.inputAttempts')) === 1, 'restored controlled value is unknown, not acknowledged or retried')
  const repeatedType = await execute(command('type', { idx: 1, text: '新的输入' }, { tab_id: 'tab-a', expected_snapshot_id: controlledPage.snapshot_id, approved_action_id: 'approved' }))
  assert(repeatedType.error_kind === 'stale_snapshot' && (await a.page('window.inputAttempts')) === 1, 'failed type consumes snapshot and cannot repeat')
  await a.page("document.querySelector('#search').oninput=null;var select=document.createElement('select');select.id='choice';select.innerHTML='<option value=one>一</option><option value=two>二</option>';document.body.appendChild(select);true")
  const selectPage = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  const invalidSelect = await execute(command('type', { idx: 2, text: 'absent' }, { tab_id: 'tab-a', expected_snapshot_id: selectPage.snapshot_id, approved_action_id: 'approved' }))
  assert(!invalidSelect.ok && invalidSelect.outcome === 'blocked' && invalidSelect.error_kind === 'invalid_option' && (await a.page("document.querySelector('#choice').value")) === 'one', 'missing select option blocks before mutation')
  setTabOpener(() => { void makeTab('tab-c'); return 'tab-c' })
  const navigated = await execute(command('navigate', { url: ${JSON.stringify(url + 'new')} }, { run_id: 'run-c' }))
  assert(navigated.ok && navigated.tab_id === 'tab-c' && navigated.url.endsWith('/new'), 'first navigation allocates stable tab')
  const multiTab = await execute(command('snapshot', {}, { tab_id: 'tab-b' }))
  const originalTab = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  assert(multiTab.ok && originalTab.ok && originalTab.tab_id === 'tab-a', 'one run can explicitly address both owned tabs')
  registerWebview('tab-a', null); st.tabs = st.tabs.filter(t => t.id !== 'tab-a')
  const closed = await execute(command('snapshot', {}, { tab_id: 'tab-a' }))
  assert(closed.error_kind === 'tab_closed', 'closed bound tab does not switch to another tab')
  cancelRendererBrowserRun('run-c')
  const cancelled = await execute(command('snapshot', {}, { run_id: 'run-c', tab_id: 'tab-c' }))
  assert(cancelled.error_kind === 'run_cancelled', 'takeover stops later commands')
  const prior = await execute(command('snapshot', {}, { tab_id: 'tab-b' }))
  releaseRendererBrowserRun('run-a')
  setActiveWebview(b.wv, 'tab-b')
  const oldWrite = await execute(command('click', { idx: 0 }, { tab_id: 'tab-b', expected_snapshot_id: prior.snapshot_id, approved_action_id: 'old-approved' }))
  assert(oldWrite.error_kind === 'run_cancelled' && !(await b.page('window.submits')), 'terminal release blocks queued old writes')
  const reusedSnapshot = await execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: 'tab-b', expected_snapshot_id: prior.snapshot_id, approved_action_id: 'new-approved' }))
  assert(reusedSnapshot.error_kind === 'stale_snapshot' && !(await b.page('window.submits')), 'new run cannot reuse prior run snapshot')
  const reusedTab = await execute(command('extract', {}, { run_id: 'run-new' }))
  assert(reusedTab.ok && reusedTab.tab_id === 'tab-b', 'finished run releases current tab for new conversation')
  b.wv.isLoading = () => true
  const delayedWrite = execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: 'tab-b', expected_snapshot_id: reusedTab.snapshot_id, approved_action_id: 'pending-approved' }))
  releaseRendererBrowserRun('run-new')
  activateRendererBrowserRun('run-new')
  b.wv.isLoading = () => false
  const delayedResult = await delayedWrite
  assert(delayedResult.error_kind === 'run_superseded' && !(await b.page('window.submits')), 'reactivation cannot revive an old pending write')
  const lateWrite = await execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: 'tab-b', expected_snapshot_id: reusedTab.snapshot_id, approved_action_id: 'late-approved' }))
  assert(lateWrite.error_kind === 'stale_snapshot' && !(await b.page('window.submits')), 'late old snapshot rejected after same-run new turn')
  const queuedRead = await execute(command('extract', {}, { run_id: 'run-new', tab_id: 'tab-b' }), 0)
  assert(queuedRead.error_kind === 'run_superseded', 'old queue epoch cannot produce a fresh snapshot')
  const sameRunPage = await execute(command('extract', {}, { run_id: 'run-new', tab_id: 'tab-b' }))
  assert(sameRunPage.ok && sameRunPage.snapshot_id, 'same run can read again in its next turn')
  activateRendererBrowserRun('run-new')
  const nextTurnWrite = await execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: 'tab-b', expected_snapshot_id: sameRunPage.snapshot_id, approved_action_id: 'next-turn-approved' }))
  assert(nextTurnWrite.ok && (await b.page('window.submits')) === 1, 'ordinary approval activation retains current-turn snapshot')
  const nav = await makeTab('tab-navigation')
  setActiveWebview(nav.wv, 'tab-navigation')
  await nav.page("var a=document.createElement('a');a.id='navigation';a.href=location.origin+'/next';a.textContent='下一页';a.onclick=function(){localStorage.setItem('unwanted-click','yes')};document.body.appendChild(a);true")
  const navigationPage = await execute(command('snapshot', {}, { run_id: 'navigation-run' }))
  const link = navigationPage.elements.find(el => el.text === '下一页')
  assert(link.href === ${JSON.stringify(url)} + 'next', 'snapshot exposes the actual ordinary anchor URL')
  const navPin = { run_id: 'navigation-run', tab_id: 'tab-navigation', expected_snapshot_id: navigationPage.snapshot_id }
  assert((await execute(command('click', { idx: link.idx }, navPin))).error_kind === 'approval_required', 'native anchor navigation still requires approval')
  await nav.page("document.querySelector('#navigation').href=location.origin+'/changed';true")
  assert((await execute(command('click', { idx: link.idx }, { ...navPin, approved_action_id: 'navigation-approved' }))).error_kind === 'stale_snapshot', 'changed href invalidates approval snapshot')
  await nav.page("document.querySelector('#navigation').href=location.origin+'/next';true")
  await nav.page("var cover=document.createElement('div');cover.id='cover';cover.style.cssText='position:fixed;inset:0;z-index:999999;background:white';document.body.appendChild(cover);true")
  const coveredNav = await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: 'tab-navigation' }))
  assert((await execute(command('click', { idx: link.idx }, { ...navPin, expected_snapshot_id: coveredNav.snapshot_id, approved_action_id: 'navigation-approved' }))).error_kind === 'element_obscured', 'an obscured anchor cannot navigate')
  await nav.page("document.querySelector('#cover').remove();true")
  const currentNav = await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: 'tab-navigation' }))
  const anchorNavigation = await execute(command('click', { idx: link.idx }, { ...navPin, expected_snapshot_id: currentNav.snapshot_id, approved_action_id: 'navigation-approved' }))
  assert(anchorNavigation.ok && anchorNavigation.interaction_kind === 'navigation' && anchorNavigation.url === link.href, 'bound anchor navigates in the same visible browser tab')
  assert(await nav.page("localStorage.getItem('unwanted-click')===null"), 'native navigation does not dispatch an untrusted page click handler')
  assert((await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: 'tab-navigation' }))).ok, 'the same run continues observing after a navigation click')
  await nav.page("var redirect=document.createElement('a');redirect.href=location.origin+'/redirect';redirect.textContent='跳转测试';document.body.appendChild(redirect);true")
  const beforeRedirect = await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: 'tab-navigation' }))
  const redirectLink = beforeRedirect.elements.find(el => el.text === '跳转测试')
  const redirected = await execute(command('click', { idx: redirectLink.idx }, { ...navPin, expected_snapshot_id: beforeRedirect.snapshot_id, approved_action_id: 'redirect-approved' }))
  assert(redirected.outcome === 'unknown' && redirected.error_kind === 'navigation_redirected' && !redirected.interaction_kind, 'redirected navigation is UNKNOWN and cannot masquerade as the approved target')
  return { checks }
})()
`
  const built = await build({
    stdin: { contents: script, resolveDir: process.cwd(), loader: 'ts' },
    bundle: true, format: 'iife', platform: 'browser', write: false, tsconfig: 'tsconfig.web.json', external: ['electron'],
    plugins: [{ name: 'browser-test-dependencies', setup(build) {
      build.onResolve({ filter: /\/store$/ }, () => ({ path: 'test-store', namespace: 'test' }))
      build.onLoad({ filter: /.*/, namespace: 'test' }, () => ({ contents: 'export const useStore = { getState: () => globalThis.__browserTestStore }', loader: 'js' }))
      build.onResolve({ filter: /Readability\.js\?raw$/ }, () => ({ path: resolve('node_modules/@mozilla/readability/Readability.js'), namespace: 'raw' }))
      build.onLoad({ filter: /.*/, namespace: 'raw' }, args => ({ contents: readFileSync(args.path, 'utf8'), loader: 'text' }))
    } }]
  })
  const code = built.outputFiles[0].text
  // Only this controlled fixture host has Node enabled to install its IPC stub; guest webviews do not.
  window = new BrowserWindow({ show: false, webPreferences: { sandbox: false, contextIsolation: false, nodeIntegration: true, webviewTag: true } })
  await window.loadURL(url)
  await window.webContents.executeJavaScript(code)
  const result = await window.webContents.executeJavaScript('globalThis.__browserTestResult')
  console.log(`Browser regression: ${result.checks} assertions passed (real Chromium fixture)`)
}

main().then(() => { server.close(); app.exit(0) }).catch(error => { console.error(error); server.close(); app.exit(1) })
