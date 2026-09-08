// 渲染层浏览器执行器：绑定真实标签，固定 DOM 脚本由主进程在隔离世界 1001 执行。
// DOM 蒸馏(set-of-marks) + Readability 正文提取 + Horsepower 式表格提取 + 可视化(彩色编号框+合成光标)。
import readabilitySrc from '@mozilla/readability/Readability.js?raw'
import { useStore } from '../store'
import { allowedBrowserSite, browserCommandGuard, isBrowserWrite, validateBrowserCommand, type BrowserCommand, type BrowserObservation } from '@shared/browser'

// 站点友好名（顶部浮条显示"PlanGo正在浏览 大众点评…"）
function siteName(url: string): string {
  const u = url || ''
  if (/dianping/.test(u)) return '大众点评'
  if (/meituan|waimai/.test(u)) return '美团'
  if (/xiaohongshu/.test(u)) return '小红书'
  if (/amap|gaode/.test(u)) return '高德地图'
  if (/douyin/.test(u)) return '抖音'
  if (/baidu/.test(u)) return '百度'
  try {
    return new URL(u).hostname.replace(/^www\./, '')
  } catch {
    return '网页'
  }
}

// 顶部浮条 + 自动切到浏览器视图（让用户"看着PlanGo操作"）。空闲一段时间后自动收起。
let bannerTimer: ReturnType<typeof setTimeout> | null = null
function markBrowsing(action: string, site?: string): void {
  try {
    const st = useStore.getState()
    st.setView('browser')
    st.setAiBrowsing({ active: true, action, site: site ?? st.aiBrowsing.site })
    if (bannerTimer) clearTimeout(bannerTimer)
    bannerTimer = setTimeout(() => useStore.getState().setAiBrowsing({ active: false }), 6000)
  } catch {
    /* ignore */
  }
}

export type WebviewEl = {
  getWebContentsId: () => number
  loadURL: (url: string) => Promise<void> | void
  getURL: () => string
  getTitle: () => string
  isLoading?: () => boolean
}

function executeInBrowser(wv: WebviewEl, code: string): Promise<unknown> {
  return window.plango.browserEval(wv.getWebContentsId(), code)
}

const webviews = new Map<string, WebviewEl>()
const bindings = new Map<string, string>()
const tabOwners = new Map<string, string>()
const cancelledRuns = new Set<string>()
const runEpochs = new Map<string, number>()
export function cancelRendererBrowserRun(runId: string, epoch?: number): void {
  cancelledRuns.add(runId)
  runEpochs.set(runId, epoch ?? (runEpochs.get(runId) || 0) + 1)
}
export function activateRendererBrowserRun(runId: string, epoch?: number): void {
  if (epoch !== undefined) runEpochs.set(runId, epoch)
  cancelledRuns.delete(runId)
}
export function releaseRendererBrowserRun(runId: string, epoch?: number): void {
  cancelRendererBrowserRun(runId, epoch)
  for (const [tabId, owner] of tabOwners) {
    if (JSON.parse(owner)[1] === runId) {
      tabOwners.delete(tabId)
      bindings.delete(owner)
    }
  }
}
let activeTabId: string | null = null
export function registerWebview(id: string, wv: WebviewEl | null): void {
  if (wv) webviews.set(id, wv)
  else webviews.delete(id)
}
export function setActiveWebview(wv: WebviewEl | null, tabId?: string): void {
  activeTabId = wv ? tabId || [...webviews].find(([, item]) => item === wv)?.[0] || null : null
}

type TabOpener = (url: string) => string
let tabOpener: TabOpener | null = null
export function setTabOpener(fn: TabOpener | null): void { tabOpener = fn }

