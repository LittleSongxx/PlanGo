import { DialogShell } from './DialogShell'
import { useEffect, useRef, useState } from 'react'
import { loadAMap } from '../lib/amap'
import { cityCenter } from '../lib/cityCenter'
import type { RouteTarget } from '../store'
import { addRouteOverlays } from '../lib/routePath'
import { X, Car, Bus, Footprints, Navigation } from 'lucide-react'

type Mode = 'driving' | 'transit' | 'walking'

interface Stats {
  distanceText: string
  durationText: string
  extra: string // 费用/地铁线路
}

const MODES: { key: Mode; label: string; icon: JSX.Element }[] = [
  { key: 'driving', label: '驾车', icon: <Car size={14} /> },
  { key: 'transit', label: '公交地铁', icon: <Bus size={14} /> },
  { key: 'walking', label: '步行', icon: <Footprints size={14} /> }
]

// 页面内路线面板（仿 weplan openTravelSheet）：模式切换 + 内嵌高德地图画真实路线 + 距离/时长/地铁线路。
export function RouteSheet({ target, onClose }: { target: RouteTarget; onClose: () => void }): JSX.Element {
  const mapEl = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const routerRef = useRef<any>(null)
  const plannedRef = useRef<any[]>([])
  const [mode, setMode] = useState<Mode>(target.initialMode || 'driving')
  const [stats, setStats] = useState<Stats | null>(null)
  const [err, setErr] = useState('')
  const [fromPlan, setFromPlan] = useState(!!target.paths?.length)

  // 照抄 weplan originFallback：起点缺失时用城市中心兜底，永远能画路线（诚实标注估算）
  const hasRealOrigin = !!(target.origin && target.origin.includes(','))
  const coarseOrigin = !hasRealOrigin || ['city', 'district', 'unknown'].includes(target.originGranularity || 'unknown')
  const effectiveOrigin = hasRealOrigin ? target.origin! : cityCenter(target.city)
  const hasDest = !!(target.dest && target.dest.includes(','))

  useEffect(() => {
    let disposed = false
    setStats(null)
    setErr('')
    if (!hasDest) {
      setErr('这个地点缺少坐标，无法规划路线。')
      return
    }
    loadAMap()
      .then((AMap) => {
        if (disposed || !mapEl.current) return
        const o = effectiveOrigin.split(',').map(Number)
        const d = target.dest.split(',').map(Number)
        if (!mapRef.current) mapRef.current = new AMap.Map(mapEl.current, { zoom: 13, viewMode: '2D', center: o })
        const map = mapRef.current
        try {
          routerRef.current?.clear?.()
        } catch {
          /* ignore */
        }
        plannedRef.current.forEach((overlay) => {
          try { map.remove(overlay) } catch { /* ignore */ }
        })
        plannedRef.current = []
        const plannedMode = target.initialMode || 'driving'
        if (mode === plannedMode && (target.paths || []).length) {
          plannedRef.current = addRouteOverlays(AMap, map, target.paths || [])
          plannedRef.current.push(new AMap.Marker({ position: o, map, title: target.originName || '起点' }))
          plannedRef.current.push(new AMap.Marker({ position: d, map, title: target.destName }))
          try { map.setFitView(null, false, [40, 40, 40, 40]) } catch { /* ignore */ }
          const planned = target.planned
          setFromPlan(true)
          setStats({
            distanceText: planned?.distanceKm != null ? `${planned.distanceKm}km` : '—',
            durationText: planned?.durationMin != null ? `${planned.durationMin}分钟` : '—',
            extra: planned?.extra || (mode === 'transit' ? '公交地铁' : mode === 'walking' ? '步行' : '驾车'),
          })
          return
        }
        setFromPlan(false)
        const opt = { map, hideMarkers: false, autoFitView: true, panel: false }
        let router: any
        if (mode === 'walking' && AMap.Walking) router = new AMap.Walking(opt)
        else if (mode === 'transit' && AMap.Transfer) router = new AMap.Transfer({ ...opt, city: target.city })
        else router = new AMap.Driving(opt)
        routerRef.current = router
        router.search(o, d, (status: string, result: any) => {
          if (disposed) return
          if (status !== 'complete') {
            setErr('该模式暂无可用路线，换个模式看看')
            return
          }
          setStats(extractStats(mode, result))
        })
      })
      .catch(() => !disposed && setErr('地图加载失败'))
    return () => {
      disposed = true
      try {
        routerRef.current?.clear?.()
      } catch {
        /* ignore */
      }
    }
  }, [mode, target.dest, effectiveOrigin, target.city, hasDest, target.paths, target.initialMode, target.planned, target.originName, target.destName])

  useEffect(() => {
    return () => {
      try {
        mapRef.current?.destroy?.()
      } catch {
        /* ignore */
      }
      mapRef.current = null
    }
  }, [])

  const openExternalNav = (): void => {
    const [lng, lat] = target.dest.split(',')
    const navMode = { driving: 'car', walking: 'walk', transit: 'bus' }[mode]
    const from = target.origin ? `&from=${target.origin},${encodeURIComponent(target.originName || '设定起点')}` : ''
    const url = `https://uri.amap.com/navigation?to=${lng},${lat},${encodeURIComponent(target.destName)}${from}&mode=${navMode}&policy=1&src=plango&coordinate=gaode&callnative=1`
    window.plango.openExternal(url)
  }
  const hailTaxi = (): void => {
    // 只有打车才跳转（唤起高德打车）
    const [lng, lat] = target.dest.split(',')
    const url = `https://uri.amap.com/marker?position=${lng},${lat}&name=${encodeURIComponent(target.destName)}&src=plango&coordinate=gaode&callnative=1`
    window.plango.openExternal(url)
  }

  return (
    <DialogShell label={`路线 · ${target.destName}`} onClose={onClose} className="w-[720px]">
        <div className="plango-panel-header shrink-0 gap-3">
          <Navigation size={16} className="text-brand-ink" />
          <span className="font-semibold text-sm">怎么去 · {target.destName}</span>
          <button onClick={onClose} aria-label="关闭路线" className="ml-auto plango-icon-button">
            <X size={16} />
          </button>
        </div>

        <div className="flex gap-1.5 px-5 py-3 border-b border-[var(--line)]" role="tablist" aria-label="交通方式">
          {MODES.map((m) => (
            <button
              key={m.key}
              onClick={() => setMode(m.key)} role="tab" aria-selected={mode === m.key}
              className={`flex items-center gap-1 text-xs px-3 py-1.5 rounded-full border transition-colors ${
                mode === m.key ? 'border-brand bg-brand/10 text-brand-ink font-medium' : 'border-neutral-200 text-neutral-500 hover:border-neutral-300'
              }`}
            >
              {m.icon} {m.label}
            </button>
          ))}
        </div>

        <div ref={mapEl} className="w-full h-[340px] bg-[var(--surface-soft)]" />

        <div className="p-5 space-y-3 overflow-y-auto">
          {err ? (
            <div className="text-xs text-amber-600">{err}</div>
          ) : stats ? (
            <div className="flex items-stretch gap-2">
              <Stat v={stats.distanceText} l="距离" />
              <Stat v={stats.durationText} l="时长" />
              <Stat v={stats.extra || '—'} l={mode === 'transit' ? '线路/票价' : mode === 'walking' ? '方式' : '打车约'} />
            </div>
          ) : (
            <div className="text-xs text-neutral-400">正在规划路线…</div>
          )}
          {target.originName ? <div className="text-[11px] text-neutral-500">使用计划记录的起点：{target.originName}，不代表设备当前位置。</div> : coarseOrigin && (
            <div className="text-[11px] text-amber-600">起点按「{target.city}」地区参考点估算；可在设置中指定出发地址。</div>
          )}
          {fromPlan ? <div className="text-[11px] text-neutral-500">图上轨迹与草案同一条高德算路，不是地图插件另算的结果。</div>
            : <div className="text-[11px] text-amber-700" aria-live="polite">当前模式由地图另算，可能与草案文案不一致；切回草案交通方式可看同一条路线。</div>}
          {fromPlan && target.planned?.summary ? <p className="text-[11px] leading-5 text-neutral-600 whitespace-pre-wrap">{target.planned.summary.replace(/；/g, '\n')}</p> : null}
          <div className="text-[11px] text-neutral-400">路线结果来自高德，实际路况和费用以出发时为准。</div>
          <div className="flex gap-2">
            <button onClick={openExternalNav} className="flex-1 text-xs py-1.5 rounded-lg bg-brand text-brand-ink font-medium">🧭 高德实时导航</button>
            <button onClick={hailTaxi} className="flex-1 text-xs py-1.5 rounded-lg bg-neutral-100 hover:bg-neutral-200 border border-neutral-200">🚕 打车前往（跳转）</button>
          </div>
        </div>
    </DialogShell>
  )
}

