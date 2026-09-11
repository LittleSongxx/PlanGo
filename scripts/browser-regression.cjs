// Real WebContentsView + trusted main bridge/Playwright regression; isolated loopback fixtures only.
const { app, BrowserWindow } = require('electron')
const { createServer } = require('node:http')
const { readFileSync, mkdtempSync, rmSync } = require('node:fs')
const { resolve, join } = require('node:path')
const { tmpdir } = require('node:os')
const { build } = require('esbuild')
const work = mkdtempSync(join(tmpdir(), 'plango-browser-regression-'))
app.setPath('userData', work)
app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
app.commandLine.appendSwitch('remote-debugging-port', '0')
app.commandLine.appendSwitch('host-resolver-rules', 'MAP fixture.meituan.com 127.0.0.1, MAP frame.meituan.com 127.0.0.1, MAP account.dianping.com 127.0.0.1, MAP verify.meituan.com 127.0.0.1')
app.commandLine.appendSwitch('no-proxy-server')
app.commandLine.appendSwitch('disable-gpu')
const fixture = '<!doctype html><html><body><h1>真实菜单测试页</h1><table><tr><th>菜品</th><th>价格</th></tr><tr><td>双人套餐</td><td>128 元</td></tr></table><button id="submit" onclick="window.submits=(window.submits||0)+1">预约</button><input id="search" placeholder="搜索"><p id="result"></p></body></html>'
let slowRequests = 0
const server = createServer((req,res) => {
  if (req.url === '/v2/app/general_page') { res.setHeader('Content-Type','text/html; charset=utf-8'); res.end('<title>验证中心</title><p>请在当前浏览器中完成安全验证</p>'); return }
  if (req.url === '/merchant-preview') { res.setHeader('Content-Type','text/html; charset=utf-8'); res.end('<header><h1>受控短页餐厅</h1><p>地址：重庆市受控地址1号</p></header><section><h2>精选双人餐</h2><p>周一至周日 随时退 ¥98</p></section><article><h2>推荐菜</h2><p>'+'受控推荐菜名与介绍。'.repeat(30)+'</p></article>'); return }
  if (req.url === '/pclogin') { res.setHeader('Content-Type','text/html; charset=utf-8'); res.end('<title>大众点评网</title><h1>登录</h1><p>APP扫码，享七天免登录</p><p>打开大众点评APP</p><p>扫描二维码登录</p>'); return }
  if (req.url === '/forms-frame') { res.setHeader('Content-Type','text/html; charset=utf-8'); res.end('<form id="booking" action="/frame-book"><h2>框架门店</h2><label>人数<input name="party" value="2"></label><button type="submit">框架提交</button></form>'); return }
  if (req.url === '/forms') {
    res.setHeader('Content-Type','text/html; charset=utf-8')
    res.end(`<h1>OUTSIDE_OTHER_SHOP</h1><form id="booking" action="/book"><h2>重庆溪畔餐厅</h2><p>重庆市渝中区受控地址</p><fieldset><label>人数<input name="party" value="3"></label></fieldset><label for="day">到店日期</label><input id="day" name="date" type="date" value="2026-09-12"><input aria-label="到店时间" name="time" type="time" value="18:30"><label>同意<input name="accepted" type="checkbox" checked></label><select name="choice" multiple><option value="a" selected>甲</option><option value="b" selected>乙</option></select><input type="password" value="SECRET_PASSWORD"><input type="file"><input type="hidden" value="SECRET_HIDDEN"><div style="display:none"><input value="SECRET_DISPLAY"></div><div style="opacity:0"><input value="SECRET_OPACITY"><span>HIDDEN_OTHER_SHOP</span></div><button type="submit">确认预约</button><button type="button">返回</button><button type="submit" formaction="/wrong-entry">另一个入口</button></form><label for="contact">联系人</label><input id="contact" form="booking" name="contact" value="受控姓名"><form action="/other"><h2>另一门店</h2><input name="other" value="other-value"></form><iframe src="http://frame.meituan.com:${server.address().port}/forms-frame" style="width:500px;height:180px"></iframe>`)
    return
  }
  if (req.url === '/slow') { slowRequests++; setTimeout(() => { res.setHeader('Content-Type','text/html'); res.end(fixture) },1800); return }
  if (req.url === '/frame') { res.setHeader('Content-Type','text/html; charset=utf-8'); res.end('<input placeholder="框架输入"><script>parent.postMessage("frame-ready","*")</script>'); return }
  if (req.url === '/redirect') { res.writeHead(302, { Location: '/redirected' }); res.end(); return }
  res.setHeader('Content-Type','text/html; charset=utf-8'); res.end(fixture)
})
let host, api, hideNextFill = false
// Simulate a real layout change after the driver's final check, exactly as the public
// Playwright fill starts; this wrapper delegates all actual input to Playwright.
const { chromium } = require('playwright-core')
const connect = chromium.connectOverCDP.bind(chromium)
chromium.connectOverCDP = async (...args) => {
  const browser = await connect(...args), wrapped = new WeakSet()
  const frame = value => {
    if (wrapped.has(value)) return; wrapped.add(value)
    const locate = value.locator.bind(value)
    value.locator = (...args) => {
      const locator = locate(...args), fill = locator.fill.bind(locator)
      locator.fill = (...args) => { if (hideNextFill) { hideNextFill=false; api.setBrowserLayout({x:0,y:0,width:850,height:580,visible:false}) } return fill(...args) }
      return locator
    }
  }
  const page = value => { value.frames().forEach(frame); value.on('frameattached',frame) }
  for (const context of browser.contexts()) { context.pages().forEach(page); context.on('page',page) }
  return browser
}
const watchdog = setTimeout(() => { console.error('Browser regression watchdog expired'); app.exit(1) }, 120000)
async function main() {
  await new Promise(done => server.listen(0, '127.0.0.1', done))
  const url = `http://fixture.meituan.com:${server.address().port}/`
  const outfile = join(work, 'bridge.cjs')
  await build({ stdin: { contents: "export * from './src/main/browser-bridge'; export * from './src/main/browserView'; export * from './src/main/browserDriver'; export * from './src/shared/browser'", resolveDir: process.cwd(), loader:'ts' },
    bundle:true,format:'cjs',platform:'node',outfile,external:['electron','playwright-core'],tsconfig:'tsconfig.node.json',
    plugins:[{name:'raw',setup(build) {
      build.onResolve({filter:/Readability\.js\?raw$/},()=>({path:resolve('node_modules/@mozilla/readability/Readability.js'),namespace:'raw'}))
      build.onLoad({filter:/.*/,namespace:'raw'},args=>({contents:readFileSync(args.path,'utf8'),loader:'text'}))
    }}] })
  const Module = require('node:module')
  const loaded = new Module(outfile, module); loaded.filename = outfile; loaded.paths = Module._nodeModulePaths(process.cwd())
  loaded._compile(readFileSync(outfile,'utf8'),outfile); api = loaded.exports
  const { executeBrowserCommand: execute, acknowledgeBrowserCommand, validateBrowserCommand, browserCommandGuard, allowedBrowserSite, createBrowserTab, activateBrowserTab, handleBrowserIntent, releaseBrowserRun, activateBrowserRun, cancelBrowserRun } = api
  await app.whenReady()
  host = new BrowserWindow({show:true,width:900,height:650,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}})
  await host.loadURL('data:text/html,<html><body>PlanGo controlled browser host</body></html>')
  api.initializeBrowserViews(host)
  api.setBrowserLayout({x:0,y:0,width:850,height:580,visible:true})
  let checks = 0
  const assert = (ok, label) => { if (!ok) throw new Error(label); checks++ }
  const makeTab = async (label) => {
    const tab = await createBrowserTab(url + label)
    return { id: tab.id, wc: tab.contents, page: code => tab.contents.executeJavaScript(code) }
  }
  const a = await makeTab('a'), b = await makeTab('b')
  activateBrowserTab(a.id)
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
  assert(page.ok && page.tab_id === a.id && page.snapshot_id && page.elements.length === 2, 'snapshot creates stable refs: ' + JSON.stringify(page))
  assert(page.text.includes('128 元'), 'real page text extracted')
  assert(page.tables?.[0]?.rows?.[0]?.[1] === '128 元', 'snapshot keeps DOM tables for listing assembly')
  const pin = { tab_id: page.tab_id, expected_snapshot_id: page.snapshot_id }
  const unapproved = await execute(command('click', { idx: 0 }, pin))
  assert(unapproved.error_kind === 'approval_required' && !(await a.page('window.submits')), 'missing hint cannot bypass approval')
  const invalidHint = await execute(command('click', { idx: 0, hint: '' }, { ...pin, approved_action_id: 'approved' }))
  assert(invalidHint.error_kind === 'invalid_command', 'model hint is not accepted as authority')
  activateBrowserTab(b.id)
  const readPinned = await execute(command('extract_tables', {}, { tab_id: a.id }))
  assert(readPinned.ok && readPinned.tab_id === a.id && readPinned.tables[0].rows[0][1] === '128 元', 'tab switching preserves command target')
  assert(await a.page("typeof window.__plangoSnapshot === 'undefined' && typeof require === 'undefined'"), 'remote page cannot access isolated snapshot or Node')
  const conflict = await execute(command('snapshot', {}, { run_id: 'run-b', tab_id: a.id }))
  assert(conflict.error_kind === 'tab_session_mismatch', 'other run cannot steal tab')
  await a.page("window.__plangoSnapshot={dirty:false,refs:[document.querySelector('#search')],observer:{disconnect(){},takeRecords(){return []}}};window.__plangoSnapshot.observer.disconnect();document.querySelector('#submit').textContent = '支付';window.__plangoSnapshot.dirty=false")
  await Promise.resolve()
  const stale = await execute(command('click', { idx: 0 }, { ...pin, expected_snapshot_id: readPinned.snapshot_id, approved_action_id: 'approved' }))
  assert(stale.error_kind === 'stale_snapshot' && !(await a.page('window.submits')), 'remote snapshot forgery cannot hide DOM mutation')
  const fresh = await execute(command('snapshot', {}, { tab_id: a.id }))
  const approvedClick = command('click', { idx: 0 }, { tab_id: a.id, expected_snapshot_id: fresh.snapshot_id, approved_action_id: 'approved' })
  const clicked = await execute(approvedClick)
  assert(clicked.ok && clicked.outcome === 'executed' && (await a.page('window.submits')) === 1 && !clicked.receipt, 'real click has no invented receipt')
  assert(JSON.stringify(await execute(approvedClick)) === JSON.stringify(clicked) && (await a.page('window.submits')) === 1, 'same durable command replays its receipt, not its side effect')
  assert((await execute({ ...approvedClick, arguments: { idx: 1 } })).error_kind === 'command_conflict', 'same command ID cannot change parameters')
  acknowledgeBrowserCommand({ ...approvedClick, arguments: { idx: 1 } })
  assert(JSON.stringify(await execute(approvedClick)) === JSON.stringify(clicked), 'a mismatched acknowledgement cannot discard the original receipt')
  acknowledgeBrowserCommand(approvedClick)
  assert((await execute(approvedClick)).error_kind === 'command_already_delivered' && (await a.page('window.submits')) === 1, 'acknowledged command drops its result but retains a non-replayable identity')
  assert((await execute({ ...approvedClick, arguments: { idx: 1 } })).error_kind === 'command_conflict', 'compacted identity still rejects changed parameters')
  const repeat = await execute(command('click', { idx: 0 }, { tab_id: a.id, expected_snapshot_id: fresh.snapshot_id, approved_action_id: 'approved' }))
  assert(repeat.error_kind === 'stale_snapshot' && (await a.page('window.submits')) === 1, 'snapshot cannot repeat a write')
  const expired = command('snapshot', {}, { expires_at: '2000-01-01T00:00:00Z' })
  assert(browserCommandGuard(expired) === 'command_expired', 'expired command blocked')
  const inputPage = await execute(command('snapshot', {}, { tab_id: a.id }))
  await a.page("document.querySelector('#search').value = '人工修改'; document.querySelector('#search').dispatchEvent(new Event('input', { bubbles: true }))")
  const manual = await execute(command('click', { idx: 0 }, { tab_id: a.id, expected_snapshot_id: inputPage.snapshot_id, approved_action_id: 'approved' }))
  assert(manual.error_kind === 'stale_snapshot', 'manual form edits invalidate snapshot')
  const silentPage = await execute(command('snapshot', {}, { tab_id: a.id }))
  await a.page("document.querySelector('#search').value = '静默篡改'")
  const silentClick = await execute(command('click', { idx: 0 }, { tab_id: a.id, expected_snapshot_id: silentPage.snapshot_id, approved_action_id: 'approved' }))
  assert(silentClick.error_kind === 'stale_snapshot' && (await a.page('window.submits')) === 1, 'silent input.value change blocks click')
  const silentType = await execute(command('type', { idx: 1, text: '不得覆盖' }, { tab_id: a.id, expected_snapshot_id: silentPage.snapshot_id, approved_action_id: 'approved' }))
  assert(silentType.error_kind === 'stale_snapshot' && (await a.page("document.querySelector('#search').value")) === '静默篡改', 'silent input.value change blocks type')
  const typePage = await execute(command('snapshot', {}, { tab_id: a.id }))
  const typed = await execute(command('type', { idx: 1, text: '双人套餐' }, { tab_id: a.id, expected_snapshot_id: typePage.snapshot_id, approved_action_id: 'approved' }))
  assert(typed.ok && (await a.page("document.querySelector('#search').value")) === '双人套餐', 'approved type uses actual DOM setter')
  await a.page("document.querySelector('#search').oninput = function(){window.inputAttempts=(window.inputAttempts||0)+1;this.value='受控旧值'};true")
  const controlledPage = await execute(command('snapshot', {}, { tab_id: a.id }))
  const controlled = await execute(command('type', { idx: 1, text: '新的输入' }, { tab_id: a.id, expected_snapshot_id: controlledPage.snapshot_id, approved_action_id: 'approved' }))
  assert(!controlled.ok && controlled.outcome === 'unknown' && controlled.error_kind === 'input_not_applied' && (await a.page('window.inputAttempts')) === 1, 'restored controlled value is unknown, not acknowledged or retried')
  const repeatedType = await execute(command('type', { idx: 1, text: '新的输入' }, { tab_id: a.id, expected_snapshot_id: controlledPage.snapshot_id, approved_action_id: 'approved' }))
  assert(repeatedType.error_kind === 'stale_snapshot' && (await a.page('window.inputAttempts')) === 1, 'failed type consumes snapshot and cannot repeat')
  await a.page("document.querySelector('#search').oninput=null;var select=document.createElement('select');select.id='choice';select.innerHTML='<option value=one>一</option><option value=two>二</option>';document.body.appendChild(select);true")
  const selectPage = await execute(command('snapshot', {}, { tab_id: a.id }))
  const invalidSelect = await execute(command('type', { idx: 2, text: 'absent' }, { tab_id: a.id, expected_snapshot_id: selectPage.snapshot_id, approved_action_id: 'approved' }))
  assert(!invalidSelect.ok && invalidSelect.outcome === 'blocked' && invalidSelect.error_kind === 'invalid_option' && (await a.page("document.querySelector('#choice').value")) === 'one', 'missing select option blocks before mutation')
  const navigated = await execute(command('navigate', { url: url + 'new' }, { run_id: 'run-c' }))
  assert(navigated.ok && navigated.tab_id !== a.id && navigated.tab_id !== b.id && navigated.url.endsWith('/new'), 'first navigation allocates stable tab')
  const multiTab = await execute(command('snapshot', {}, { tab_id: b.id }))
  const originalTab = await execute(command('snapshot', {}, { tab_id: a.id }))
  assert(multiTab.ok && originalTab.ok && originalTab.tab_id === a.id, 'one run can explicitly address both owned tabs')
  await handleBrowserIntent({ kind: 'close', id: a.id })
  const closed = await execute(command('snapshot', {}, { tab_id: a.id }))
  assert(closed.error_kind === 'tab_closed', 'closed bound tab does not switch to another tab')
  cancelBrowserRun('run-c')
  const cancelled = await execute(command('snapshot', {}, { run_id: 'run-c', tab_id: navigated.tab_id }))
  assert(cancelled.error_kind === 'run_cancelled', 'takeover stops later commands')
  const prior = await execute(command('snapshot', {}, { tab_id: b.id }))
  releaseBrowserRun('run-a')
  assert((await execute(approvedClick)).error_kind === 'command_already_delivered', 'terminal release cannot erase an acknowledged write identity')
  activateBrowserTab(b.id)
  const oldWrite = await execute(command('click', { idx: 0 }, { tab_id: b.id, expected_snapshot_id: prior.snapshot_id, approved_action_id: 'old-approved' }))
  assert(oldWrite.error_kind === 'run_cancelled' && !(await b.page('window.submits')), 'terminal release blocks queued old writes')
  const reusedSnapshot = await execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: b.id, expected_snapshot_id: prior.snapshot_id, approved_action_id: 'new-approved' }))
  assert(reusedSnapshot.error_kind === 'stale_snapshot' && !(await b.page('window.submits')), 'new run cannot reuse prior run snapshot: '+JSON.stringify(reusedSnapshot))
  const reusedTab = await execute(command('extract', {}, { run_id: 'run-new' }))
  assert(reusedTab.ok && reusedTab.tab_id === b.id, 'finished run releases current tab for new conversation')
  const delayedWrite = execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: b.id, expected_snapshot_id: reusedTab.snapshot_id, approved_action_id: 'pending-approved' }))
  releaseBrowserRun('run-new')
  activateBrowserRun('run-new')
  const delayedResult = await delayedWrite
  assert(delayedResult.error_kind === 'run_superseded' && !(await b.page('window.submits')), 'reactivation cannot revive an old pending write')
  const lateWrite = await execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: b.id, expected_snapshot_id: reusedTab.snapshot_id, approved_action_id: 'late-approved' }))
  assert(lateWrite.error_kind === 'stale_snapshot' && !(await b.page('window.submits')), 'late old snapshot rejected after same-run new turn')
  const queuedReadPromise = execute(command('extract', {}, { run_id: 'run-new', tab_id: b.id }))
  releaseBrowserRun('run-new'); activateBrowserRun('run-new')
  const queuedRead = await queuedReadPromise
  assert(queuedRead.error_kind === 'run_superseded', 'old queue epoch cannot produce a fresh snapshot')
  const sameRunPage = await execute(command('extract', {}, { run_id: 'run-new', tab_id: b.id }))
  assert(sameRunPage.ok && sameRunPage.snapshot_id, 'same run can read again in its next turn')
  activateBrowserRun('run-new')
  const nextTurnWrite = await execute(command('click', { idx: 0 }, { run_id: 'run-new', tab_id: b.id, expected_snapshot_id: sameRunPage.snapshot_id, approved_action_id: 'next-turn-approved' }))
  assert(nextTurnWrite.ok && (await b.page('window.submits')) === 1, 'ordinary approval activation retains current-turn snapshot')
  const nav = await makeTab('navigation')
  activateBrowserTab(nav.id)
  await nav.page("var a=document.createElement('a');a.id='navigation';a.href=location.origin+'/next';a.textContent='下一页';a.onclick=function(){localStorage.setItem('unwanted-click','yes')};document.body.appendChild(a);true")
  const navigationPage = await execute(command('snapshot', {}, { run_id: 'navigation-run' }))
  const link = navigationPage.elements.find(el => el.text === '下一页')
  assert(link.href === url + 'next', 'snapshot exposes the actual ordinary anchor URL')
  const navPin = { run_id: 'navigation-run', tab_id: nav.id, expected_snapshot_id: navigationPage.snapshot_id }
  assert((await execute(command('click', { idx: link.idx }, navPin))).error_kind === 'approval_required', 'native anchor navigation still requires approval')
  await nav.page("document.querySelector('#navigation').href=location.origin+'/changed';true")
  assert((await execute(command('click', { idx: link.idx }, { ...navPin, approved_action_id: 'navigation-approved' }))).error_kind === 'stale_snapshot', 'changed href invalidates approval snapshot')
  await nav.page("document.querySelector('#navigation').href=location.origin+'/next';true")
  await nav.page("var cover=document.createElement('div');cover.id='cover';cover.style.cssText='position:fixed;inset:0;z-index:999999;background:white';document.body.appendChild(cover);true")
  const coveredNav = await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: nav.id }))
  const obscured = await execute(command('click', { idx: link.idx }, { ...navPin, expected_snapshot_id: coveredNav.snapshot_id, approved_action_id: 'navigation-approved' }))
  assert(!obscured.ok && obscured.outcome === 'blocked' && ['element_obscured','browser_timeout'].includes(obscured.error_kind), 'an obscured anchor cannot navigate')
  await nav.page("document.querySelector('#cover').remove();true")
  const currentNav = await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: nav.id }))
  const anchorNavigation = await execute(command('click', { idx: link.idx }, { ...navPin, expected_snapshot_id: currentNav.snapshot_id, approved_action_id: 'navigation-approved' }))
  assert(anchorNavigation.ok && anchorNavigation.interaction_kind === 'navigation' && anchorNavigation.url === link.href, 'bound anchor navigates in the same visible browser tab')
  assert(await nav.page("localStorage.getItem('unwanted-click')===null"), 'native navigation does not dispatch an untrusted page click handler')
  assert((await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: nav.id }))).ok, 'the same run continues observing after a navigation click')
  await nav.page("var redirect=document.createElement('a');redirect.href=location.origin+'/redirect';redirect.textContent='跳转测试';document.body.appendChild(redirect);true")
  const beforeRedirect = await execute(command('snapshot', {}, { run_id: 'navigation-run', tab_id: nav.id }))
  const redirectLink = beforeRedirect.elements.find(el => el.text === '跳转测试')
  const redirected = await execute(command('click', { idx: redirectLink.idx }, { ...navPin, expected_snapshot_id: beforeRedirect.snapshot_id, approved_action_id: 'redirect-approved' }))
  assert(redirected.outcome === 'unknown' && redirected.error_kind === 'navigation_redirected' && !redirected.interaction_kind, 'redirected navigation is UNKNOWN and cannot masquerade as the approved target')

  releaseBrowserRun('navigation-run')
  const capabilitiesRun = 'capabilities-run'
  const cap = (operation, args = {}, extra = {}) => command(operation,args,{run_id:capabilitiesRun,tab_id:nav.id,...extra})
  const frameURL = url.replace('fixture.meituan.com','frame.meituan.com') + 'frame'
  await nav.page(`document.body.innerHTML='<div id="shadow"></div><iframe id="frame" style="width:700px;height:100px"></iframe><select id="chinese"><option value="重庆">重庆</option><option value="成都">成都</option></select>';
    document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<input placeholder="影子输入">';
    window.addEventListener('message',e=>{if(e.data==='frame-ready')window.frameReady=true});document.querySelector('#frame').src=${JSON.stringify(frameURL)};true`)
  const waitFor = async (condition) => { const until=Date.now()+5000;while(!(await condition())){if(Date.now()>until)throw Error('fixture timed out');await new Promise(r=>setTimeout(r,30))} }
  await waitFor(()=>nav.page('window.frameReady===true'))
  const capPage = await execute(cap('snapshot'))
  assert(capPage.ok && capPage.elements.some(x=>x.name==='影子输入') && capPage.elements.some(x=>x.name==='框架输入'), 'production driver observes open shadow and cross-origin iframe')
  const shadowInput = capPage.elements.find(x=>x.name==='影子输入')
  assert((await execute(cap('type',{idx:shadowInput.idx,text:'重庆'}, {expected_snapshot_id:capPage.snapshot_id,approved_action_id:'shadow-approved'}))).ok
    && await nav.page("document.querySelector('#shadow').shadowRoot.querySelector('input').value==='重庆'"), 'production Playwright fills original shadow input')
  const framePage = await execute(cap('snapshot'))
  const frameInput = framePage.elements.find(x=>x.name==='框架输入')
  assert((await execute(cap('type',{idx:frameInput.idx,text:'观音桥'}, {expected_snapshot_id:framePage.snapshot_id,approved_action_id:'frame-approved'}))).ok
    && await nav.wc.mainFrame.frames.find(f=>f.url===frameURL).executeJavaScript("document.querySelector('input').value==='观音桥'"), 'production Playwright fills original cross-origin frame')
  await nav.page("document.querySelector('#shadow').shadowRoot.querySelector('input').oninput=()=>window.hiddenInputs=(window.hiddenInputs||0)+1;true")
  const beforeHidden = await execute(cap('snapshot'))
  hideNextFill = true
  const hiddenType = await execute(cap('type',{idx:beforeHidden.elements.find(x=>x.name==='影子输入').idx,text:'必须不写入'}, {expected_snapshot_id:beforeHidden.snapshot_id,approved_action_id:'hidden-approved'}))
  assert(!hiddenType.ok && await nav.page("!window.hiddenInputs && document.querySelector('#shadow').shadowRoot.querySelector('input').value==='重庆'"), 'hiding a view as fill begins aborts before input or value mutation')
  api.setBrowserLayout({x:0,y:0,width:850,height:580,visible:true})
  await waitFor(()=>nav.page("document.visibilityState==='visible'"))
  assert((await execute(cap('snapshot'))).ok, 'showing a tab permits fresh reads without cancelling its entire run')
  const selectSnapshot = await execute(cap('snapshot'))
  const chineseSelect = selectSnapshot.elements.find(x=>x.tag==='select')
  assert((await execute(cap('type',{idx:chineseSelect.idx,text:'成都'}, {expected_snapshot_id:selectSnapshot.snapshot_id,approved_action_id:'select-approved'}))).ok
    && await nav.page("document.querySelector('#chinese').value==='成都'"), 'Chinese option values survive the isolated selector transport')
  const cloneSnapshot = await execute(cap('snapshot'))
  await nav.page("var el=document.querySelector('#chinese');el.replaceWith(el.cloneNode(true));true")
  assert((await execute(cap('type',{idx:chineseSelect.idx,text:'重庆'}, {expected_snapshot_id:cloneSnapshot.snapshot_id,approved_action_id:'clone-approved'}))).error_kind==='stale_snapshot', 'rerendered lookalike cannot inherit snapshot authority')
  await handleBrowserIntent({kind:'zoom',id:nav.id,factor:1.25})
  const zoomPage = await execute(cap('snapshot'))
  const shot = await execute(cap('screenshot',{}, {expected_snapshot_id:zoomPage.snapshot_id}))
  const png = shot.screenshot && Buffer.from(shot.screenshot.data_url.split(',')[1],'base64')
  assert(shot.ok && shot.page_version===zoomPage.page_version && shot.screenshot.snapshot_id===zoomPage.snapshot_id
    && png.readUInt32BE(16)===shot.screenshot.image.width && png.readUInt32BE(20)===shot.screenshot.image.height
    && Math.abs(shot.screenshot.image.width-shot.screenshot.clip.width*shot.screenshot.dpr)<=2, 'native screenshot pixels and version agree at non-default zoom')
  await nav.page("var p=document.createElement('button');p.id='popup';p.textContent='Open fixture popup';p.onclick=()=>window.open(location.origin+'/popup');document.body.appendChild(p);true")
  const popupSnapshot = await execute(cap('snapshot'))
  const popupButton = popupSnapshot.elements.find(x=>x.text==='Open fixture popup')
  await execute(cap('click',{idx:popupButton.idx},{expected_snapshot_id:popupSnapshot.snapshot_id,approved_action_id:'popup-approved'}))
  await waitFor(()=>api.getBrowserState().tabs.some(t=>t.popup))
  const popup = api.getBrowserState().tabs.find(t=>t.popup)
  assert(api.isOwnedBrowserContents(api.getBrowserTab(popup.id).id), 'native popup remains a visible manager-owned browser')
  assert((await execute(command('snapshot',{}, {run_id:'popup-thief',tab_id:popup.id}))).error_kind==='tab_session_mismatch', 'another run cannot steal an opener-owned popup')
  await waitFor(()=>!api.getBrowserTab(popup.id).isLoading())
  assert((await execute(command('snapshot',{}, {run_id:capabilitiesRun,tab_id:popup.id}))).ok, 'original run may observe its same-session popup for verification')
  await handleBrowserIntent({kind:'close',id:popup.id}); activateBrowserTab(nav.id)
  await nav.page(`document.body.innerHTML='<style>.high{display:none}@media(max-width:650px){.low{display:none}.high{display:inline}}</style><button id="price" onclick="window.chosen=this.innerText"><span class="low">套餐128</span><span class="high">套餐256</span></button>';true`)
  const beforeResize = await execute(cap('snapshot'))
  host.setSize(560,650)
  await waitFor(()=>nav.page("document.querySelector('#price').innerText.includes('256')"))
  assert((await execute(cap('click',{idx:0},{expected_snapshot_id:beforeResize.snapshot_id,approved_action_id:'resize-old'}))).error_kind==='stale_snapshot'
    && await nav.page('!window.chosen'), 'CSS responsive price changes invalidate an old approval on the same node')
  const afterResize = await execute(cap('snapshot'))
  assert((await execute(cap('click',{idx:0},{expected_snapshot_id:afterResize.snapshot_id,approved_action_id:'resize-new'}))).ok
    && await nav.page("window.chosen.includes('256')"), 'fresh approval after resize can execute the actual displayed choice')
  await nav.page(`document.body.innerHTML='<style>.high{display:none}button:hover .low{display:none}button:hover .high{display:inline}</style><button id="long-price" onclick="window.longChosen=this.innerText">${'套餐说明'.repeat(30)}<span class="low">128</span><span class="high">256</span></button>';true`)
  nav.wc.sendInputEvent({type:'mouseMove',x:500,y:400})
  await waitFor(()=>nav.page("document.querySelector('#long-price').innerText.endsWith('128')"))
  const beforeHover = await execute(cap('snapshot'))
  assert((await execute(cap('click',{idx:0},{expected_snapshot_id:beforeHover.snapshot_id,approved_action_id:'hover-old'}))).error_kind==='stale_snapshot'
    && await nav.page('!window.longChosen'), 'trial-hover cannot silently change a price beyond the public text truncation')
  const afterHover = await execute(cap('snapshot'))
  assert(beforeHover.elements[0].text===afterHover.elements[0].text
    && (await execute(cap('click',{idx:0},{expected_snapshot_id:afterHover.snapshot_id,approved_action_id:'hover-new'}))).ok
    && await nav.page("window.longChosen.endsWith('256')"), 'full private semantics protect truncated labels while fresh hover approval works')
  const slow = execute(command('navigate',{url:url+'slow'}, {run_id:'slow-run'}))
  await waitFor(()=>slowRequests>0)
  cancelBrowserRun('slow-run')
  const cancelledNavigation = await slow
  assert(!cancelledNavigation.ok, 'cancellation aborts a pending native navigation')
  await new Promise(r=>setTimeout(r,1900))
  assert(!api.getBrowserState().tabs.some(t=>api.getBrowserTab(t.id).getURL().endsWith('/slow')), 'cancelled navigation cannot commit after its response arrives')
  host.setSize(900, 900)
  const formsTab = await createBrowserTab(url + 'forms')
  await waitFor(() => !formsTab.contents.isLoading())
  const formRead = await execute(command('snapshot', {}, { run_id: 'forms-run', tab_id: formsTab.id }))
  assert(formRead.ok, 'real form snapshot can be observed without granting submission permission')
  const forms = formRead.fields.dom.forms
  const booking = forms.find(form => form.action_url === url + 'book')
  assert(forms.length === 3 && new Set(forms.map(form => form.form_id)).size === 3, 'native forms have unique snapshot/frame-local identities')
  assert(booking && !booking.truncated && booking.context_text.includes('重庆溪畔餐厅') && !booking.context_text.includes('OUTSIDE_OTHER_SHOP') && !booking.context_text.includes('HIDDEN_OTHER_SHOP'), 'form context contains only its own visible text')
  assert(!JSON.stringify(forms).includes('SECRET_'), 'password/file/hidden and invisible values never leave the driver')
  assert(booking.controls.find(control => control.name === 'party').value === '3' && booking.controls.find(control => control.name === 'contact').value === '受控姓名', 'native form-associated controls retain actual values including external form= fields')
  assert(booking.controls.find(control => control.name === 'date').label.includes('到店日期') && booking.controls.find(control => control.name === 'time').label === '到店时间', 'field labels come from actual label/aria associations')
  assert(booking.controls.find(control => control.name === 'accepted').value === true && JSON.stringify(booking.controls.find(control => control.name === 'choice').value) === '["a","b"]', 'checkbox and multiselect values keep their native types')
  assert(booking.submit_indices.length === 1 && formRead.elements[booking.submit_indices[0]].input_type === 'submit' && !formRead.elements[booking.submit_indices[0]].disabled, 'only a visible native submit matching the form action can be reviewed')
  assert(forms.every(form => form.controls.every(control => formRead.elements[control.idx])), 'form indexes map to the flattened visible-frame elements')
  await formsTab.contents.executeJavaScript("const field=document.createElement('textarea');field.name='long';field.value='x'.repeat(2001);document.querySelector('#booking').appendChild(field);true")
  const truncatedForm = await execute(command('snapshot', {}, { run_id: 'forms-run', tab_id: formsTab.id }))
  assert(truncatedForm.fields.dom.forms.find(form => form.action_url === url + 'book').truncated === true, 'bounded form truncation remains explicit rather than pretending a complete check')
  const merchantTab = await createBrowserTab(url + 'merchant-preview')
  await waitFor(() => !merchantTab.contents.isLoading())
  const merchantRead = await execute(command('extract', {}, { run_id: 'merchant-run', tab_id: merchantTab.id }))
  assert(merchantRead.ok && merchantRead.text.includes('受控短页餐厅') && merchantRead.text.includes('重庆市受控地址1号') && merchantRead.text.includes('¥98'), 'short business pages retain merchant/address/offer facts outside the article')
  const qrTab = await createBrowserTab(`http://account.dianping.com:${server.address().port}/pclogin`)
  await waitFor(() => !qrTab.contents.isLoading())
  const qrRead = await execute(command('extract', {}, { run_id: 'qr-run', tab_id: qrTab.id }))
  assert(qrRead.ok && qrRead.fields.dom.manual_gate === 'login', 'observed Dianping QR-only login is a manual gate even without password or OTP inputs')
  const securityTab = await createBrowserTab(`http://verify.meituan.com:${server.address().port}/v2/app/general_page`)
  await waitFor(() => !securityTab.contents.isLoading())
  const securityRead = await execute(command('extract', {}, { run_id: 'security-run', tab_id: securityTab.id }))
  assert(securityRead.ok && securityRead.fields.dom.manual_gate === 'captcha', 'the observed Meituan verification-center route requires manual handling without a password/OTP control')
  const publicTab = await createBrowserTab(url + 'pclogin')
  await waitFor(() => !publicTab.contents.isLoading())
  const publicRead = await execute(command('extract', {}, { run_id: 'public-run', tab_id: publicTab.id }))
  assert(publicRead.ok && publicRead.fields.dom.manual_gate === null, 'QR login text on an unrelated public page does not impersonate the site-specific login route')
  // The document is a local protocol fixture. Restore real HTTPS transport
  // before testing requests, which target only our loopback listener.
  const previewSession = publicTab.contents.session
  const previewUrl = 'https://www.szuo.com/en/niccolo-chongqing-tealounge/reserve/landing?pax=2&start_date=2026-09-11&start_time=15%3A00'
  await previewSession.protocol.handle('https', () => new Response('<div data-testid="Landing Page Root"><button data-testid="Landing Venue Panel Opener Button">The Tea Lounge</button><button data-testid="Landing Pax Panel Opener Button">2 Guests</button><button data-testid="Landing Date Panel Opener Button">Fri Sep 11</button><button data-testid="Landing Time Panel Opener Button">3:00 pm</button></div>', { headers: { 'content-type': 'text/html' } }))
  const protectedTab = await createBrowserTab(previewUrl)
  await waitFor(() => !protectedTab.contents.isLoading())
  await previewSession.protocol.unhandle('https')
  let previewConnections = 0
  const countConnection = () => previewConnections++
  server.on('connection', countConnection)
  const loopbackApi = `https://127.0.0.1:${server.address().port}/v2/booking/cart/init`
  await protectedTab.contents.executeJavaScript(`Promise.all([fetch(${JSON.stringify(loopbackApi)},{method:'POST'}).catch(()=>null),fetch(${JSON.stringify(loopbackApi.replace('/cart/init','/calendar'))},{method:'POST'}).catch(()=>null)])`)
  assert(previewConnections === 0, 'guard blocks cart/calendar before even connecting to a controlled loopback endpoint')
  assert(api.getBrowserState().tabs.find(tab => tab.id === protectedTab.id).previewProtected, 'the actual guarded tab reports preview-only protection')
  const previewRead = await execute(command('extract', {}, { run_id: 'preview-run', tab_id: protectedTab.id }))
  assert(previewRead.ok && previewRead.fields.booking_preview.protected && previewRead.fields.booking_preview.blocked_requests >= 2 && previewRead.fields.booking_preview.party_label === '2 Guests' && previewRead.fields.booking_preview.time_label === '3:00 pm', 'trusted snapshot captures actual widget labels with main-process protection')
  assert(previewRead.fields.dom.forms.length === 0, 'custom widget is not fabricated as a native form')
  const rejectedClick = await execute(command('click', { idx: 0 }, { run_id: 'preview-run', tab_id: protectedTab.id, expected_snapshot_id: previewRead.snapshot_id, approved_action_id: 'not-a-site-write-grant' }))
  assert(rejectedClick.error_kind === 'site_not_allowed', 'booking preview does not expand automatic click authority')
  await api.loadBrowserURL(protectedTab.contents, loopbackApi).catch(() => {})
  assert(previewConnections === 0, 'redirecting a protected tab cannot release protection')
  await handleBrowserIntent({ kind: 'close', id: protectedTab.id })
  await previewSession.fetch(loopbackApi, { method: 'POST' }).catch(() => {})
  assert(previewConnections === 0, 'detached requests remain blocked after the protected tab closes')
  server.removeListener('connection', countConnection)
  const stillNormal = await makeTab('after-preview')
  assert((await stillNormal.page('document.body.innerText')).includes('真实菜单测试页'), 'other owned ordinary tabs continue to load')
  const retainedRead = command('extract', {}, { run_id: 'retention-run', tab_id: stillNormal.id })
  const retainedResult = await execute(retainedRead)
  assert(retainedResult.ok && retainedResult.text.includes('128 元'), 'pending observation remains available for transport retry')
  acknowledgeBrowserCommand(retainedRead)
  const compactedRead = await execute(retainedRead)
  assert(compactedRead.error_kind === 'command_already_delivered' && !compactedRead.text && !compactedRead.fields && !compactedRead.elements && !compactedRead.screenshot, 'acknowledgement removes all large observation fields from the executor cache')
  let bounded = false
  for (let index = 0; index < 10_001; index++) {
    const expiredCommand = command('snapshot', {}, { expires_at: '2000-01-01T00:00:00Z' })
    const result = await execute(expiredCommand)
    if (result.error_kind === 'browser_command_capacity') { bounded = true; break }
    acknowledgeBrowserCommand(expiredCommand)
  }
  assert(bounded, 'identity cache has a fixed capacity and refuses new commands instead of evicting old write identities')
  assert((await execute(approvedClick)).error_kind === 'command_already_delivered', 'capacity pressure cannot revive an old acknowledged write')
  assert((await execute({ ...approvedClick, arguments: { idx: 1 } })).error_kind === 'command_conflict', 'capacity pressure preserves changed-payload rejection')
  console.log(`Browser regression: ${checks} assertions passed (real WebContentsView + trusted bridge + Playwright)`)
}
async function finish(code) {
  clearTimeout(watchdog)
  await api?.closeBrowserDriver().catch(() => {})
  host?.destroy(); server.close()
  if (!code) rmSync(work,{recursive:true,force:true})
  else console.error('Failure fixture retained:',work)
  app.exit(code)
}
main().then(()=>finish(0)).catch(error=>{console.error(error);void finish(1)})