function delay(ms: number): Promise<void> { return new Promise((resolve) => setTimeout(resolve, ms)) }
async function waitForTab(id: string): Promise<WebviewEl> {
  const deadline = Date.now() + 12000
  while (Date.now() < deadline) {
    const wv = webviews.get(id)
    if (wv) {
      try { if (wv.getURL() && !wv.isLoading?.()) return wv } catch { /* dom-ready pending */ }
    }
    await delay(80)
  }
  throw new Error('页面尚未就绪，请处理登录或网络问题后重新读取')
}

// Snapshot state lives in the isolated world, inaccessible to page scripts; only the DOM is shared.
function distillScript(snapshotId: string, owner: string, epoch: number): string {
  return `(function(){
    try {
      if(window.__plangoSnapshot) window.__plangoSnapshot.observer.disconnect();
      document.querySelectorAll('[data-ai-idx]').forEach(function(el){el.removeAttribute('data-ai-idx');});
      var sel='a,button,input,textarea,select,[role=button],[role=link],[role=tab],[onclick],[contenteditable=true]';
      var els=Array.prototype.slice.call(document.querySelectorAll(sel));
      var out=[],refs=[];
      for(var k=0;k<els.length;k++){
        var el=els[k],r=el.getBoundingClientRect(),st=window.getComputedStyle(el);
        if(r.width<3||r.height<3||st.visibility==='hidden'||st.display==='none'||st.opacity==='0') continue;
        var i=refs.length;el.setAttribute('data-ai-idx',String(i));refs.push(el);
        var name=el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||el.getAttribute('title')||'';
        var text=((el.innerText||'')+'').replace(/\\s+/g,' ').trim().slice(0,80);
        var item={idx:i,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',name:name,text:text};
        if(el.tagName==='A'&&el.hasAttribute('href')&&!el.hasAttribute('download')&&(!el.target||el.target==='_self')){
          try{var link=new URL(el.href);if(['http:','https:'].includes(link.protocol)&&!link.username&&!link.password)item.href=link.href;}catch(e){}
        }
        out.push(item);
        if(refs.length>=180) break;
      }
      function formFingerprint(){
        var controls=document.querySelectorAll('input,textarea,select,[contenteditable=true]');
        if(controls.length>2000)throw new Error('页面表单过于复杂，请人工操作');
        var value=JSON.stringify(Array.prototype.map.call(controls,function(el){return [
          el.tagName,el.getAttribute('name'),el.getAttribute('type'),el.value,el.checked,el.selectedIndex,
          el.selectedOptions?Array.prototype.map.call(el.selectedOptions,function(o){return o.value;}):null,
          el.isContentEditable?el.textContent:null
        ];}));
        if(value.length>100000)throw new Error('页面表单过于复杂，请人工操作');
        return value;
      }
      var state={id:${JSON.stringify(snapshotId)},owner:${JSON.stringify(owner)},epoch:${epoch},url:location.href,refs:refs,hrefs:out.map(function(item){return item.href;}),dirty:false,formFingerprint:formFingerprint(),fingerprint:formFingerprint};
      state.observer=new MutationObserver(function(){state.dirty=true;});
      state.observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true});
      window.__plangoSnapshot=state;
      if(!window.__plangoInputGuard){
        window.__plangoInputGuard=true;
        ['input','change','pointerdown','keydown'].forEach(function(event){document.addEventListener(event,function(){if(window.__plangoSnapshot)window.__plangoSnapshot.dirty=true;},true);});
      }
      return {ok:true,url:location.href,title:document.title,elements:out,text:(document.body?document.body.innerText:'').slice(0,8000)};
    }catch(e){return {ok:false,error:String(e)};}
  })()`
}