function Stat({ v, l }: { v: string; l: string }): JSX.Element {
  return (
    <div className="flex-1 text-center rounded-xl bg-neutral-50 border border-neutral-100 py-2">
      <div className="text-sm font-bold text-brand-ink truncate px-1">{v}</div>
      <div className="text-[10px] text-neutral-400 mt-0.5">{l}</div>
    </div>
  )
}

function fmtDist(m?: number): string {
  if (!m) return '—'
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${Math.round(m)}m`
}
function fmtDur(s?: number): string {
  if (!s) return '—'
  if (s > 0 && s < 60) return '不足1分钟'
  const min = Math.round(s / 60)
  return min >= 60 ? `${Math.floor(min / 60)}h${min % 60}m` : `${min}分钟`
}

function extractStats(mode: Mode, result: any): Stats {
  if (mode === 'transit') {
    const p = result?.plans?.[0]
    const lines: string[] = []
    for (const seg of p?.segments || []) {
      const lname = seg?.transit?.lines?.[0]?.name || seg?.bus?.buslines?.[0]?.name
      if (lname) lines.push(String(lname).split('(')[0])
    }
    const fare = p?.cost != null ? `¥${p.cost}` : ''
    return { distanceText: fmtDist(p?.distance), durationText: fmtDur(p?.time), extra: [lines.slice(0, 3).join('→'), fare].filter(Boolean).join(' · ') || '公交' }
  }
  const r = result?.routes?.[0]
  if (mode === 'driving') {
    const taxi = result?.taxiCost != null ? `¥${Math.round(result.taxiCost)}` : ''
    return { distanceText: fmtDist(r?.distance), durationText: fmtDur(r?.time), extra: taxi }
  }
  return { distanceText: fmtDist(r?.distance), durationText: fmtDur(r?.time), extra: '步行' }
}
