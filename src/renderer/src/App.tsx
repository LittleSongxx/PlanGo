import { useEffect, useRef } from 'react'
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
import { AiBrowsingBar } from './components/AiBrowsingBar'
import { detectViaAMap } from './lib/amap'
import { locationPriority, type LocationInfo } from '@shared/location'

export default function App(): JSX.Element {
  const addProactive = useStore((s) => s.addProactive)
  const setCity = useStore((s) => s.setCity)
  const setLocationInfo = useStore((s) => s.setLocationInfo)
  const view = useStore((s) => s.view)
  const chatWidth = useStore((s) => s.chatWidth)
  const setChatWidth = useStore((s) => s.setChatWidth)
  const dragging = useRef(false)
  useEffect(() => {
    installBrowserBridge()
    const unHarness = window.plango.onHarnessEvent((event) => useStore.getState().receiveHarnessEvent(event))
    void useStore.getState().hydrateHarness()
    const poll = window.setInterval(() => { void useStore.getState().refreshRun() }, 2000)
    const un3 = window.plango.onProactive((p) => addProactive(p))
    const un4 = window.plango.onImIncoming((m) => addProactive({ id: 'im' + m.ts, ts: m.ts, text: `【微信·${m.from}】${m.text}`, kind: 'im' }))
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
      window.clearInterval(poll)
      un3?.()
      un4?.()
      un5?.()
    }
  }, [addProactive, setCity, setLocationInfo])

  // 拖拽分隔条调整右侧对话宽度
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const w = window.innerWidth - e.clientX
      setChatWidth(w)
      e.preventDefault()
    }
    const onUp = () => {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [setChatWidth])

  return (
    <div className="h-full flex flex-col">
      {/* 顶栏（可拖动窗口） */}
      <header className="h-8 shrink-0 flex items-center bg-neutral-900 text-neutral-400 text-xs select-none" style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}>
        <div className="pl-20">PlanGo · AI 本地生活浏览器</div>
      </header>

      <div className="flex-1 flex min-h-0">
        <IconRail />

        {/* 中间主工作区：浏览器 / 成果区 切换 */}
        <section className="flex-1 flex flex-col min-w-0 bg-neutral-50 relative">
          <AiBrowsingBar />
          <div className={`flex-1 min-h-0 flex flex-col ${view === 'browser' ? '' : 'hidden'}`} aria-hidden={view !== 'browser'}>
            <TabBar />
            <BrowserPane />
          </div>
          {view === 'outcome' && <div className="flex-1 min-h-0"><OutcomeCanvas /></div>}
        </section>

        {/* 可拖拽分隔条 */}
        <div
          onMouseDown={() => {
            dragging.current = true
            document.body.style.cursor = 'col-resize'
            document.body.style.userSelect = 'none'
          }}
          className="w-1 shrink-0 cursor-col-resize bg-neutral-200 hover:bg-brand transition-colors"
          title="拖动调整宽度"
        />

        {/* 右侧PlanGo对话（宽度可调） */}
        <section className="shrink-0 flex flex-col min-w-0 bg-white" style={{ width: chatWidth }}>
          <ChatPanel />
        </section>
      </div>

      <SettingsDrawer />
      <HistoryDrawer />
      <SidePanel />
      <DiscoverPanel />
    </div>
  )
}