const EXTRACT_TABLES = `(function(){
  try{
    var tables=[];
    var tbs=Array.prototype.slice.call(document.querySelectorAll('table')).slice(0,6);
    for(var t=0;t<tbs.length;t++){
      var tb=tbs[t];var rows=Array.prototype.slice.call(tb.querySelectorAll('tr'));
      if(!rows.length) continue;
      var headers=Array.prototype.slice.call(rows[0].querySelectorAll('th,td')).map(function(c){return (c.innerText||'').trim();});
      var data=[];
      for(var r=1;r<rows.length&&r<20;r++){
        var cells=Array.prototype.slice.call(rows[r].querySelectorAll('td,th')).map(function(c){return (c.innerText||'').trim();});
        if(cells.length) data.push(cells);
      }
      tables.push({headers:headers,rows:data});
    }
    var body=(document.body?document.body.innerText:'').replace(/\\s+\\n/g,'\\n').slice(0,9000);
    return {text:body,url:location.href,title:document.title,tables:tables};
  }catch(e){return {error:String(e)};}
})()`

// 注入 Mozilla Readability 到页面上下文，抽取"主正文"（去掉导航/广告/评论噪声，token 省、稳）。
// 登录态内容也能读（跑在 webview 里，带 cookie）。失败返回 {ok:false} 交给调用方兜底。
const READABILITY = `${readabilitySrc}
;(function(){
  try {
    var docClone = document.cloneNode(true);
    var article = new Readability(docClone, { charThreshold: 200 }).parse();
    if (article && article.textContent && article.textContent.replace(/\\s/g,'').length > 120) {
      return { ok:true, method:'readability', url:location.href,
        title: article.title || document.title,
        byline: article.byline || '',
        excerpt: article.excerpt || '',
        text: (article.textContent||'').replace(/\\n{3,}/g,'\\n\\n').trim().slice(0,8000) };
    }
    return { ok:false };
  } catch(e){ return { ok:false, error:String(e) }; }
})()`

// Set-of-Marks 可视化：给已打 data-ai-idx 的可交互元素画彩色编号框（角色配色），单个固定层承载，避免污染 DOM。
// 学习 Browser Use / Opticlick / 微软 SoM：按钮绿 / 链接蓝 / 输入紫 / 其它橙。
const SOM_DRAW = `(function(){
  try{
    var ID='__plango_som__';
    var old=document.getElementById(ID); if(old) old.remove();
    var layer=document.createElement('div'); layer.id=ID;
    layer.style.cssText='position:fixed;left:0;top:0;width:0;height:0;z-index:2147483646;pointer-events:none;';
    var els=Array.prototype.slice.call(document.querySelectorAll('[data-ai-idx]'));
    var color=function(el){var t=el.tagName.toLowerCase();var r=el.getAttribute('role')||'';
      if(t==='button'||r==='button') return '#22c55e';
      if(t==='a'||r==='link') return '#3b82f6';
      if(t==='input'||t==='textarea'||t==='select') return '#a855f7';
      return '#f59e0b';};
    var n=0;
    for(var i=0;i<els.length;i++){
      var el=els[i];var rc=el.getBoundingClientRect();
      if(rc.width<6||rc.height<6||rc.bottom<0||rc.top>window.innerHeight) continue;
      var c=color(el);
      var box=document.createElement('div');
      box.style.cssText='position:fixed;left:'+rc.left+'px;top:'+rc.top+'px;width:'+rc.width+'px;height:'+rc.height+'px;border:2px solid '+c+';border-radius:4px;box-sizing:border-box;';
      var tag=document.createElement('div');
      tag.textContent=el.getAttribute('data-ai-idx');
      tag.style.cssText='position:fixed;left:'+rc.left+'px;top:'+(rc.top-14>0?rc.top-14:rc.top)+'px;background:'+c+';color:#fff;font:700 10px/14px -apple-system,sans-serif;padding:0 4px;border-radius:3px;';
      layer.appendChild(box);layer.appendChild(tag);
      n++; if(n>=60) break;
    }
    document.documentElement.appendChild(layer);
    return {ok:true,marked:n};
  }catch(e){return {ok:false,error:String(e)};}
})()`

