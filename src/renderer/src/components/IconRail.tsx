import { useStore } from '../store'
import { Route, Globe, LayoutList, Settings, MapPin, Compass, History, MessageCircle, Ticket, Plus, ArrowUpRight } from 'lucide-react'
import { locationLabel } from '@shared/location'

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
  const newSession = useStore(state => state.newSession)
  const city = useStore((s) => s.city)
  const district = useStore((s) => s.district)
  const citySource = useStore((s) => s.citySource)
  const locAccuracy = useStore((s) => s.locAccuracy)
  const granularity = useStore((s) => s.locationGranularity)

  const planCount = cards.filter((c) => c.kind === 'plan' || c.kind === 'plans').length
  const locLabel = locationLabel(citySource, locAccuracy, granularity)

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
    <aside className="w-[176px] shrink-0 flex flex-col min-h-0 overflow-y-auto select-none" aria-label="主导航">
      <div className="flex items-center gap-2.5 px-2 pt-3 pb-6" title="PlanGo"><span className="w-10 h-10 rounded-[14px] bg-brand-strong text-white flex items-center justify-center shadow-card"><Route size={21} strokeWidth={2} /></span><div><span className="text-[23px] tracking-[-0.8px] font-bold text-brand-ink">PlanGo</span><p className="text-[10px] text-[var(--muted)] mt-0.5">生活，有计划地出发</p></div></div>
      <button onClick={newSession} className="plango-primary w-full mb-6" title="开始新安排"><Plus size={16} />开始新安排</button>
      <div className="plango-kicker px-3 mb-2">工作台</div>
      <nav className="space-y-1">{top.map(item => <RailButton key={item.id} item={item} />)}</nav>
      <div className="plango-kicker px-3 mt-7 mb-2">发现与管理</div>
      <nav className="space-y-1">{mid.map(item => <RailButton key={item.id} item={item} />)}</nav>
      <div className="mt-auto pt-5 space-y-2">
        <button onClick={() => setSettings(true)} className="w-full rounded-2xl border border-[var(--line)] bg-white/70 p-3 text-left hover:bg-white transition-colors" title={`当前定位：${city}${district ? '·' + district : ''}（${locLabel}）\n点击可在设置里重新定位/手动指定`}>
          <div className="flex items-center gap-1.5 text-[10px] text-[var(--muted)]"><MapPin size={12} />当前地区<ArrowUpRight size={11} className="ml-auto" /></div>
          <div className="mt-2 text-sm font-semibold truncate">{city}{district ? ` · ${district}` : ''}</div><div className="text-[10px] text-[var(--muted)] mt-1 truncate">{locLabel}</div>
        </button>
        <RailButton item={{ id: 'settings', label: '设置', icon: <Settings size={18} />, onClick: () => setSettings(true) }} />
      </div>
    </aside>
  )
}

function RailButton({ item }: { item: NavItem }): JSX.Element {
  return <button onClick={item.onClick} title={item.label} aria-label={item.label} aria-current={item.active ? 'page' : undefined}
    className={`relative w-full h-11 rounded-xl flex items-center gap-3 px-3 transition-colors text-[13px] ${item.active ? 'bg-white text-brand-strong font-semibold shadow-card border border-[#d8e6dc]' : 'border border-transparent text-[#617269] hover:bg-white/70 hover:text-brand-ink'}`}>
    {item.icon}<span>{item.id === 'outcome' ? '方案与结果' : item.id === 'plugins' ? '连接与能力' : item.label}</span>{item.badge ? <span className="ml-auto rounded-md bg-brand-soft text-brand-strong px-1.5 py-0.5 text-[10px] tabular-nums">{item.badge}</span> : null}
  </button>
}
