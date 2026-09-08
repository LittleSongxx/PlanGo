import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { registerWebview, setActiveWebview, setTabOpener } from '../lib/browserBridge'
import { browserUrl } from '@shared/browser'
import { FAVORITES } from '../lib/favorites'
import { ArrowLeft, ArrowRight, RotateCw, Compass, ZoomIn, ZoomOut, Search, Star } from 'lucide-react'

export function BrowserPane(): JSX.Element {
  const tabs = useStore((s) => s.tabs)
  const activeTabId = useStore((s) => s.activeTabId)
  const addTab = useStore((s) => s.addTab)
  const updateTab = useStore((s) => s.updateTab)
  const webviewRefs = useRef<Record<string, any>>({})
  const [addr, setAddr] = useState('')
  const [zoom, setZoom] = useState(1)

  useEffect(() => {
    setTabOpener((url) => addTab(url))
    return () => setTabOpener(null)
  }, [addTab])

  useEffect(() => {
    for (const id of Object.keys(webviewRefs.current)) {
      if (!tabs.some((tab) => tab.id === id)) delete webviewRefs.current[id]
    }
    const wv = activeTabId ? webviewRefs.current[activeTabId] : null
    setActiveWebview(wv || null, activeTabId || undefined)
    const t = tabs.find((x) => x.id === activeTabId)
    setAddr(t?.url || '')
    try {
      if (wv?.getZoomFactor) setZoom(wv.getZoomFactor())
    } catch {
      /* ignore */
    }
  }, [activeTabId, tabs])

  const active = webviewRefs.current[activeTabId || '']

  const applyZoom = (z: number) => {
    const next = Math.max(0.5, Math.min(2.5, Math.round(z * 10) / 10))
    setZoom(next)
    try {
      active?.setZoomFactor?.(next)
    } catch {
      /* ignore */
    }
  }

  const go = (raw: string) => {
    let url: string
    try { url = browserUrl(raw) } catch { return }
    if (activeTabId && active) active.loadURL(url)
    else addTab(url)
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-neutral-50">
      {/* 地址栏 */}
      <div className="h-10 shrink-0 flex items-center gap-2 px-2 bg-white border-b border-neutral-200">
        <button className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => active?.canGoBack?.() && active.goBack()}>
          <ArrowLeft size={16} />
        </button>
        <button className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => active?.canGoForward?.() && active.goForward()}>
          <ArrowRight size={16} />
        </button>
        <button className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => active?.reload?.()}>
          <RotateCw size={15} />
        </button>
        <div className="flex-1 flex items-center h-7 px-3 bg-neutral-100 rounded-full focus-within:ring-2 ring-brand/40">
          <Search size={13} className="text-neutral-400 mr-1.5 shrink-0" />
          <input
            value={addr}
            onChange={(e) => setAddr(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && go(addr)}
            placeholder="输入网址或搜索词，回车打开…"
            className="flex-1 bg-transparent text-sm outline-none"
          />
        </div>
        {/* 缩放控制 */}
        <div className="flex items-center gap-0.5 ml-1">
          <button className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => applyZoom(zoom - 0.1)} title="缩小">
            <ZoomOut size={15} />
          </button>
          <button className="px-1 text-xs text-neutral-500 tabular-nums min-w-[38px] hover:bg-neutral-100 rounded disabled:opacity-30" disabled={!active} onClick={() => applyZoom(1)} title="重置缩放">
            {Math.round(zoom * 100)}%
          </button>
          <button className="p-1.5 rounded hover:bg-neutral-100 disabled:opacity-30" disabled={!active} onClick={() => applyZoom(zoom + 0.1)} title="放大">
            <ZoomIn size={15} />
          </button>
        </div>
      </div>

      {/* 收藏夹栏：本地生活常用站点真实入口 */}
      <div className="h-8 shrink-0 flex items-center gap-1 px-2 bg-white border-b border-neutral-100 overflow-x-auto">
        <Star size={12} className="text-brand-ink shrink-0" />
        {FAVORITES.map((f) => (
          <button
            key={f.url}
            onClick={() => (activeTabId && active ? active.loadURL(f.url) : addTab(f.url))}
            className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-full text-[11px] text-neutral-600 hover:bg-brand/10 hover:text-brand-ink"
            title={f.url}
          >
            <span>{f.emoji}</span>
            {f.label}
          </button>
        ))}
      </div>

      {/* 内容 */}
      <div className="flex-1 relative min-h-0">
        {tabs.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-5 text-center px-8">
            <div className="w-14 h-14 rounded-2xl bg-brand/20 flex items-center justify-center">
              <Compass size={26} className="text-brand-ink" />
            </div>
            <div>
              <div className="text-lg font-semibold">内置浏览器 · 登录你的真实账号</div>
              <div className="text-sm text-neutral-500 mt-1 max-w-md">
                打开大众点评/美团并登录后，PlanGo就能读到真实门店、团购、菜单、排队信息（登录态持久保存）。也可以让PlanGo自己开页面。
              </div>
            </div>
            <div className="flex flex-wrap gap-2 justify-center">
              {FAVORITES.map((s) => (
                <button key={s.url} onClick={() => addTab(s.url)} className="px-4 py-2 text-sm rounded-lg bg-white border border-neutral-200 hover:border-brand hover:bg-brand/5 shadow-sm">
                  <span className="mr-1">{s.emoji}</span>
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {tabs.map((t) => (
          <webview
            key={t.id}
            ref={((el: any) => {
              registerWebview(t.id, el)
              if (!el) return
              if (el && webviewRefs.current[t.id] !== el) {
                webviewRefs.current[t.id] = el
                el.addEventListener('dom-ready', () => {
                  registerWebview(t.id, el)
                  if (useStore.getState().activeTabId === t.id) setActiveWebview(el, t.id)
                })
                el.addEventListener('page-title-updated', (e: any) => updateTab(t.id, { title: e.title }))
                el.addEventListener('did-navigate', (e: any) => updateTab(t.id, { url: e.url }))
                el.addEventListener('did-navigate-in-page', (e: any) => updateTab(t.id, { url: e.url }))
              }
            }) as any}
            src={t.url}
            partition="persist:plango"
            className="absolute inset-0 w-full h-full bg-white"
            style={{ display: activeTabId === t.id ? 'flex' : 'none' }}
          />
        ))}
      </div>
    </div>
  )
}