function failure(command: BrowserCommand, kind: string, text = kind, outcome: BrowserObservation['outcome'] = 'blocked'): BrowserObservation {
  return { command_id: command.command_id, tab_id: command.tab_id, ok: false, outcome, error_kind: kind, error: text }
}

// All model input is data. These fixed scripts are the only JavaScript that reaches a page.
function elementActionScript(command: BrowserCommand, epoch: number): string {
  const idx = command.arguments.idx as number
  const preamble = `var state=window.__plangoSnapshot;
    if(!state||state.id!==${JSON.stringify(command.expected_snapshot_id)}||state.owner!==${JSON.stringify(JSON.stringify([command.browser_session_id, command.run_id]))}||state.epoch!==${epoch}||state.url!==location.href||state.dirty||state.observer.takeRecords().length||state.formFingerprint!==state.fingerprint())
      return {ok:false,error_kind:'stale_snapshot',error:'页面已变化，请重新读取并确认目标'};
    var el=state.refs[${idx}];
    if(!el||!el.isConnected||el.getAttribute('data-ai-idx')!==${JSON.stringify(String(idx))})
      return {ok:false,error_kind:'stale_snapshot',error:'目标元素已变化'};
    var rect=el.getBoundingClientRect(),style=window.getComputedStyle(el);
    if(el.disabled||rect.width<3||rect.height<3||style.visibility==='hidden'||style.display==='none')
      return {ok:false,error_kind:'element_unavailable',error:'目标元素不可操作'};`
  let action: string
  if (command.operation === 'click') {
    action = `var text=(el.innerText||'').slice(0,80),href=state.hrefs[${idx}];
      if(href){
        if(el.tagName!=='A'||el.href!==href||el.hasAttribute('download')||(el.target&&el.target!=='_self'))return {ok:false,error_kind:'stale_snapshot',error:'链接目标已变化，请重新读取并确认'};
        el.scrollIntoView({block:'center'});rect=el.getBoundingClientRect();
        var hit=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
        if(!hit||(hit!==el&&!el.contains(hit)))return {ok:false,error_kind:'element_obscured',error:'链接被遮挡，请先处理页面遮挡'};
        state.dirty=true;return {ok:true,navigation_url:href};
      }
      state.dirty=true;el.click();return {ok:true,text:text};`
  } else if (command.operation === 'type') {
    action = `if(!['INPUT','TEXTAREA','SELECT'].includes(el.tagName)&&!el.isContentEditable)return {ok:false,error_kind:'invalid_element',error:'目标不是输入框'};
      if(el.tagName==='INPUT'&&['password','file','hidden'].includes(el.type))return {ok:false,error_kind:'manual_input_required',error:'敏感输入请由用户在页面填写'};
      var value=${JSON.stringify(command.arguments.text)};
      if(el.tagName==='SELECT'&&!Array.prototype.some.call(el.options,function(option){return option.value===value&&!option.disabled&&!(option.parentElement.tagName==='OPTGROUP'&&option.parentElement.disabled);}))return {ok:false,error_kind:'invalid_option',error:'目标选项不存在或不可选'};
      state.dirty=true;el.focus();
      if(el.isContentEditable)el.innerText=value;else {var setter=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value');if(setter&&setter.set)setter.set.call(el,value);else el.value=value;}
      el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
      if(!el.isConnected||(el.isContentEditable?el.innerText:el.value)!==value)return {ok:false,outcome:'unknown',error_kind:'input_not_applied',error:'页面未保留请求的输入；请核对页面，不会自动重试'};
      return {ok:true};`
  } else {
    action = `el.style.outline='3px solid #ffd100';el.scrollIntoView({block:'center'});return {ok:true};`
  }
  return `(function(){${preamble}${action}})()`
}

