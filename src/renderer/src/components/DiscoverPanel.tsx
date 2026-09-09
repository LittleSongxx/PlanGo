import { useEffect, useState, useCallback, useRef } from 'react'
import { useStore } from '../store'
import type { DiscoverGroup, POISummary, DealRow } from '@shared/types'
import { X, RefreshCw, Compass, Ticket, MapPin, Star, Navigation } from 'lucide-react'
import { DialogShell } from './DialogShell'
import { PoiImage } from './PoiImage'

// 独立窗口：附近发现 / 优惠发现。直连 IPC 拉数据，不经聊天、不堆成果区；可刷新/关闭。
export function DiscoverPanel(): JSX.Element | null {
  const open = useStore((s) => s.discoverOpen)
  const setOpen = useStore((s) => s.setDiscoverOpen)
  const city = useStore((s) => s.city)
  const coords = useStore((s) => s.coords)
  const granularity = useStore((s) => s.locationGranularity)
  const detailSource = useStore((s) => s.citySource)
  const locationObservedAt = useStore((s) => s.locationObservedAt)
  const send = useStore((s) => s.send)
  const openRoute = useStore((s) => s.openRoute)
  const [tab, setTab] = useState<'discover' | 'deals'>('discover')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const busy = useStore((s) => s.busy)
  const [groups, setGroups] = useState<DiscoverGroup[]>([])
  const [deals, setDeals] = useState<{ poi: POISummary; deal: DealRow }[]>([])
  const [scope, setScope] = useState<'around' | 'city'>('city')
  const [freshness, setFreshness] = useState<{ observed_at: string; expires_at: string; cache_hit: boolean } | null>(null)
  const [expired, setExpired] = useState(false)
  const requestSeq = useRef(0)

  useEffect(() => {
    if (open) setTab(open)
  }, [open])

  const load = useCallback(
    async (which: 'discover' | 'deals', refresh = false) => {
      const sequence = ++requestSeq.current
      setLoading(true)
      setError('')
      try {
        if (which === 'discover') {
          const r = await window.plango.discoverFetch({ city, refresh })
          if (sequence !== requestSeq.current) return
          setGroups(r.groups || [])
          setScope(r.scope)
          setFreshness(r)
        } else {
          const r = await window.plango.dealsFetch(city)
          if (sequence !== requestSeq.current) return
          setDeals(r.items || [])
        }
      } catch (e) {
        if (sequence !== requestSeq.current) return
        setError(String(e))
        if (which === 'discover') setGroups([])
        else setDeals([])
      } finally {
        if (sequence === requestSeq.current) setLoading(false)
      }
    },
    [city, coords, granularity, detailSource, locationObservedAt]
  )

  useEffect(() => {
    if (open) void load(tab)
    return () => { requestSeq.current++ }
  }, [open, tab, load])

  useEffect(() => {
    setExpired(false)
    if (!freshness) return
    const remaining = Date.parse(freshness.expires_at) - Date.now()
    if (!(remaining > 0)) { setExpired(true); return }
    const timer = setTimeout(() => setExpired(true), remaining)
    return () => clearTimeout(timer)
  }, [freshness])

  if (!open) return null

  const route = (p: POISummary): void => {
    if (p.lng && p.lat) openRoute({ origin: coords || undefined, dest: `${p.lng},${p.lat}`, destName: p.name, city })
    setOpen(false)
  }
  const plan = (p: POISummary): void => {
    if (busy || (p.source === 'amap' && expired)) return
    const selected = p.source === 'amap' && p.lng !== undefined && p.lat !== undefined ? { poi_id: p.poi_id, name: p.name, address: p.address,
      longitude: p.lng, latitude: p.lat, source: 'amap' as const, ...(p.observed_at ? { observed_at: p.observed_at } : {}) } : undefined
    void send(`就以「${p.name}」${p.address ? `（${p.address}）` : ''}为中心，帮我排一套附近的周末方案`, undefined, selected)
    setOpen(false)
  }

  return (
    <DialogShell label="地点与优惠发现" onClose={() => setOpen(false)} className="w-[820px] h-[780px]">
        <div className="plango-panel-header shrink-0">
          <span className="font-semibold text-base flex items-center gap-2">
            {tab === 'discover' ? <Compass size={16} className="text-brand-ink" /> : <Ticket size={16} className="text-brand-ink" />}
            {city} · {tab === 'discover' ? scope === 'around' ? '周边发现 · 5公里' : '同城发现' : '优惠发现'}
          </span>
          <button onClick={() => void load(tab, true)} title="刷新" className="ml-auto plango-icon-button">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button onClick={() => setOpen(false)} aria-label="关闭地点与优惠发现" className="plango-icon-button">
            <X size={16} />
          </button>
        </div>

        <div className="flex gap-1 p-1 mx-5 mt-4 rounded-xl bg-[var(--surface-soft)]">
          <button onClick={() => setTab('discover')} className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium ${tab === 'discover' ? 'bg-brand text-brand-ink' : 'text-neutral-500 hover:bg-neutral-100'}`}>
            <Compass size={13} /> 地点发现
          </button>
          <button onClick={() => setTab('deals')} className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium ${tab === 'deals' ? 'bg-brand text-brand-ink' : 'text-neutral-500 hover:bg-neutral-100'}`}>
            <Ticket size={13} /> 优惠发现
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 bg-[var(--surface-soft)] mt-4">
          {error && <div role="alert" className="mb-3 text-xs text-amber-700 bg-amber-50 rounded-lg p-2">{error}</div>}
          {tab === 'discover' && freshness && <div className="mb-2 text-[11px] text-neutral-500">高德 · {new Date(freshness.observed_at).toLocaleString('zh-CN')}{freshness.cache_hit ? ' · 包含缓存结果' : ''}{expired ? ' · 信息已过期，请刷新后选择' : ''}</div>}
          {tab === 'deals' && <button disabled={busy} onClick={() => { setOpen(false); void send('读取当前浏览器页面的真实菜单和优惠，保留商品名称、价格、人数和使用条件，并展示原文证据。') }} className="mb-3 w-full py-2 rounded-lg bg-brand text-brand-ink text-xs disabled:opacity-40">读取当前页面真实优惠</button>}
          {loading ? (
            <div className="text-center text-neutral-400 text-sm py-12">正在读取{tab === 'discover' ? '地点信息' : '已观测优惠'}…</div>
          ) : tab === 'discover' ? (
            groups.length === 0 ? (
              <div className="flex flex-col items-center justify-center text-center py-20 px-6"><span className="flex h-16 w-16 items-center justify-center rounded-[22px] border border-[var(--line)] bg-white text-brand-strong"><Compass size={27} strokeWidth={1.5} /></span><h3 className="text-base font-semibold mt-5">换个范围，发现下一站</h3><p className="text-xs leading-6 text-[var(--muted)] max-w-xs mt-2">当前范围未返回地点，确认位置后刷新试试。</p><button onClick={() => void load(tab, true)} className="mt-5 plango-primary"><RefreshCw size={13} />重新查找</button></div>
            ) : (
              <div className="space-y-6">
                {groups.map((g) => (
                  <div key={g.label}>
                    <div className="text-xs font-semibold text-neutral-600 mb-1.5">
                      {g.emoji} {g.label}
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      {g.items.map((p) => (
                        <PoiCard key={p.poi_id} p={p} groupLabel={g.label} disabled={busy || expired} onPlan={() => plan(p)} onRoute={() => route(p)} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : deals.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center py-20 px-6"><span className="flex h-16 w-16 items-center justify-center rounded-[22px] border border-[var(--line)] bg-white text-brand-strong"><Ticket size={27} strokeWidth={1.5} /></span><h3 className="text-base font-semibold mt-5">从真实页面，找到合适的优惠</h3><p className="text-xs leading-6 text-[var(--muted)] max-w-xs mt-2">还没有已读取的优惠。先在浏览器打开菜单或团购页面，再点击上方读取按钮。</p></div>
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
    </DialogShell>
  )
}

function PoiCard({ p, groupLabel, disabled, onPlan, onRoute }: { p: POISummary; groupLabel: string; disabled: boolean; onPlan: () => void; onRoute: () => void }): JSX.Element {
  return (
    <div className="plango-card overflow-hidden">
      <PoiImage src={p.image} name={p.name} className="h-32 rounded-none" />
      <div className="p-3.5">
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
              {p.distance_m < 1000 ? `${Math.round(p.distance_m)} 米` : `${(p.distance_m / 1000).toFixed(1)} 公里`}
            </span>
          ) : null}
        </div>
        <div className="mt-1.5 flex gap-1">
          <button disabled={disabled} onClick={onPlan} className="flex-1 text-[10px] py-1 rounded-lg bg-brand text-brand-ink font-medium disabled:opacity-40">去规划</button>
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
