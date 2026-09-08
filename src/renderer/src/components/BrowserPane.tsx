import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { FAVORITES } from '../lib/favorites'
import { ArrowLeft, ArrowRight, RotateCw, Compass, ZoomIn, ZoomOut, Search, Star } from 'lucide-react'

export function BrowserPane(): JSX.Element {
  const tabs = useStore(state => state.tabs)
  const activeTabId = useStore(state => state.activeTabId)
  const request = useStore(state => state.browserIntent)
  const error = useStore(state => state.browserError)
  const visible = useStore(state => state.view === 'browser' && !state.settingsOpen && !state.historyOpen && !state.sidePanelOpen && !state.discoverOpen && !state.routeTarget && !state.sharePlan)
  const active = tabs.find(tab => tab.id === activeTabId)
  const content = useRef<HTMLDivElement>(null)
  const [address, setAddress] = useState('')
  useEffect(() => { setAddress(active?.url || '') }, [active?.url, activeTabId])

  useLayoutEffect(() => {
    const element = content.current
    if (!element) return
    let frame = 0, last = ''
    const sync = () => {
      frame = 0
      const rect = element.getBoundingClientRect()
      const overlay = [...document.querySelectorAll('dialog[open],[role="dialog"],[role="menu"]')].some(node => { const box = node.getBoundingClientRect(); return box.width > 0 && box.height > 0 })
      const value = { x: Math.max(0, rect.x), y: Math.max(0, rect.y), width: rect.width, height: rect.height, visible: visible && !overlay && !error && !!active && !active.error && rect.width > 0 && rect.height > 0 }
      const key = JSON.stringify(value)
      if (key !== last) { last = key; void window.plango.browser.layout(value).catch(() => {}) }
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(sync) }
    const observer = new ResizeObserver(schedule)
    observer.observe(element)
    const overlays = new MutationObserver(schedule)
    overlays.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'aria-hidden', 'style'] })
    window.addEventListener('resize', schedule)
    sync()
    return () => { observer.disconnect(); overlays.disconnect(); window.removeEventListener('resize', schedule); cancelAnimationFrame(frame); void window.plango.browser.layout({ x: 0, y: 0, width: 0, height: 0, visible: false }).catch(() => {}) }
  }, [visible, activeTabId, active?.error, error])

  const go = (url: string) => { void request(active ? { kind: 'navigate', id: active.id, url } : { kind: 'create', url }) }
  const action = (kind: 'back' | 'forward' | 'reload' | 'focus') => { if (active) void request({ kind, id: active.id }) }
  const zoom = (factor: number) => { if (active) void request({ kind: 'zoom', id: active.id, factor: Math.max(0.5, Math.min(2.5, Math.round(factor * 10) / 10)) }) }
  return <div className="flex-1 flex flex-col min-h-0 bg-neutral-50">
    <div className="h-10 shrink-0 flex items-center gap-2 px-2 bg-white border-b border-neutral-200">
      <button title="后退" className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active?.canGoBack} onClick={() => action('back')}><ArrowLeft size={16} /></button>
      <button title="前进" className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active?.canGoForward} onClick={() => action('forward')}><ArrowRight size={16} /></button>
      <button title="重新加载" className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => action('reload')}><RotateCw size={15} className={active?.loading ? 'animate-spin' : ''} /></button>
      <div className="flex-1 flex items-center h-7 px-3 bg-neutral-100 rounded-full focus-within:ring-2 ring-brand/40">
        <Search size={13} className="text-neutral-400 mr-1.5 shrink-0" />
        <input value={address} onChange={event => setAddress(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') go(address) }} placeholder="输入网址或搜索词，回车打开…" aria-label="浏览器网址或搜索词" className="flex-1 bg-transparent text-sm outline-none" />
      </div>
      <div className="flex items-center gap-0.5 ml-1">
        <button className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => zoom((active?.zoom || 1) - 0.1)} title="缩小"><ZoomOut size={15} /></button>
        <button className="px-1 text-xs text-neutral-500 tabular-nums min-w-[38px] hover:bg-neutral-100 rounded disabled:opacity-30" disabled={!active} onClick={() => zoom(1)} title="重置缩放">{Math.round((active?.zoom || 1) * 100)}%</button>
        <button className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => zoom((active?.zoom || 1) + 0.1)} title="放大"><ZoomIn size={15} /></button>
      </div>
    </div>
    <div className="h-8 shrink-0 flex items-center gap-1 px-2 bg-white border-b border-neutral-100 overflow-x-auto">
      <Star size={12} className="text-brand-ink shrink-0" />
      {FAVORITES.map(favorite => <button key={favorite.url} onClick={() => go(favorite.url)} className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-full text-[11px] text-neutral-600 hover:bg-brand/10 hover:text-brand-ink" title={favorite.url}><span>{favorite.emoji}</span>{favorite.label}</button>)}
    </div>
    <div ref={content} data-browser-viewport className="flex-1 relative min-h-0" onClick={() => action('focus')}>
      {!tabs.length && <div className="absolute inset-0 flex flex-col items-center justify-center gap-5 text-center px-8">
        <div className="w-14 h-14 rounded-2xl bg-brand/20 flex items-center justify-center"><Compass size={26} className="text-brand-ink" /></div>
        <div><div className="text-lg font-semibold">内置浏览器 · 登录你的真实账号</div><div className="text-sm text-neutral-500 mt-1 max-w-md">打开大众点评/美团并登录后，PlanGo会在这个真实浏览器中读取页面；登录态持久保存，需要时可直接接管。</div></div>
        <div className="flex flex-wrap gap-2 justify-center">{FAVORITES.map(favorite => <button key={favorite.url} onClick={() => go(favorite.url)} className="px-4 py-2 text-sm rounded-lg bg-white border border-neutral-200 hover:border-brand hover:bg-brand/5 shadow-sm"><span className="mr-1">{favorite.emoji}</span>{favorite.label}</button>)}</div>
      </div>}
      {(error || active?.error) && <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-neutral-50 p-6 text-sm"><p>{error || active?.error}</p>{active && <button className="rounded bg-brand px-4 py-2" onClick={() => action('reload')}>重新加载</button>}</div>}
      {active?.popup && !active.error && <div className="absolute inset-0 flex items-center justify-center text-sm text-neutral-500">此标签使用独立浏览器窗口，保持相同登录会话。<button className="ml-2 underline" onClick={() => action('focus')}>显示窗口</button></div>}
    </div>
  </div>
}
