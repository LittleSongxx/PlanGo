import { useStore } from '../store'
import { Sparkles, Globe, LayoutList, Settings, MapPin, Compass, History, MessageCircle, Ticket } from 'lucide-react'

interface NavItem {
  id: string
  label: string
  icon: JSX.Element
  onClick: () => void
  active?: boolean
  badge?: number
}

export function IconRail(): JSX.Element {
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)
  const setSettings = useStore((s) => s.setSettings)
  const setHistoryOpen = useStore((s) => s.setHistoryOpen)
  const setSidePanelOpen = useStore((s) => s.setSidePanelOpen)
  const setDiscoverOpen = useStore((s) => s.setDiscoverOpen)
  const cards = useStore((s) => s.cards)
  const city = useStore((s) => s.city)
  const district = useStore((s) => s.district)
  const citySource = useStore((s) => s.citySource)
  const locAccuracy = useStore((s) => s.locAccuracy)

  const planCount = cards.filter((c) => c.kind === 'plan' || c.kind === 'plans').length
  const precise = citySource === 'gps' || citySource === 'amap-gps'
  const isIp = citySource === 'ip' || citySource === 'amap-ip'
  const accText = precise && locAccuracy ? `±${Math.round(locAccuracy)}m` : ''
  const locLabel = precise ? '精确' : isIp ? '区域级' : '城市级'
  const locColor = precise ? 'text-green-400' : isIp ? 'text-sky-400' : 'text-amber-400'

  const top: NavItem[] = [
    { id: 'browser', label: '浏览器', icon: <Globe size={20} />, onClick: () => setView('browser'), active: view === 'browser' },
    { id: 'outcome', label: '成果区', icon: <LayoutList size={20} />, onClick: () => setView('outcome'), active: view === 'outcome', badge: planCount || undefined }
  ]

  // 有价值的入口：附近发现/优惠发现是独立窗口(不再发死消息、不堆成果区)
  const mid: NavItem[] = [
    { id: 'discover', label: '附近发现', icon: <Compass size={20} />, onClick: () => setDiscoverOpen('discover') },
    { id: 'deals', label: '优惠发现', icon: <Ticket size={20} />, onClick: () => setDiscoverOpen('deals') },
    { id: 'history', label: '历史', icon: <History size={20} />, onClick: () => setHistoryOpen(true) },
    { id: 'plugins', label: '连接', icon: <MessageCircle size={20} />, onClick: () => setSidePanelOpen(true) }
  ]

  return (
    <aside className="w-[68px] shrink-0 flex flex-col items-center bg-neutral-900 text-neutral-300 py-3 gap-1 select-none">
      <div className="w-9 h-9 rounded-xl bg-brand flex items-center justify-center mb-2" title="小悠">
        <Sparkles size={18} className="text-brand-ink" />
      </div>

      {top.map((n) => (
        <RailButton key={n.id} item={n} />
      ))}

      <div className="w-8 h-px bg-neutral-700 my-2" />

      {mid.map((n) => (
        <RailButton key={n.id} item={n} />
      ))}

      <div className="mt-auto flex flex-col items-center gap-1">
        <button
          onClick={() => setSettings(true)}
          className="text-[10px] text-neutral-400 text-center leading-tight px-1 hover:text-brand"
          title={`当前定位：${city}${district ? '·' + district : ''}（${precise ? 'GPS精确' + (accText ? ' ' + accText : '') : isIp ? 'IP区域级' : '城市级'}）\n点击可在设置里重新定位/手动指定`}
        >
          <MapPin size={12} className={`inline mb-0.5 ${locColor}`} />
          <div className="truncate max-w-[56px]">{district || city}</div>
          <div className={`text-[8px] leading-none ${locColor}`}>{locLabel}</div>
        </button>
        <RailButton item={{ id: 'settings', label: '设置', icon: <Settings size={20} />, onClick: () => setSettings(true) }} />
      </div>
    </aside>
  )
}

function RailButton({ item }: { item: NavItem }): JSX.Element {
  return (
    <button
      onClick={item.onClick}
      title={item.label}
      className={`relative w-[52px] h-[52px] rounded-xl flex flex-col items-center justify-center gap-0.5 transition-colors ${
        item.active ? 'bg-brand text-brand-ink' : 'hover:bg-neutral-800 text-neutral-300'
      }`}
    >
      {item.icon}
      <span className="text-[10px] leading-none">{item.label}</span>
      {item.badge ? (
        <span className="absolute top-1 right-1 min-w-[15px] h-[15px] px-0.5 rounded-full bg-red-500 text-white text-[9px] flex items-center justify-center">{item.badge}</span>
      ) : null}
    </button>
  )
}
