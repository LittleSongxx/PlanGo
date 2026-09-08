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

export default function App(): JSX.Element {
  const addProactive = useStore((s) => s.addProactive)
  const setCity = useStore((s) => s.setCity)
  const setLocationInfo = useStore((s) => s.setLocationInfo)
  const view = useStore((s) => s.view)
  const chatWidth = useStore((s) => s.chatWidth)
  const setChatWidth = useStore((s) => s.setChatWidth)
  const dragging = useRef(false)
  // 定位权威级别：0=无 / 1=IP区域级(高德/v3/ip rectangle 中心，桌面无GPS时这就是最准的) / 2=真实GPS
  const locLevel = useRef(0)

  useEffect(() => {
    installBrowserBridge()
    const unHarness = window.plango.onHarnessEvent((event) => useStore.getState().receiveHarnessEvent(event))
    void useStore.getState().hydrateHarness()
    const poll = window.setInterval(() => { void useStore.getState().refreshRun() }, 2000)
    const un3 = window.plango.onProactive((p) => addProactive(p))
    const un4 = window.plango.onImIncoming((m) => addProactive({ id: 'im' + m.ts, ts: m.ts, text: `【微信·${m.from}】${m.text}`, kind: 'im' }))
    // 主进程高德 /v3/ip 定位：出站走本机真实公网 IP，返回 rectangle 中心（区县级坐标）。
    // 桌面无 GPS 时这就是最佳圆心 —— 采纳其 city+coords（level 1），只让真实 GPS(level 2) 覆盖。
    const applyIp = (l: { city?: string; source?: string; coords?: string; district?: string }): void => {
      if (locLevel.current >= 2 || !l?.city) return
      locLevel.current = Math.max(locLevel.current, 1)
      setLocationInfo({ city: l.city, district: l.district, coords: l.coords, source: 'ip', accuracy: l.coords ? 3000 : undefined })
    }
    const un5 = window.plango.onLocation((l) => applyIp(l))
    window.plango.getLocation().then((l) => applyIp(l))
    // 主动召回：打开即"想起你"，用记忆生成开场（无记忆则不打扰）
    window.plango
      .memoryGreeting()
      .then((g) => {
        if (g?.text) addProactive({ id: 'recall' + Date.now(), ts: Date.now(), text: g.text, kind: 'recall' })
      })
      .catch(() => {})
    // 客户端高德 JS SDK：只有拿到"真实 GPS"（浏览器 GPS / AMap.Geolocation 有真实坐标+精度）才升级到 level 2；
    // CitySearch 只返回城市中心(城市级)，绝不用它覆盖上面的 IP 区域级坐标（这正是之前定位跳到"深圳市中心"的根因）。
    detectViaAMap()
      .then((loc) => {
        if (!loc) return
        const isRealGps = (loc.source === 'gps' || loc.source === 'amap-gps') && !!loc.coords
        if (isRealGps) {
          locLevel.current = 2
          setLocationInfo({ city: loc.city, district: loc.district, coords: loc.coords, source: loc.source, accuracy: loc.accuracy })
          window.plango.reportLocation({ city: loc.city, coords: loc.coords })
        } else if (locLevel.current === 0 && loc.city) {
          // IP 也失败时（无任何坐标），照抄 weplan：用 CitySearch 的城市名 + bounds 中心坐标兜底，
          // 起点至少有值能画路线（城市级，诚实标注）；有 IP 区域级(level1)时不覆盖。
          setLocationInfo({ city: loc.city, coords: loc.coords, source: 'amap-city', accuracy: loc.coords ? 8000 : undefined })
        }
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