export async function executeRendererBrowserCommand(raw: BrowserCommand, commandEpoch?: number): Promise<BrowserObservation> {
  let command: BrowserCommand
  try { command = validateBrowserCommand(raw) }
  catch (error) { return failure(raw, 'invalid_command', (error as Error).message) }
  const guard = browserCommandGuard(command)
  if (guard) return failure(command, guard)
  const epoch = commandEpoch ?? runEpochs.get(command.run_id) ?? 0
  if (epoch !== (runEpochs.get(command.run_id) || 0)) return failure(command, 'run_superseded', '浏览器命令所属轮次已结束')
  if (cancelledRuns.has(command.run_id)) return failure(command, 'run_cancelled', '任务已停止')
  const owner = JSON.stringify([command.browser_session_id, command.run_id])
  let tabId = command.tab_id || bindings.get(owner)
  if (tabId && tabOwners.has(tabId) && tabOwners.get(tabId) !== owner) return failure(command, 'tab_session_mismatch')
  markBrowsing(command.operation, undefined)
  try {
    if (command.operation === 'open_tab') tabId = undefined
    if (!tabId) {
      if (command.operation === 'navigate' || command.operation === 'open_tab') {
        if (!tabOpener) return failure(command, 'browser_unavailable')
        tabId = tabOpener(String(command.arguments.url))
      } else {
        // First observation may attach to the user's current tab, once. Later commands remain pinned.
        tabId = activeTabId || undefined
        if (!tabId) return failure(command, 'tab_required', '请先打开一个浏览器标签')
        if (tabOwners.has(tabId) && tabOwners.get(tabId) !== owner) return failure(command, 'tab_session_mismatch')
      }
      bindings.set(owner, tabId)
      tabOwners.set(tabId, owner)
    } else if (!tabOwners.has(tabId)) {
      // A supplied id can only attach to an existing user tab, never create an alias.
      if (!webviews.has(tabId)) return failure(command, 'tab_not_found')
      bindings.set(owner, tabId)
      tabOwners.set(tabId, owner)
    }
    if (!useStore.getState().tabs.some((tab) => tab.id === tabId)) return failure(command, 'tab_closed')
    useStore.getState().setActiveTab(tabId)
    const wv = await waitForTab(tabId)
    const expired = browserCommandGuard(command)
    if (expired) return failure(command, expired)
    if (epoch !== (runEpochs.get(command.run_id) || 0)) return failure(command, 'run_superseded', '浏览器命令所属轮次已结束')
    if (cancelledRuns.has(command.run_id)) return failure(command, 'run_cancelled', '任务已停止')
    const base = { command_id: command.command_id, tab_id: tabId, url: wv.getURL(), title: wv.getTitle() }
    if (isBrowserWrite(command.operation) && !allowedBrowserSite(base.url)) return { ...failure(command, 'site_not_allowed', '当前站点尚未开放自动写操作'), ...base }
    if (command.operation === 'navigate' || command.operation === 'open_tab') {
      const url = String(command.arguments.url)
      if (wv.getURL() !== url) await wv.loadURL(url)
      await waitForTab(tabId)
      return { ...base, ok: true, outcome: 'observed', url: wv.getURL(), title: wv.getTitle() }
    }
    switch (command.operation) {
      case 'snapshot':
      case 'read_page': {
        markBrowsing('读取页面', siteName(base.url))
        const snapshotId = command.command_id
        const result = await executeInBrowser(wv, distillScript(snapshotId, owner, epoch)) as Partial<BrowserObservation>
        if (!result.ok) return { ...base, ...failure(command, 'page_read_failed', result.error), ...result }
        try { await executeInBrowser(wv, SOM_DRAW) } catch { /* decoration only */ }
        return { ...base, ...result, command_id: command.command_id, tab_id: tabId, snapshot_id: snapshotId, ok: true, outcome: 'observed' }
      }
      case 'extract':
      case 'extract_tables': {
        const snapshot = await executeInBrowser(wv, distillScript(command.command_id, owner, epoch)) as Partial<BrowserObservation>
        if (!snapshot.ok) return { ...failure(command, 'page_read_failed', snapshot.error), ...base }
        const result = await executeInBrowser(wv, EXTRACT_TABLES) as Partial<BrowserObservation>
        if (result.error) return { ...failure(command, 'page_read_failed', result.error), ...base }
        try {
          const article = await executeInBrowser(wv, READABILITY) as { ok?: boolean; text?: string }
          if (article.ok && article.text) result.text = article.text
        } catch { /* retain actual DOM text */ }
        return { ...base, ...snapshot, ...result, snapshot_id: command.command_id, ok: true, outcome: 'observed' }
      }
      case 'current': return { ...base, ok: true, outcome: 'observed' }
      case 'click':
      case 'type':
      case 'highlight': {
        const result = await executeInBrowser(wv, elementActionScript(command, epoch)) as Partial<BrowserObservation> & { navigation_url?: string }
        if (result.ok !== true) return { ...base, ...result, command_id: command.command_id, ok: false, outcome: result.outcome === 'unknown' ? 'unknown' : 'blocked' }
        if (command.operation === 'click' && result.navigation_url) {
          // A bound anchor uses native navigation; page click handlers cannot turn it into an unrelated submission.
          await wv.loadURL(result.navigation_url)
          await waitForTab(tabId)
          if (cancelledRuns.has(command.run_id) || epoch !== (runEpochs.get(command.run_id) || 0)) return failure(command, 'run_superseded', '导航期间任务已停止', 'unknown')
          if (wv.getURL() !== result.navigation_url) return { ...base, ...failure(command, 'navigation_redirected', '导航目标发生跳转，请重新核对页面', 'unknown'), url: wv.getURL() }
          return { ...base, ok: true, outcome: 'executed', interaction_kind: 'navigation', url: wv.getURL(), title: wv.getTitle() }
        }
        // A DOM acknowledgement is not an order/booking receipt. The Harness must verify business state separately.
        return { ...base, ...result, command_id: command.command_id, ok: true, outcome: 'executed' }
      }
      case 'scroll':
        await executeInBrowser(wv, `(function(){if(window.__plangoSnapshot)window.__plangoSnapshot.dirty=true;window.scrollBy(0,${command.arguments.dir === 'up' ? -700 : 700});return true;})()`)
        return { ...base, ok: true, outcome: 'executed' }
    }
  } catch (error) {
    return { ...failure(command, 'browser_execution_failed', (error as Error).message, isBrowserWrite(command.operation) ? 'unknown' : 'failed'), tab_id: tabId }
  }
}

