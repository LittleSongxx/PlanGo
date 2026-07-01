// 渲染层浏览器执行器：维护"活动 webview"，接收主进程动作，用 executeJavaScript 执行后回执。
// DOM 蒸馏(set-of-marks) + Readability 正文提取 + Horsepower 式表格提取 + 可视化(彩色编号框+合成光标)。
import readabilitySrc from '@mozilla/readability/Readability.js?raw'
import { useStore } from '../store'

// 站点友好名（顶部浮条显示"小悠正在浏览 大众点评…"）
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

// 顶部浮条 + 自动切到浏览器视图（让用户"看着小悠操作"）。空闲一段时间后自动收起。
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

type WebviewEl = {
  executeJavaScript: (code: string) => Promise<unknown>
  loadURL: (url: string) => void
  getURL: () => string
  getTitle: () => string
}

let activeWebview: WebviewEl | null = null
export function setActiveWebview(wv: WebviewEl | null): void {
  activeWebview = wv
}

type TabOpener = (url: string) => void
let tabOpener: TabOpener | null = null
export function setTabOpener(fn: TabOpener): void {
  tabOpener = fn
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
function waitForActive(ms = 5000): Promise<void> {
  const start = Date.now()
  return new Promise((resolve) => {
    if (activeWebview) return resolve()
    const t = setInterval(() => {
      if (activeWebview || Date.now() - start > ms) {
        clearInterval(t)
        resolve()
      }
    }, 80)
  })
}

const DISTILL = `(function(){
  try {
    var sel='a,button,input,textarea,select,[role=button],[role=link],[role=tab],[onclick],[contenteditable=true]';
    var els=Array.prototype.slice.call(document.querySelectorAll(sel));
    var out=[];var i=0;
    for(var k=0;k<els.length;k++){
      var el=els[k];var r=el.getBoundingClientRect();
      if(r.width<3||r.height<3) continue;
      var st=window.getComputedStyle(el);
      if(st.visibility==='hidden'||st.display==='none'||st.opacity==='0') continue;
      el.setAttribute('data-ai-idx',String(i));
      var name=el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('name')||el.getAttribute('title')||'';
      var text=((el.innerText||el.value||'')+'').replace(/\\s+/g,' ').trim().slice(0,80);
      out.push({idx:i,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',name:name,text:text});
      i++; if(i>=180) break;
    }
    var body=(document.body?document.body.innerText:'').replace(/\\s+\\n/g,'\\n').slice(0,6000);
    return {url:location.href,title:document.title,elements:out,text:body};
  } catch(e){ return {error:String(e)}; }
})()`

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

function jsFindByIdx(idx: number): string {
  return `document.querySelector('[data-ai-idx="${idx}"]')`
}

// Set-of-Marks 可视化：给已打 data-ai-idx 的可交互元素画彩色编号框（角色配色），单个固定层承载，避免污染 DOM。
// 学习 Browser Use / Opticlick / 微软 SoM：按钮绿 / 链接蓝 / 输入紫 / 其它橙。
const SOM_DRAW = `(function(){
  try{
    var ID='__xy_som__';
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

const SOM_CLEAR = `(function(){var o=document.getElementById('__xy_som__');if(o)o.remove();var c=document.getElementById('__xy_cursor__');if(c)c.remove();return {ok:true};})()`

// 合成光标：移动到目标元素中心并做点击脉冲，让"AI 点击"肉眼可见（Manus 式）。
function cursorClickScript(idx: number): string {
  return `(function(){
    try{
      var el=document.querySelector('[data-ai-idx="${idx}"]'); if(!el) return {error:'no el'};
      el.scrollIntoView({block:'center',behavior:'instant'});
      var rc=el.getBoundingClientRect();
      var cx=rc.left+rc.width/2, cy=rc.top+rc.height/2;
      var cur=document.getElementById('__xy_cursor__');
      if(!cur){cur=document.createElement('div');cur.id='__xy_cursor__';
        cur.style.cssText='position:fixed;z-index:2147483647;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;background:rgba(255,184,0,.35);border:2px solid #ffb800;transition:left .5s ease,top .5s ease;pointer-events:none;left:'+(window.innerWidth/2)+'px;top:'+(window.innerHeight-40)+'px;';
        document.documentElement.appendChild(cur);}
      requestAnimationFrame(function(){cur.style.left=cx+'px';cur.style.top=cy+'px';});
      return {ok:true};
    }catch(e){return {ok:false,error:String(e)};}
  })()`
}

function normalizeUrl(raw: string): string {
  const url = String(raw ?? '').trim()
  if (!url) return ''
  if (/^https?:\/\//.test(url)) return url
  if (/\.[a-z]{2,}/i.test(url) && !/\s/.test(url)) return 'https://' + url
  return 'https://www.baidu.com/s?wd=' + encodeURIComponent(url)
}

async function exec(action: string, args: Record<string, unknown>): Promise<unknown> {
  if (action === 'navigate' || action === 'open_tab') {
    const url = normalizeUrl(String(args.url ?? ''))
    if (!url && action === 'navigate') return { error: '缺少 url' }
    // 自动切浏览器视图 + 顶部浮条，让用户"看着小悠打开网页"
    markBrowsing('打开', siteName(url))
    if (!activeWebview) {
      if (!tabOpener) return { error: '无法打开浏览器标签' }
      tabOpener(url || 'https://www.dianping.com/')
      await waitForActive()
      await delay(900)
    } else if (url) {
      activeWebview.loadURL(url)
      await delay(700)
    }
    return { ok: true, url: url || activeWebview?.getURL?.() }
  }

  const wv = activeWebview
  if (!wv) return { error: '当前没有打开的浏览器标签。请先在左侧开一个浏览器标签。' }
  try {
    switch (action) {
      case 'read_page': {
        markBrowsing('读取页面', siteName(wv.getURL?.() || ''))
        // 先蒸馏可交互元素（决定点哪里），再用 Readability 补一份干净正文（理解内容）
        const distilled = (await wv.executeJavaScript(DISTILL)) as Record<string, unknown>
        // Set-of-Marks 可视化：把可交互元素画成彩色编号框，让用户看见 AI"看到了什么"
        try {
          await wv.executeJavaScript(SOM_DRAW)
        } catch {
          /* 覆盖层失败不影响读取 */
        }
        let mainText = ''
        try {
          const rd = (await wv.executeJavaScript(READABILITY)) as { ok?: boolean; text?: string; title?: string }
          if (rd?.ok && rd.text) mainText = rd.text
        } catch {
          /* readability 失败则仅用蒸馏正文 */
        }
        if (mainText) distilled.text = mainText
        return distilled
      }
      case 'extract':
      case 'extract_tables': {
        // 正文优先 Readability（干净主正文），表格再用 DOM 抓
        const tablesRes = (await wv.executeJavaScript(EXTRACT_TABLES)) as Record<string, unknown>
        try {
          const rd = (await wv.executeJavaScript(READABILITY)) as { ok?: boolean; text?: string }
          if (rd?.ok && rd.text) tablesRes.text = rd.text
        } catch {
          /* keep DOM body text */
        }
        return tablesRes
      }
      case 'current':
        return { url: wv.getURL(), title: wv.getTitle() }
      case 'click': {
        const idx = Number(args.idx)
        markBrowsing('点击', siteName(wv.getURL?.() || ''))
        // 合成光标先移动到目标（肉眼可见），停顿再真正点击
        try {
          await wv.executeJavaScript(cursorClickScript(idx))
          await delay(650)
        } catch {
          /* 光标动画失败不影响点击 */
        }
        const code = `(function(){var el=${jsFindByIdx(idx)};if(!el)return {error:'未找到元素 ${idx}'};el.scrollIntoView({block:'center'});el.click();return {ok:true,text:((el.innerText||'')+'').slice(0,40)};})()`
        return await wv.executeJavaScript(code)
      }
      case 'type': {
        const idx = Number(args.idx)
        markBrowsing('填写', siteName(wv.getURL?.() || ''))
        try {
          await wv.executeJavaScript(cursorClickScript(idx))
          await delay(400)
        } catch {
          /* ignore */
        }
        const text = JSON.stringify(String(args.text ?? ''))
        const code = `(function(){var el=${jsFindByIdx(idx)};if(!el)return {error:'未找到输入框 ${idx}'};el.focus();try{el.value=${text};}catch(e){};if(el.isContentEditable){el.innerText=${text};}el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return {ok:true};})()`
        return await wv.executeJavaScript(code)
      }
      case 'scroll': {
        markBrowsing('翻页', siteName(wv.getURL?.() || ''))
        const dir = String(args.dir ?? 'down')
        const dy = dir === 'up' ? -700 : 700
        // 翻页后重绘 SoM 覆盖层（元素位置变了）
        const r = await wv.executeJavaScript(`(function(){window.scrollBy(0,${dy});return {ok:true,y:window.scrollY};})()`)
        try {
          await wv.executeJavaScript(SOM_DRAW)
        } catch {
          /* ignore */
        }
        return r
      }
      case 'clear_marks':
        return await wv.executeJavaScript(SOM_CLEAR)
      case 'highlight': {
        const idx = Number(args.idx)
        const code = `(function(){var el=${jsFindByIdx(idx)};if(!el)return {error:'no el'};el.style.outline='3px solid #ffd100';el.scrollIntoView({block:'center'});return {ok:true};})()`
        return await wv.executeJavaScript(code)
      }
      default:
        return { error: '未知浏览器动作：' + action }
    }
  } catch (e) {
    return { error: (e as Error).message }
  }
}

let installed = false
export function installBrowserBridge(): void {
  if (installed) return
  installed = true
  window.xiaonian.onBrowserExec(async ({ id, action, args }) => {
    const result = await exec(action, args ?? {})
    window.xiaonian.browserExecResult(id, result)
  })
}
