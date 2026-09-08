// Trusted main-process driver. Page scripts never receive the CDP endpoint or snapshot registry.
import { app, webContents, type WebContents } from 'electron'
import { readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { get } from 'node:http'
import { createHash, randomUUID } from 'node:crypto'
import { chromium, selectors, type Browser, type CDPSession, type Frame, type Page } from 'playwright-core'
import { getBrowserTabBounds, loadBrowserURL } from './browserView'
import readabilitySource from '@mozilla/readability/Readability.js?raw'
import { allowedBrowserSite, browserCommandGuard, isBrowserWrite, validateBrowserCommand, type BrowserCommand, type BrowserObservation, type ScreenshotEvidence } from '../shared/browser'

export interface BrowserDriverContext {
  signal: AbortSignal
  check: () => void
  owner: string
  epoch: number
}
type ElementInfo = NonNullable<BrowserObservation['elements']>[number] & { input_type?: string; editable?: boolean; disabled?: boolean }
type FormControl = { idx: number; input_type: string; name: string; label: string; value: string | boolean | string[]; disabled: boolean }
type DomForm = { form_id: string; action_url: string; context_text: string; controls: FormControl[]; submit_indices: number[]; truncated: boolean }
type FrameSnapshot = { frame: Frame; url: string; version: string }
type Snapshot = {
  id: string; owner: string; epoch: number; url: string; page: Page; consumed: boolean; manualGate: 'login' | 'captcha' | null
  frames: FrameSnapshot[]; refs: { frame: Frame; index: number; info: ElementInfo }[]
}
const snapshots = new Map<WebContents, Snapshot>()
let browser: Browser | undefined
let connecting: Promise<Browser> | undefined
const processStartedAt = Date.now() - process.uptime() * 1000
let selectorName = ''

// A contentScript selector runs in Playwright's isolated utility world. Its closure
// retains exact node identities; neither a forged attribute nor a replacement node
// can inherit an approval. The engine is a fixed program; command input is JSON data.
const selectorSource = `(() => {
  ${readabilitySource}
  let current;
  const interactive='a,button,input,textarea,select,[role=button],[role=link],[role=tab],[onclick],[contenteditable=true]';
  const formSelector='input,textarea,select,[contenteditable=true]';
  function roots(){
    const found=[document];let nodes=0;
    for(let i=0;i<found.length;i++)for(const el of found[i].querySelectorAll('*')){
      if(++nodes>100000)throw Error('page_too_complex');
      if(el.shadowRoot)found.push(el.shadowRoot);
    }
    return found;
  }
  function fingerprint(rs,ignore){
    const values=[];
    for(const root of rs)for(const el of root.querySelectorAll(formSelector)){
      if(values.length>=2000)throw Error('page_too_complex');
      values.push([el.tagName,el.getAttribute('name'),el.getAttribute('type'),
        el===ignore?null:el.value,el===ignore?null:el.checked,el===ignore?null:el.selectedIndex,
        el!==ignore&&el.selectedOptions?Array.from(el.selectedOptions,o=>o.value):null,
        el!==ignore&&el.isContentEditable?el.textContent:null]);
    }
    const value=JSON.stringify(values);if(value.length>100000)throw Error('page_too_complex');return value;
  }
  function nameOf(el){
    return el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||el.getAttribute('title')||'';
  }
  function hrefOf(el){
    if(el.tagName!=='A'||!el.hasAttribute('href')||el.hasAttribute('download')||(el.target&&el.target!=='_self'))return null;
    try{const url=new URL(el.href);if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password)return url.href;}catch{}
    return null;
  }
  function meaning(el){
    const value=JSON.stringify([el.tagName.toLowerCase(),el.getAttribute('role')||'',nameOf(el),(el.innerText||'').replace(/\s+/g,' ').trim(),hrefOf(el)]);
    // Large targets remain readable but require a smaller target or human action.
    return value.length<=20000?value:null;
  }
  function viewport(){return JSON.stringify([innerWidth,innerHeight,devicePixelRatio]);}
  function metadata(value){const el=document.createElement('pre');el.textContent=JSON.stringify(value);return [el];}
  function identity(p){
    if(!current||current.id!==p.id||current.owner!==p.owner||current.epoch!==p.epoch||current.doc!==document||current.url!==location.href)throw Error('stale_snapshot');
    return current;
  }
  function validate(p){
    const s=identity(p),rs=roots();
    if(s.viewport!==viewport()||s.dirty||s.observers.some(o=>o.takeRecords().length)||rs.length!==s.roots.length||rs.some((r,i)=>r!==s.roots[i])||fingerprint(rs)!==s.fingerprint)throw Error('stale_snapshot');
    return s;
  }
  function target(s,index){
    if(document.visibilityState!=='visible')throw Error('browser_not_visible');
    const el=s.refs[index];if(!el||!el.isConnected)throw Error('stale_snapshot');
    if(s.meanings[index]===null)throw Error('element_too_complex');
    if(s.meanings[index]!==meaning(el))throw Error('stale_snapshot');
    const r=el.getBoundingClientRect(),style=getComputedStyle(el);
    if(el.disabled||r.width<3||r.height<3||style.visibility==='hidden'||style.display==='none'||style.opacity==='0')throw Error('element_unavailable');
    return el;
  }
  function manualGate(rs){
    // Sensitive controls are independent of the 180 model-visible reference budget.
    const controls=[];
    for(const root of rs)for(const el of root.querySelectorAll(formSelector)){
      const r=el.getBoundingClientRect(),style=getComputedStyle(el);
      if(r.width>=3&&r.height>=3&&style.visibility!=='hidden'&&style.display!=='none'&&style.opacity!=='0')controls.push(el);
    }
    let gate=controls.some(el=>el.tagName==='INPUT'&&el.type==='password')?'login':null;
    if(controls.some(el=>(['INPUT','TEXTAREA'].includes(el.tagName)||el.isContentEditable)&&(/captcha|验证码|验证代码|安全验证/i.test([el.getAttribute('aria-label'),el.getAttribute('name'),el.getAttribute('placeholder'),...(el.labels?Array.from(el.labels,label=>label.innerText):[])].join(' '))||el.getAttribute('autocomplete')==='one-time-code')))gate='captcha';
    const text=(document.body?document.body.innerText:'').trim().slice(0,1000);
    if(/^(安全验证|人机验证|验证码|Just a moment[.]*|Checking your browser|Verify (you are|you're) human)(\\s*[|–-].*)?$/i.test(document.title.trim())||/^(请完成(人机|安全|身份)验证|请验证您是真人)/.test(text))gate='captcha';
    return gate;
  }
  function visible(el){const r=el.getBoundingClientRect();return r.width>=3&&r.height>=3&&el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});}
  function formData(rs,refs){
    const found=rs.flatMap(root=>Array.from(root.querySelectorAll('form'))),out=[];
    for(let ordinal=0;ordinal<Math.min(20,found.length);ordinal++){
      const form=found[ordinal],controls=[],submit_indices=[];
      let truncated=found.length>20,context_text='';
      const walker=document.createTreeWalker(form,NodeFilter.SHOW_TEXT);
      for(let node=walker.nextNode();node;node=walker.nextNode()){
        const parent=node.parentElement;
        if(!parent||parent.closest('script,style,noscript,textarea,select')||!parent.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}))continue;
        context_text+=(context_text?' ':'')+(node.textContent||'').trim();
        if(context_text.length>6000){context_text=context_text.slice(0,6000);truncated=true;break;}
      }
      const members=Array.from(new Set([...Array.from(form.elements),...Array.from(form.querySelectorAll('[contenteditable=true]'))]));
      for(const el of members){
        if(!['INPUT','TEXTAREA','SELECT','BUTTON'].includes(el.tagName)&&!el.isContentEditable)continue;
        if(!visible(el)||['password','file','hidden'].includes(el.type))continue;
        const idx=refs.indexOf(el);
        if(idx<0){truncated=true;continue;}
        if(['BUTTON','INPUT'].includes(el.tagName)&&['submit','image'].includes(el.type)){
          if(el.form===form&&!el.disabled&&(!el.hasAttribute('formaction')||el.formAction===form.action))submit_indices.push(idx);
        }
        if(!['INPUT','TEXTAREA','SELECT'].includes(el.tagName)&&!el.isContentEditable)continue;
        if(controls.length>=80){truncated=true;continue;}
        let value=['checkbox','radio'].includes(el.type)?el.checked:el.tagName==='SELECT'&&el.multiple?Array.from(el.selectedOptions,option=>option.value):el.isContentEditable?el.innerText:el.value;
        if(typeof value==='string'&&value.length>2000){value=value.slice(0,2000);truncated=true;}
        if(Array.isArray(value)&&(value.length>20||value.some(item=>item.length>2000))){value=value.slice(0,20).map(item=>item.slice(0,2000));truncated=true;}
        const label=[el.getAttribute('aria-label')||'',...Array.from(el.labels||[],label=>label.innerText)].filter(Boolean).join(' / ');
        const name=el.getAttribute('name')||'';
        if(label.length>500||name.length>200)truncated=true;
        controls.push({idx,input_type:el.isContentEditable?'contenteditable':el.type||el.tagName.toLowerCase(),name:name.slice(0,200),label:label.slice(0,500),value,disabled:!!el.disabled});
      }
      if(!controls.length&&!submit_indices.length)continue;
      let action_url='';try{const action=new URL(form.action);if(['http:','https:'].includes(action.protocol)&&!action.username&&!action.password&&action.href.length<=8192)action_url=action.href;else truncated=true;}catch{truncated=true;}
      const result={form_id:'form-'+ordinal,action_url,context_text,controls,submit_indices,truncated};
      if(JSON.stringify(result).length>48000){result.controls=[];result.submit_indices=[];result.truncated=true;}
      out.push(result);
    }
    return out;
  }
  function capture(p){
    if(current)for(const observer of current.observers)observer.disconnect();
    const rs=roots(),refs=[],elements=[],tables=[];
    for(const root of rs){
      for(const el of root.querySelectorAll(interactive)){
        if(refs.length>=180)break;
        const r=el.getBoundingClientRect(),style=getComputedStyle(el);
        if(r.width<3||r.height<3||style.visibility==='hidden'||style.display==='none'||style.opacity==='0')continue;
        const name=nameOf(el);
        const item={idx:refs.length,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',name:name.slice(0,200),text:(el.innerText||'').replace(/\\s+/g,' ').trim().slice(0,80),input_type:el.type||'',editable:!!(el.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(el.tagName)),disabled:!!el.disabled};
        const href=hrefOf(el);if(href)item.href=href;
        refs.push(el);elements.push(item);
      }
      for(const table of root.querySelectorAll('table')){
        if(tables.length>=6)break;
        const rows=Array.from(table.querySelectorAll('tr'));if(!rows.length)continue;
        const cells=row=>Array.from(row.querySelectorAll('th,td'),c=>(c.innerText||'').trim().slice(0,1000)).slice(0,30);
        tables.push({headers:cells(rows[0]),rows:rows.slice(1,20).map(cells)});
      }
    }
    let text=(document.body?document.body.innerText:'').slice(0,9000);
    if(p.extract)try{const article=new Readability(document.cloneNode(true),{charThreshold:200}).parse();if(article&&article.textContent&&article.textContent.replace(/\\s/g,'').length>120)text=article.textContent.replace(/\\n{3,}/g,'\\n\\n').trim().slice(0,9000);}catch{}
    const s={id:p.id,owner:p.owner,epoch:p.epoch,doc:document,url:location.href,version:p.version,refs,elements,meanings:refs.map(meaning),viewport:viewport(),roots:rs,fingerprint:fingerprint(rs),dirty:false,observers:[],armed:null,dispatched:false,prevented:false};
    for(const root of rs){const observer=new MutationObserver(()=>{s.dirty=true});observer.observe(root,{subtree:true,childList:true,characterData:true,attributes:true});s.observers.push(observer);}
    current=s;
    return metadata({url:s.url,title:document.title,text,elements,tables,forms:formData(rs,refs),version:s.version,manual_gate:manualGate(rs),canvas_count:rs.reduce((count,root)=>count+root.querySelectorAll('canvas').length,0)});
  }
  // This guard also catches mutations during Playwright's actionability waits.
  // Once an input event has begun, any uncertainty is reported as UNKNOWN upstream.
  for(const eventName of ['pointerdown','mousedown','keydown','beforeinput','input','change','click','submit'])window.addEventListener(eventName,event=>{
    const s=current;if(!s)return;
    if(s.armed&&Date.now()>=s.armed.expires)s.armed=null;
    if((s.armed||s.trial)&&document.visibilityState!=='visible'){s.prevented=true;event.preventDefault();event.stopImmediatePropagation();return;}
    if(s.trial){if(!event.composedPath().includes(s.trial))s.dirty=true;return;}
    if(!s.armed){s.dirty=true;return;}
    const el=s.refs[s.armed.index];
    const eventTarget=event.composedPath()[0];
    if(!s.dispatched){
      let valid=false;
      try{
        const rs=roots();const changed=s.observers.some(o=>o.takeRecords().length)||s.dirty;
        const forms=s.armed.operation==='type'?fingerprint(rs,el)===s.armed.otherFields:fingerprint(rs)===s.fingerprint;
        valid=!changed&&forms&&s.viewport===viewport()&&s.meanings[s.armed.index]===meaning(el)&&el&&el.isConnected&&(eventTarget===el||el.contains(eventTarget));
      }catch{}
      if(!valid){s.prevented=true;event.preventDefault();event.stopImmediatePropagation();return;}
      s.dispatched=true;
    }
    s.dirty=true;
  },true);
  return {
    query(root,body){return this.queryAll(root,body)[0]||null;},
    queryAll(root,body){
      const p=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(body),c=>c.charCodeAt(0))));
      if(p.action==='capture')return capture(p);
      if(p.action==='manual_gate')return metadata({manual_gate:manualGate(roots())});
      if(p.action==='disarm'){if(current&&current.id===p.id&&current.owner===p.owner&&current.epoch===p.epoch){current.armed=null;current.trial=null;}return metadata({});}
      const s=identity(p);
      if(p.action==='end_trial'){s.trial=null;return metadata({});}
      if(p.action==='status')return metadata({dispatched:s.dispatched,prevented:s.prevented,url:location.href});
      if(p.action==='value'){
        const el=s.refs[p.index];return metadata({connected:!!el&&el.isConnected,value:el&&(el.isContentEditable?el.innerText:el.value)});
      }
      validate(p);
      if(p.action==='validate')return metadata({version:s.version});
      const el=target(s,p.index);
      if(p.action==='trial'){s.trial=el;return metadata({});}
      if(p.action==='arm'){
        if(p.operation==='type'){
          if(!['INPUT','TEXTAREA','SELECT'].includes(el.tagName)&&!el.isContentEditable)throw Error('invalid_element');
          if(el.tagName==='INPUT'&&['password','file','hidden'].includes(el.type))throw Error('manual_input_required');
          if(el.readOnly)throw Error('element_unavailable');
          if(el.tagName==='SELECT'&&!Array.from(el.options).some(o=>o.value===p.value&&!o.disabled&&!(o.parentElement.tagName==='OPTGROUP'&&o.parentElement.disabled)))throw Error('invalid_option');
        }
        s.armed={index:p.index,operation:p.operation,expires:p.expires,otherFields:fingerprint(s.roots,el)};
        return metadata({tag:el.tagName.toLowerCase(),href:s.elements[p.index].href});
      }
      return [el];
    }
  };
})()`

function selector(action: string, snapshot: Pick<Snapshot, 'id' | 'owner' | 'epoch'>, extra: Record<string, unknown> = {}): string {
  return selectorName + '=' + Buffer.from(JSON.stringify({ action, id: snapshot.id, owner: snapshot.owner, epoch: snapshot.epoch, ...extra })).toString('base64')
}
async function data<T>(frame: Frame, expression: string, signal: AbortSignal): Promise<T> {
  const value = await frame.locator(expression).textContent({ timeout: 4000, signal })
  if (!value) throw new Error('page_read_failed')
  return JSON.parse(value) as T
}
function readBeforeDeadline<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const aborted = () => { signal.removeEventListener('abort', aborted); reject(signal.reason) }
    if (signal.aborted) { pending.catch(() => {}); reject(signal.reason); return }
    signal.addEventListener('abort', aborted, { once: true })
    pending.then(value => { signal.removeEventListener('abort', aborted); resolve(value) }, error => { signal.removeEventListener('abort', aborted); reject(error) })
  })
}
async function endpoint(signal: AbortSignal): Promise<string> {
  const path = join(app.getPath('userData'), 'DevToolsActivePort')
  const [file, info] = await Promise.all([readFile(path, 'utf8'), stat(path)])
  if (info.mtimeMs + 1000 < processStartedAt) throw new Error('browser_endpoint_stale')
  const [port, browserPath] = file.trim().split(/\r?\n/)
  if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535 || !/^\/devtools\/browser\/[\da-f-]+$/i.test(browserPath || '')) throw new Error('browser_endpoint_invalid')
  const version = await new Promise<{ webSocketDebuggerUrl?: string }>((resolve, reject) => {
    const request = get(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.any([signal, AbortSignal.timeout(3000)]) }, response => {
      let body = ''
      response.on('data', chunk => { body += chunk; if (body.length > 65536) request.destroy(new Error('browser_endpoint_invalid')) })
      response.on('error', reject)
      response.on('end', () => { try { if (response.statusCode !== 200) throw new Error('browser_endpoint_stale'); resolve(JSON.parse(body)) } catch (error) { reject(error) } })
    })
    request.on('error', reject)
  })
  const url = `ws://127.0.0.1:${port}${browserPath}`
  if (version.webSocketDebuggerUrl !== url) throw new Error('browser_endpoint_stale')
  return url
}
async function connected(signal: AbortSignal): Promise<Browser> {
  if (browser?.isConnected()) return browser
  connecting ||= (async () => {
    const value = await chromium.connectOverCDP(await endpoint(signal), { noDefaults: true, timeout: 5000 })
    // CDP's pre-existing context does not inherit engines registered before connect.
    // Each connection registers after attachment; old snapshots are discarded on disconnect.
    selectorName = 'plango_' + randomUUID().replace(/-/g, '')
    await selectors.register(selectorName, { content: selectorSource }, { contentScript: true })
    browser = value
    value.on('disconnected', () => { if (browser === value) { browser = undefined; snapshots.clear() } })
    return value
  })().finally(() => { connecting = undefined })
  return connecting
}
async function pageFor(contents: WebContents, signal: AbortSignal, check: () => void): Promise<Page> {
  const client = await connected(signal)
  check()
  for (const context of client.contexts()) for (const page of context.pages()) {
    check()
    if (page.isClosed()) continue
    let session: CDPSession | undefined
    try {
      session = await readBeforeDeadline(context.newCDPSession(page), signal)
      const { targetInfo } = await readBeforeDeadline(session.send('Target.getTargetInfo'), signal)
      check()
      if (webContents.fromDevToolsTargetId(targetInfo.targetId) === contents) return page
    } catch (error) {
      check()
      // pages() is a snapshot: an unrelated tab can close before CDP attaches.
      if (!page.isClosed() && !/(Target .*closed|Target closed|Session closed|No (target|session) with given id)/i.test(String(error))) throw error
    } finally { if (session) await readBeforeDeadline(session.detach(), AbortSignal.timeout(1000)).catch(() => {}) }
  }
  throw new Error('browser_target_unavailable')
}
function pageVersion(snapshot: Snapshot): string { return createHash('sha256').update(snapshot.frames.map(frame => frame.version).join(':')).digest('hex') }
async function validateSnapshot(snapshot: Snapshot, ctx: BrowserDriverContext, signal: AbortSignal): Promise<void> {
  ctx.check(); signal.throwIfAborted()
  if (snapshot.consumed || snapshot.owner !== ctx.owner || snapshot.epoch !== ctx.epoch || snapshot.url !== snapshot.page.url()) throw new Error('stale_snapshot')
  const live = snapshot.page.frames()
  for (const saved of snapshot.frames) {
    if (!live.includes(saved.frame) || saved.frame.url() !== saved.url) throw new Error('stale_snapshot')
    await data(saved.frame, selector('validate', snapshot), signal)
    ctx.check(); signal.throwIfAborted()
  }
}
async function currentManualGate(snapshot: Snapshot, ctx: BrowserDriverContext, signal: AbortSignal): Promise<'login' | 'captcha' | null> {
  let gate = snapshot.manualGate
  const visible: Frame[] = []
  for (const frame of snapshot.page.frames()) {
    ctx.check(); signal.throwIfAborted()
    if (frame.parentFrame()) {
      const element = await readBeforeDeadline(frame.frameElement(), signal)
      try { if (!(await readBeforeDeadline(element.isVisible(), signal))) continue } finally { await element.dispose().catch(() => {}) }
    }
    visible.push(frame)
    const result = await data<{manual_gate: 'login' | 'captcha' | null}>(frame, selector('manual_gate', snapshot), signal)
    ctx.check(); signal.throwIfAborted()
    if (result.manual_gate === 'captcha' || !gate) gate = result.manual_gate
  }
  if (!gate && (visible.length !== snapshot.frames.length || visible.some(frame => !snapshot.frames.some(saved => saved.frame === frame)))) throw new Error('stale_snapshot')
  return gate
}
function errorKind(error: unknown): string {
  const message = String(error)
  return ['stale_snapshot', 'invalid_element', 'invalid_option', 'manual_input_required', 'element_unavailable', 'element_too_complex', 'page_too_complex', 'browser_endpoint_invalid', 'browser_endpoint_stale', 'browser_target_unavailable', 'browser_not_visible', 'run_superseded', 'run_cancelled', 'command_expired'].find(kind => message.includes(kind)) || (message.includes('Timeout') ? 'browser_timeout' : 'browser_execution_failed')
}

