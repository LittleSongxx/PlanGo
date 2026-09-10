import { useEffect, useRef, useState } from 'react'
import type { Plan } from '@shared/types'
import { useStore } from '../store'
import { loadAMap } from '../lib/amap'

interface Pt {
  lng: number
  lat: number
  title: string
  kind: 'start' | 'via' | 'end'
}

function haversine(lng1: number, lat1: number, lng2: number, lat2: number): number {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLng = ((lng2 - lng1) * Math.PI) / 180
  const a = Math.sin(dLat / 2) ** 2 + Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(a))
}

// 行程地图：编号 Marker + 按计划标注的直线连接；不二次调用高德算路。
export function PlanMap({ plan }: { plan: Plan }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const coords = useStore((s) => s.coords)
  const locationGranularity = useStore((s) => s.locationGranularity)
  const [err, setErr] = useState(false)
  const [estimated, setEstimated] = useState(false)

  useEffect(() => {
    let disposed = false
    setErr(false)
    setEstimated(false)
    const stops: Pt[] = []
    for (const n of plan.nodes) {
      if (n.poi?.lng && n.poi?.lat) stops.push({ lng: n.poi.lng, lat: n.poi.lat, title: n.title, kind: 'via' })
    }
    const pts: Pt[] = []
    // 用户当前位置作起点——仅当它离第一站不太远（同城，<60km），否则会画出跨城的荒谬长线
    const origin = plan.origin ? `${plan.origin.longitude},${plan.origin.latitude}` : coords
    if (origin && stops.length) {
      const [lng, lat] = origin.split(',').map(Number)
      if (!isNaN(lng) && !isNaN(lat)) {
        const dKm = haversine(lng, lat, stops[0].lng, stops[0].lat)
        if (plan.origin || dKm < 60) pts.push({ lng, lat, title: plan.origin ? `计划起点：${plan.origin.name}` : ['city', 'district', 'unknown'].includes(locationGranularity) ? '地区参考点' : '设定起点', kind: 'start' })
      }
    }
    pts.push(...stops)
    if (pts.length) pts[pts.length - 1].kind = pts.length > 1 ? 'end' : 'via'
    if (pts.length < 1) return

    loadAMap()
      .then((AMap) => {
        if (disposed || !ref.current) return
        const map = new AMap.Map(ref.current, { zoom: 12, center: [pts[0].lng, pts[0].lat], viewMode: '2D', resizeEnable: true })
        mapRef.current = map
        map.addControl && AMap.Scale && map.addControl(new AMap.Scale())

        let viaNo = 0
        pts.forEach((p) => {
          const isStart = p.kind === 'start'
          const isEnd = p.kind === 'end'
          const color = isStart ? '#8a5a00' : isEnd ? '#4b668c' : '#b47a3e'
          const label = isStart ? '起' : isEnd ? '终' : String(++viaNo)
          const marker = new AMap.Marker({
            position: [p.lng, p.lat],
            offset: new AMap.Pixel(-13, -30),
            content: `<div style="width:26px;height:26px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center"><span style="transform:rotate(45deg);color:#fff;font-size:12px;font-weight:700">${label}</span></div>`
          })
          marker.on('click', () => {
            const content = document.createElement('div')
            content.style.cssText = 'padding:4px 8px;font-size:13px;font-weight:700'
            content.textContent = p.title
            new AMap.InfoWindow({
              content,
              offset: new AMap.Pixel(0, -30)
            }).open(map, [p.lng, p.lat])
          })
          map.add(marker)
        })

        if (pts.length >= 2) {
          const stopNodes = plan.nodes.filter((node) => node.poi?.lng && node.poi?.lat)
          const hasOrigin = pts[0]?.kind === 'start'
          const segColors = ['#8a5a00', '#4b7696', '#a58254', '#857198', '#526779']
          let pending = false
          for (let i = 0; i < pts.length - 1; i++) {
            const a = pts[i]
            const b = pts[i + 1]
            const dest = stopNodes[hasOrigin ? i : i + 1] || stopNodes[i]
            const verified = dest?.distance_kind === 'route'
            if (!verified) pending = true
            const color = segColors[i % segColors.length]
            new AMap.Polyline({
              path: [[a.lng, a.lat], [b.lng, b.lat]],
              map,
              strokeColor: color,
              strokeStyle: 'dashed',
              strokeWeight: verified ? 5 : 4,
              strokeOpacity: verified ? 0.85 : 0.7,
            })
            const label = dest?.transport_summary || (dest?.distance_kind === 'straight_line_lower_bound'
              ? '直线下界 · 待核验'
              : dest?.distance_km != null
                ? `${dest.distance_km}km · 待核验`
                : '路线待核验')
            new AMap.Marker({
              position: [(a.lng + b.lng) / 2, (a.lat + b.lat) / 2],
              offset: new AMap.Pixel(-24, -10),
              content: `<div style="background:${color};color:#fff;font-size:10px;font-weight:700;padding:1px 6px;border-radius:9px;white-space:nowrap">${label}</div>`,
              zIndex: 70,
            }).setMap(map)
          }
          if (pending) setEstimated(true)
          try {
            map.setFitView(null, false, [40, 40, 40, 40])
          } catch {
            /* ignore */
          }
        }
        setTimeout(() => {
          try {
            map.setFitView(null, false, [40, 40, 40, 40])
          } catch {
            /* ignore */
          }
        }, 200)
      })
      .catch(() => {
        if (!disposed) setErr(true)
      })

    return () => {
      disposed = true
      try {
        mapRef.current?.destroy?.()
      } catch {
        /* ignore */
      }
      mapRef.current = null
    }
  }, [plan, coords, locationGranularity])

  const hasCoords = plan.nodes.some((n) => n.poi?.lng && n.poi?.lat)
  if (!hasCoords) return <></>
  if (err) {
    const last = [...plan.nodes].reverse().find((n) => n.poi?.lng && n.poi?.lat)
    const openHref =
      last?.poi?.lng && last.poi.lat
        ? `https://uri.amap.com/marker?position=${last.poi.lng},${last.poi.lat}&name=${encodeURIComponent(last.title)}&src=plango&coordinate=gaode`
        : ''
    return (
      <div className="w-full h-52 rounded-xl border border-neutral-200 mb-3 bg-neutral-50 flex flex-col items-center justify-center gap-2 text-sm text-neutral-500">
        <span>地图加载失败（网络/域名校验）</span>
        {openHref && (
          <button
            className="px-3 py-2 rounded-lg bg-brand text-brand-ink text-xs"
            onClick={() => window.plango.openExternal(openHref)}
          >
            在高德地图中打开
          </button>
        )}
      </div>
    )
  }
  return <div className="mb-4"><div ref={ref} aria-label="行程地图" className="w-full h-56 rounded-2xl overflow-hidden border border-[var(--line)] bg-[var(--surface-soft)]" />{estimated && <p className="text-[11px] text-amber-700 mt-2">虚线仅连接地点，实际道路与通行时间待核验。</p>}</div>
}
