import { useEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { installBrowserBridge } from './lib/browserBridge'
import { BrowserPane } from './components/BrowserPane'
import { TabBar } from './components/TabBar'
import { ChatPanel } from './components/ChatPanel'
import { OutcomeCanvas } from './components/OutcomeCanvas'
import { SettingsDrawer } from './components/SettingsDrawer'
import { IconRail } from './components/IconRail'
import { HistoryDrawer } from './components/HistoryDrawer'
import { SidePanel } from './components/SidePanel'
import { DiscoverPanel } from './components/DiscoverPanel'
import { ShareModal } from './components/ShareModal'
import { RouteSheet } from './components/RouteSheet'
import { AiBrowsingBar } from './components/AiBrowsingBar'
import { detectViaAMap } from './lib/amap'
import { locationPriority, type LocationInfo } from '@shared/location'
import { ArrowRight, Circle, Globe2, LayoutDashboard } from 'lucide-react'

export default function App(): JSX.Element {
  const addProactive = useStore((s) => s.addProactive)
  const setCity = useStore((s) => s.setCity)
  const setLocationInfo = useStore((s) => s.setLocationInfo)
  const view = useStore((s) => s.view)
  const chatWidth = useStore((s) => s.chatWidth)
  const setChatWidth = useStore((s) => s.setChatWidth)
  const dragging = useRef(false)
  const workspace = useRef<HTMLDivElement>(null)
  const [resizing, setResizing] = useState(false)
  const [chatMax, setChatMax] = useState(760)
  const backendReady = useStore(state => state.backendReady)
  const backendError = useStore(state => state.backendError)
  const routeTarget = useStore(state => state.routeTarget)
  const closeRoute = useStore(state => state.closeRoute)
  useEffect(() => {
    const stopBrowser = installBrowserBridge()
    const unHarness = window.plango.onHarnessEvent((event) => useStore.getState().receiveHarnessEvent(event))
    void useStore.getState().hydrateHarness()
    const un3 = window.plango.onProactive((p) => addProactive(p))
    const applyLocation = (location: LocationInfo): void => {
      if ((!location?.city && !location?.coords) || locationPriority(location.source) < locationPriority(useStore.getState().citySource)) return
      setLocationInfo(location)
    }
    const un5 = window.plango.onLocation(applyLocation)
    window.plango.getLocation().then(applyLocation).catch(() => {})
    // 主动召回：打开即"想起你"，用记忆生成开场（无记忆则不打扰）
    window.plango
      .memoryGreeting()
      .then((g) => {
        if (g?.text) addProactive({ id: 'recall' + Date.now(), ts: Date.now(), text: g.text, kind: 'recall' })
      })
      .catch(() => {})
    // Preserve provenance and user choices when background location requests finish later.
    detectViaAMap()
      .then(async (location) => {
        if (!location || locationPriority(location.source) < locationPriority(useStore.getState().citySource)) return
        applyLocation(await window.plango.reportLocation({ ...location, city: location.city || useStore.getState().city }))
      })
      .catch(() => {})
    void window.plango.desktopReady()
    return () => {
      unHarness()
      stopBrowser()
      un3?.()
      un5?.()
    }
  }, [addProactive, setCity, setLocationInfo])

  // 拖拽分隔条调整右侧对话宽度
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const w = (workspace.current?.getBoundingClientRect().right || window.innerWidth) - e.clientX - 8
      setChatWidth(Math.min(chatMax, w))
      e.preventDefault()
    }
    const onUp = () => {
      dragging.current = false
      setResizing(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [setChatWidth, chatMax])

  useEffect(() => {
    if (!workspace.current) return
    const observe = new ResizeObserver(([entry]) => {
      const maximum = Math.max(320, Math.min(760, entry.contentRect.width - 456))
      setChatMax(maximum)
      if (useStore.getState().chatWidth > maximum) setChatWidth(maximum)
    })
    observe.observe(workspace.current)
    return () => observe.disconnect()
  }, [setChatWidth])

  return (
    <div className="h-full flex flex-col gap-4 p-4 bg-[var(--canvas)]">
      <header className="h-11 shrink-0 flex items-center justify-between gap-5 px-2 select-none" style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}>
        <div className="flex items-center gap-3"><span className="flex h-8 w-8 items-center justify-center rounded-xl bg-white border border-[var(--line)] text-brand-strong"><LayoutDashboard size={16} /></span><div><p className="plango-kicker">PLANGO WORKSPACE</p><p className="text-[13px] font-semibold mt-0.5">把想法，安排好。</p></div></div>
        <div className="hidden xl:flex items-center gap-3 text-[11px] text-[var(--muted)]" aria-label="工作流程">需求<ArrowRight size={12} />查资料<ArrowRight size={12} />方案与结果<ArrowRight size={12} />确认继续</div>
        <div role="status" className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] ${backendReady ? 'border-emerald-200 bg-white text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-800'}`}><Circle size={7} fill="currentColor" />{backendReady ? '运行服务已连接' : backendError ? '运行服务未连接' : '正在连接运行服务'}</div>
      </header>
      <div className="flex-1 flex min-h-0 gap-4">
        <IconRail />
        <div ref={workspace} className="flex-1 flex min-w-0 min-h-0">
          <main className="plango-surface flex-1 flex flex-col min-w-0 overflow-hidden relative" aria-label="主工作区">
            <div className="plango-panel-header relative shrink-0 h-[74px]">
              <div><div className="plango-kicker">{view === 'browser' ? 'BROWSER' : 'YOUR WORKSPACE'}</div><div className="mt-1 flex items-center gap-2 text-[15px] font-semibold">{view === 'browser' ? <Globe2 size={16} className="text-brand-strong" /> : <LayoutDashboard size={16} className="text-brand-strong" />}{view === 'browser' ? '浏览器' : '方案与结果'}</div></div>
              <AiBrowsingBar />
            </div>
            <div className={`flex-1 min-h-0 flex flex-col ${view === 'browser' ? '' : 'hidden'}`} aria-hidden={view !== 'browser'}>
              <TabBar />
              <BrowserPane />
            </div>
            {view === 'outcome' && <div className="flex-1 min-h-0"><OutcomeCanvas /></div>}
          </main>
          <div role="separator" aria-label="调整对话面板宽度" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={chatMax} aria-valuenow={Math.round(chatWidth)} tabIndex={0}
            onKeyDown={event => { const next = event.key === 'ArrowLeft' ? chatWidth + 20 : event.key === 'ArrowRight' ? chatWidth - 20 : event.key === 'Home' ? 320 : event.key === 'End' ? chatMax : null; if (next !== null) { event.preventDefault(); setChatWidth(Math.min(chatMax, next)) } }}
            onMouseDown={() => { dragging.current = true; setResizing(true); document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none' }}
            className="group w-4 shrink-0 cursor-col-resize flex items-center justify-center rounded-lg outline-offset-0" title="拖动调整宽度"><div className="h-10 w-1 rounded-full bg-[#d8d4ca] group-hover:bg-brand transition-colors" /></div>
          <aside className="plango-surface shrink-0 flex flex-col min-w-0 overflow-hidden" style={{ width: chatWidth }} aria-label="对话助手"><ChatPanel /></aside>
        </div>
      </div>
      {resizing && <div data-browser-overlay className="fixed inset-0 z-40 cursor-col-resize" />}
      <SettingsDrawer />
      <HistoryDrawer />
      <SidePanel />
      <DiscoverPanel />
      <ShareModal />
      {routeTarget && <RouteSheet target={routeTarget} onClose={closeRoute} />}
    </div>
  )
}
