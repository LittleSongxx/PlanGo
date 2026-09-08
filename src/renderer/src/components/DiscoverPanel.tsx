import { useEffect, useState, useCallback } from 'react'
import { useStore } from '../store'
import { cityCenter } from '../lib/cityCenter'
import type { DiscoverGroup, POISummary, DealRow } from '@shared/types'
import { X, RefreshCw, Compass, Ticket, MapPin, Star, Navigation } from 'lucide-react'

const CAT_IMG: Record<string, string> = {
  dining: 'https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=400&q=70',
  activity: 'https://images.unsplash.com/photo-1533107862482-0e6974b06ec4?w=400&q=70'
}

// 独立窗口：附近发现 / 优惠发现。直连 IPC 拉数据，不经聊天、不堆成果区；可刷新/关闭。
export function DiscoverPanel(): JSX.Element | null {
  const open = useStore((s) => s.discoverOpen)
  const setOpen = useStore((s) => s.setDiscoverOpen)
  const city = useStore((s) => s.city)
  const coords = useStore((s) => s.coords)
  const send = useStore((s) => s.send)
  const openRoute = useStore((s) => s.openRoute)
  const [tab, setTab] = useState<'discover' | 'deals'>('discover')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const busy = useStore((s) => s.busy)
  const [groups, setGroups] = useState<DiscoverGroup[]>([])
  const [deals, setDeals] = useState<{ poi: POISummary; deal: DealRow }[]>([])

  useEffect(() => {
    if (open) setTab(open)
  }, [open])

  const load = useCallback(
    async (which: 'discover' | 'deals') => {
      setLoading(true)
      setError('')
      try {
        if (which === 'discover') {
          const r = await window.xiaonian.discoverFetch(city)
          setGroups(r.groups || [])
        } else {
          const r = await window.xiaonian.dealsFetch(city)
          setDeals(r.items || [])
        }
      } catch (e) {
        setError(String(e))
        if (which === 'discover') setGroups([])
        else setDeals([])
      } finally {
        setLoading(false)
      }
    },
    [city]
  )

  useEffect(() => {
    if (open) void load(tab)
  }, [open, tab, load])

  if (!open) return null

  const route = (p: POISummary): void => {
    if (p.lng && p.lat) openRoute({ origin: coords || cityCenter(city), dest: `${p.lng},${p.lat}`, destName: p.name, city })
    setOpen(false)
  }
  const plan = (p: POISummary): void => {
    void send(`就以「${p.name}」为中心，帮我排一套附近的周末方案`)
    setOpen(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setOpen(false)}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-[640px] max-w-[94vw] max-h-[86vh] overflow-hidden bg-white rounded-2xl shadow-2xl flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="h-12 shrink-0 flex items-center px-4 border-b border-neutral-200">
          <span className="font-semibold text-sm flex items-center gap-1.5">
            {tab === 'discover' ? <Compass size={16} className="text-brand-ink" /> : <Ticket size={16} className="text-brand-ink" />}
            {city} · {tab === 'discover' ? '附近发现' : '优惠发现'}
          </span>
          <button onClick={() => void load(tab)} title="刷新" className="ml-auto p-1.5 rounded hover:bg-neutral-100">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button onClick={() => setOpen(false)} className="p-1.5 rounded hover:bg-neutral-100">
            <X size={16} />
          </button>
        </div>

        <div className="flex gap-1 px-3 py-2 border-b border-neutral-100">
          <button onClick={() => setTab('discover')} className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium ${tab === 'discover' ? 'bg-brand text-brand-ink' : 'text-neutral-500 hover:bg-neutral-100'}`}>
            <Compass size={13} /> 附近发现
          </button>
          <button onClick={() => setTab('deals')} className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium ${tab === 'deals' ? 'bg-brand text-brand-ink' : 'text-neutral-500 hover:bg-neutral-100'}`}>
            <Ticket size={13} /> 优惠发现
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {error && <div role="alert" className="mb-3 text-xs text-amber-700 bg-amber-50 rounded-lg p-2">{error}</div>}
          {tab === 'deals' && <button disabled={busy} onClick={() => { setOpen(false); void send('读取当前浏览器页面的真实菜单和优惠，保留商品名称、价格、人数和使用条件，并展示原文证据。') }} className="mb-3 w-full py-2 rounded-lg bg-brand text-brand-ink text-xs disabled:opacity-40">读取当前页面真实优惠</button>}
          {loading ? (
            <div className="text-center text-neutral-400 text-sm py-12">正在扫描{tab === 'discover' ? '附近热点' : '附近优惠'}…</div>
          ) : tab === 'discover' ? (
            groups.length === 0 ? (
              <div className="text-center text-neutral-400 text-sm py-12">附近暂时没拉到热点，确认定位后刷新试试。</div>
            ) : (
              <div className="space-y-4">
                {groups.map((g) => (
                  <div key={g.label}>
                    <div className="text-xs font-semibold text-neutral-600 mb-1.5">
                      {g.emoji} {g.label}
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {g.items.map((p) => (
                        <PoiCard key={p.poi_id} p={p} groupLabel={g.label} onPlan={() => plan(p)} onRoute={() => route(p)} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : deals.length === 0 ? (
            <div className="text-center text-neutral-400 text-sm py-12">还没有经过核验的优惠。先在浏览器打开菜单或团购页面，再点击上方读取按钮。</div>
          ) : (
            <div className="space-y-2">
              <div className="text-[11px] text-neutral-400 mb-1">仅展示已获取实际价格和使用条件的优惠；是否可用以当前页面为准。</div>
              {deals.map(({ poi, deal }) => (
                <div key={poi.poi_id} className="rounded-xl border border-neutral-200 p-3 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{poi.name}</div>
                    <div className="text-[11px] text-neutral-500 mt-0.5">
                      原价¥{deal.original} → <span className="text-red-500 font-semibold">到手¥{deal.final}</span> · 省¥{deal.saved} · {deal.reason}
                    </div>
                    {deal.filtered_fake?.length ? <div className="text-[10px] text-neutral-400 mt-0.5">已识别假低价：{deal.filtered_fake.join('、')}</div> : null}
                  </div>
                  <button onClick={() => plan(poi)} className="shrink-0 text-[11px] px-2.5 py-1.5 rounded-lg bg-brand text-brand-ink font-medium">去规划</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function PoiCard({ p, groupLabel, onPlan, onRoute }: { p: POISummary; groupLabel: string; onPlan: () => void; onRoute: () => void }): JSX.Element {
  const img = p.image || (/咖啡|甜品|美食|餐/.test(groupLabel) ? CAT_IMG.dining : CAT_IMG.activity)
  return (
    <div className="rounded-xl border border-neutral-200 overflow-hidden bg-white">
      <div className="relative h-20 bg-neutral-100">
        <img src={img} alt={p.name} loading="lazy" className="w-full h-full object-cover" onError={(e) => (e.currentTarget.src = CAT_IMG.dining)} />
        {!p.image && <span className="absolute bottom-0 right-0 text-[8px] text-white bg-black/40 px-1 rounded-tl">示意图</span>}
      </div>
      <div className="p-2">
        <div className="text-xs font-medium truncate">{p.name}</div>
        <div className="text-[11px] text-neutral-500 mt-0.5 flex items-center gap-1.5 flex-wrap">
          {(p.filtered_score ?? p.raw_score) ? (
            <span className="text-amber-600 flex items-center gap-0.5">
              <Star size={9} />
              {p.filtered_score ?? p.raw_score}
            </span>
          ) : null}
          {p.price_per_person ? <span>¥{p.price_per_person}</span> : null}
          {p.distance_m ? (
            <span className="flex items-center gap-0.5">
              <MapPin size={9} />
              {(p.distance_m / 1000).toFixed(1)}km
            </span>
          ) : null}
        </div>
        <div className="mt-1.5 flex gap-1">
          <button onClick={onPlan} className="flex-1 text-[10px] py-1 rounded-lg bg-brand text-brand-ink font-medium">去规划</button>
          {p.lng && p.lat && (
            <button onClick={onRoute} className="px-2 text-[10px] py-1 rounded-lg bg-neutral-100 hover:bg-neutral-200 border border-neutral-200 flex items-center gap-0.5">
              <Navigation size={9} /> 路线
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