export async function executeBrowserOperation(contents: WebContents, raw: BrowserCommand, ctx: BrowserDriverContext): Promise<Partial<BrowserObservation>> {
  let command: BrowserCommand
  try { command = validateBrowserCommand(raw) } catch { return { ok: false, outcome: 'blocked', error_kind: 'invalid_command' } }
  const guard = browserCommandGuard(command)
  if (guard) return { ok: false, outcome: 'blocked', error_kind: guard }
  const expires = Math.min(command.expires_at ? Date.parse(command.expires_at) : Infinity, Date.now() + 20_000)
  const signal = AbortSignal.any([ctx.signal, AbortSignal.timeout(Math.max(1, expires - Date.now()))])
  const check = () => {
    if (Date.now() >= expires) throw new Error('command_expired')
    if (ctx.signal.aborted) throw new Error('run_cancelled')
    signal.throwIfAborted(); ctx.check()
    if (contents.isDestroyed()) throw new Error('browser_target_unavailable')
  }
  const step = async <T>(operation: () => Promise<T>): Promise<T> => { check(); const result = await operation(); check(); return result }
  let attempted = false
  let armedFrame: Frame | undefined
  let armedSnapshot: Snapshot | undefined
  try {
    check()
    if (ctx.owner !== JSON.stringify([command.browser_session_id, command.run_id])) throw new Error('run_superseded')
    if (isBrowserWrite(command.operation) && !allowedBrowserSite(contents.getURL())) return { ok: false, outcome: 'blocked', error_kind: 'site_not_allowed' }
    const page = await step(() => pageFor(contents, signal, check))
    const base = { command_id: command.command_id, tab_id: command.tab_id, url: contents.getURL(), title: contents.getTitle() }
    if (command.operation === 'current') return { ...base, ok: true, outcome: 'observed' }
    if (['snapshot', 'read_page', 'extract', 'extract_tables'].includes(command.operation)) {
      const snapshot: Snapshot = { id: command.command_id, owner: ctx.owner, epoch: ctx.epoch, url: page.url(), page, frames: [], refs: [], consumed: false, manualGate: null }
      const elements: NonNullable<BrowserObservation['elements']> = [], tables: NonNullable<BrowserObservation['tables']> = [], texts: string[] = []
      const forms: DomForm[] = []
      let canvasCount = 0
      const frames = page.frames()
      if (frames.length > 32) throw new Error('page_too_complex')
      for (const frame of frames) {
        if (frame.parentFrame()) {
          const element = await step(() => frame.frameElement())
          try { if (!(await step(() => element.isVisible()))) continue } finally { await element.dispose() }
        }
        const observed = await step(() => data<{ url: string; version: string; manual_gate: 'login' | 'captcha' | null; canvas_count: number; text: string; elements: ElementInfo[]; tables: NonNullable<BrowserObservation['tables']>; forms: DomForm[] }>(frame, selector('capture', snapshot, { version: randomUUID(), extract: command.operation === 'extract' || command.operation === 'extract_tables' }), signal))
        snapshot.frames.push({ frame, url: observed.url, version: observed.version })
        texts.push(observed.text); tables.push(...observed.tables); canvasCount += observed.canvas_count
        if (observed.manual_gate === 'captcha' || !snapshot.manualGate) snapshot.manualGate = observed.manual_gate
        const indices = new Map<number, number>()
        for (const info of observed.elements) {
          if (elements.length >= 180) break
          const idx = elements.length
          snapshot.refs.push({ frame, index: info.idx, info })
          indices.set(info.idx, idx)
          elements.push({ idx, tag: info.tag, role: info.role, name: info.name, text: info.text, input_type: info.input_type, disabled: info.disabled, ...(info.href ? { href: info.href } : {}) })
        }
        for (const form of observed.forms) {
          if (forms.length >= 20) { for (const saved of forms) saved.truncated = true; break }
          const controls = form.controls.filter(control => indices.has(control.idx)).map(control => ({ ...control, idx: indices.get(control.idx)! }))
          const submit_indices = form.submit_indices.filter(index => indices.has(index)).map(index => indices.get(index)!)
          forms.push({ ...form, form_id: `${snapshot.id}:frame-${snapshot.frames.length - 1}:${form.form_id}`, controls, submit_indices,
            truncated: form.truncated || controls.length !== form.controls.length || submit_indices.length !== form.submit_indices.length })
        }
      }
      await step(() => validateSnapshot(snapshot, ctx, signal))
      snapshots.set(contents, snapshot)
      // ponytail: retain 64 tab snapshots; an evicted tab must be read again before action.
      if (snapshots.size > 64) snapshots.delete(snapshots.keys().next().value!)
      return { ...base, ok: true, outcome: 'observed', snapshot_id: snapshot.id, page_version: pageVersion(snapshot), fields: { dom: { canvas_count: canvasCount, manual_gate: snapshot.manualGate, forms } }, elements, text: texts.join('\n\n').slice(0,9000), ...(['extract', 'extract_tables'].includes(command.operation) ? { tables: tables.slice(0,6) } : {}) }
    }
    const snapshot = snapshots.get(contents)
    if (command.operation === 'scroll') {
      if (snapshot) snapshot.consumed = true
      await step(() => contents.executeJavaScriptInIsolatedWorld(1001, [{ code: `window.scrollBy(0,${command.arguments.dir === 'up' ? -700 : 700});true` }]))
      return { ...base, ok: true, outcome: 'executed' }
    }
    if (!['click', 'type', 'highlight'].includes(command.operation)) return { ...base, ok: false, outcome: 'blocked', error_kind: 'unsupported_command' }
    if (!snapshot || snapshot.page !== page || snapshot.id !== command.expected_snapshot_id) throw new Error('stale_snapshot')
    await step(() => validateSnapshot(snapshot, ctx, signal))
    if (isBrowserWrite(command.operation) && await step(() => currentManualGate(snapshot, ctx, signal))) throw new Error('manual_input_required')
    const ref = snapshot.refs[command.arguments.idx as number]
    if (!ref) throw new Error('stale_snapshot')
    const locator = ref.frame.locator(selector('target', snapshot, { index: ref.index }))
    armedFrame = ref.frame; armedSnapshot = snapshot
    if (command.operation === 'click') {
      await step(() => data(ref.frame, selector('trial', snapshot, { index: ref.index }), signal))
      await step(() => locator.click({ trial: true, timeout: 4000, signal }))
      await step(() => data(ref.frame, selector('end_trial', snapshot), signal))
    }
    await step(() => validateSnapshot(snapshot, ctx, signal))
    const armed = await step(() => data<{ tag: string; href?: string }>(ref.frame, selector('arm', snapshot, { index: ref.index, operation: command.operation, value: command.arguments.text, expires: Math.min(expires, Date.now() + 8000) }), signal))
    snapshot.consumed = true
    if (command.operation === 'highlight') {
      await step(() => locator.highlight())
      return { ...base, ok: true, outcome: 'executed' }
    }
    attempted = true
    if (command.operation === 'click' && armed.href) {
      // Preserve P0: an approved ordinary anchor navigates without dispatching its
      // untrusted onclick handler. Redirects never become a successful business result.
      const mainFrame = ref.frame === page.mainFrame()
      await step(() => loadBrowserURL(contents, armed.href!, signal, mainFrame ? undefined : url => ref.frame.goto(url, { waitUntil: 'domcontentloaded', timeout: 12000, signal })))
      if ((mainFrame ? contents.getURL() : ref.frame.url()) !== armed.href) return { ...base, url: contents.getURL(), ok: false, outcome: 'unknown', error_kind: 'navigation_redirected' }
      return { ...base, url: contents.getURL(), title: contents.getTitle(), ok: true, outcome: 'executed', interaction_kind: 'navigation' }
    }
    if (command.operation === 'click') await step(() => locator.click({ timeout: 4000, signal }))
    else if (armed.tag === 'select') await step(() => locator.selectOption({ value: String(command.arguments.text) }, { timeout: 4000, signal }))
    else await step(() => locator.fill(String(command.arguments.text), { timeout: 4000, signal }))
    const status = await step(() => data<{ prevented: boolean }>(ref.frame, selector('status', snapshot), signal))
    if (status.prevented) return { ...base, ok: false, outcome: 'unknown', error_kind: 'stale_snapshot' }
    if (command.operation === 'type') {
      const result = await step(() => data<{ connected: boolean; value: string }>(ref.frame, selector('value', snapshot, { index: ref.index }), signal))
      if (!result.connected || result.value !== command.arguments.text) return { ...base, ok: false, outcome: 'unknown', error_kind: 'input_not_applied' }
    }
    return { ...base, url: contents.getURL(), title: contents.getTitle(), ok: true, outcome: 'executed' }
  } catch (error) {
    return { command_id: command.command_id, tab_id: command.tab_id, ok: false, outcome: attempted ? 'unknown' : isBrowserWrite(command.operation) || errorKind(error) === 'stale_snapshot' ? 'blocked' : 'failed', error_kind: errorKind(error), error: errorKind(error) }
  } finally {
    // Cleanup remains allowed after cancellation: never leave a guard blocking human takeover.
    if (armedFrame && armedSnapshot) await data(armedFrame, selector('disarm', armedSnapshot), AbortSignal.timeout(1000)).catch(() => {})
  }
}