let installed = false
let execution: Promise<unknown> = Promise.resolve()
export function installBrowserBridge(): void {
  if (installed) return
  installed = true
  window.plango.onBrowserExec(({ id, action, args }) => {
    if (action === 'cancel_run' && typeof args.run_id === 'string') {
      cancelRendererBrowserRun(args.run_id, args.epoch as number | undefined)
      return
    }
    if (action === 'release_run' && typeof args.run_id === 'string') {
      releaseRendererBrowserRun(args.run_id, args.epoch as number | undefined)
      return
    }
    if (action === 'activate_run' && typeof args.run_id === 'string') {
      activateRendererBrowserRun(args.run_id, args.epoch as number | undefined)
      return
    }
    const command = args.command as BrowserCommand | undefined
    const epoch = typeof args.epoch === 'number' ? args.epoch : runEpochs.get(command?.run_id || '') || 0
    // ponytail: one page automation at a time; use per-session queues if concurrent browsing becomes necessary.
    execution = execution.catch(() => {}).then(async () => {
      const result = action === 'harness'
        ? await executeRendererBrowserCommand(command as BrowserCommand, epoch)
        : { ok: false, outcome: 'blocked', error_kind: 'unsupported_command', error: '浏览器命令必须经过 Harness 验证' }
      window.plango.browserExecResult(id, result)
    })
  })
}