export async function captureScreenshot(contents: WebContents, command: BrowserCommand, ctx: BrowserDriverContext): Promise<Partial<BrowserObservation>> {
  if (command.operation !== 'screenshot' || ctx.owner !== JSON.stringify([command.browser_session_id, command.run_id])) throw new Error('invalid_command')
  const guarded = browserCommandGuard(validateBrowserCommand(command))
  if (guarded) throw new Error(guarded)
  const snapshot = snapshots.get(contents)
  if (!snapshot || snapshot.id !== command.expected_snapshot_id) throw new Error('stale_snapshot')
  const remaining = command.expires_at ? Date.parse(command.expires_at) - Date.now() : 8000
  const signal = AbortSignal.any([ctx.signal, AbortSignal.timeout(Math.max(1, Math.min(8000, remaining)))])
  await validateSnapshot(snapshot, ctx, signal)
  if (await currentManualGate(snapshot, ctx, signal)) return { command_id: command.command_id, tab_id: command.tab_id, ok: false, outcome: 'blocked', error_kind: 'manual_input_required' }
  const observe = () => contents.executeJavaScriptInIsolatedWorld(1001, [{ code: '({url:location.href,width:innerWidth,height:innerHeight,dpr:devicePixelRatio,x:scrollX,y:scrollY})' }]) as Promise<{url:string;width:number;height:number;dpr:number;x:number;y:number}>
  const before = await readBeforeDeadline(observe(), signal); ctx.check(); signal.throwIfAborted()
  const zoom = contents.getZoomFactor()
  const viewBounds = getBrowserTabBounds(command.tab_id!)
  if (!viewBounds || Math.abs(viewBounds.width - before.width * zoom) > 2 || Math.abs(viewBounds.height - before.height * zoom) > 2) throw new Error('invalid_screenshot_geometry')
  const css = { x: 0, y: 0, width: before.width, height: before.height }
  if (!Object.values(css).every(Number.isFinite) || css.x < 0 || css.y < 0 || css.width <= 0 || css.height <= 0 || css.x + css.width > before.width || css.y + css.height > before.height || before.width > 8192 || before.height > 8192 || before.width * before.height * before.dpr ** 2 > 16_000_000) throw new Error('invalid_screenshot_geometry')
  await readBeforeDeadline(contents.executeJavaScriptInIsolatedWorld(1001, [{ code: 'new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))' }]), signal); ctx.check(); signal.throwIfAborted()
  if (await currentManualGate(snapshot, ctx, signal)) return { command_id: command.command_id, tab_id: command.tab_id, ok: false, outcome: 'blocked', error_kind: 'manual_input_required' }
  const image = await readBeforeDeadline(contents.capturePage(), signal)
  ctx.check(); signal.throwIfAborted()
  const after = await readBeforeDeadline(observe(), signal); ctx.check(); signal.throwIfAborted()
  await validateSnapshot(snapshot, ctx, signal)
  if (JSON.stringify(before) !== JSON.stringify(after) || contents.getZoomFactor() !== zoom || JSON.stringify(getBrowserTabBounds(command.tab_id!)) !== JSON.stringify(viewBounds)) throw new Error('stale_screenshot_geometry')
  if (await currentManualGate(snapshot, ctx, signal)) return { command_id: command.command_id, tab_id: command.tab_id, ok: false, outcome: 'blocked', error_kind: 'manual_input_required' }
  const png = image.toPNG(), width = png.readUInt32BE(16), height = png.readUInt32BE(20)
  if (Math.abs(width - css.width * before.dpr) > 2 || Math.abs(height - css.height * before.dpr) > 2 || png.byteLength > 8_000_000) throw new Error('invalid_screenshot_geometry')
  const screenshot: ScreenshotEvidence = {
    screenshot_id: randomUUID(), snapshot_id: snapshot.id, url: before.url, captured_at: new Date().toISOString(),
    viewport: { width: before.width, height: before.height }, image: { width, height }, dpr: before.dpr, zoom,
    clip: css, scroll: { x: before.x, y: before.y },
    view_bounds: viewBounds,
    page_version: pageVersion(snapshot), data_url: `data:image/png;base64,${png.toString('base64')}`
  }
  return { command_id: command.command_id, tab_id: command.tab_id, ok: true, outcome: 'observed', snapshot_id: snapshot.id, page_version: screenshot.page_version, url: before.url, title: contents.getTitle(), screenshot }
}

export async function closeBrowserDriver(): Promise<void> {
  snapshots.clear()
  const current = browser
  browser = undefined
  if (current) await readBeforeDeadline(current.close(), AbortSignal.timeout(2000)).catch(() => {})
}
