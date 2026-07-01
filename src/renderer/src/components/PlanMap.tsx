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

// 行程地图（仿 weplan drawMap）：编号 Marker + AMap.Driving 途经点真实路网折线 + 自适应视野。
export function PlanMap({ plan }: { plan: Plan }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const coords = useStore((s) => s.coords)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let disposed = false
    setErr(false)
    const stops: Pt[] = []
    for (const n of plan.nodes) {
      if (n.poi?.lng && n.poi?.lat) stops.push({ lng: n.poi.lng, lat: n.poi.lat, title: n.title, kind: 'via' })
    }
    const pts: Pt[] = []
    // 用户当前位置作起点——仅当它离第一站不太远（同城，<60km），否则会画出跨城的荒谬长线
    if (coords && stops.length) {
      const [lng, lat] = coords.split(',').map(Number)
      if (!isNaN(lng) && !isNaN(lat)) {
        const dKm = haversine(lng, lat, stops[0].lng, stops[0].lat)
        if (dKm < 60) pts.push({ lng, lat, title: '我的位置', kind: 'start' })
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
          const color = isStart ? '#18b56a' : isEnd ? '#f0454b' : '#ff7a1a'
          const label = isStart ? '起' : isEnd ? '终' : String(++viaNo)
          const marker = new AMap.Marker({
            position: [p.lng, p.lat],
            offset: new AMap.Pixel(-13, -30),
            content: `<div style="width:26px;height:26px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center"><span style="transform:rotate(45deg);color:#fff;font-size:12px;font-weight:700">${label}</span></div>`
          })
          marker.on('click', () => {
            new AMap.InfoWindow({
              content: `<div style="padding:4px 8px;font-size:13px;font-weight:700">${p.title}</div>`,
              offset: new AMap.Pixel(0, -30)
            }).open(map, [p.lng, p.lat])
          })
          map.add(marker)
        })

        // 真实路网折线：逐段绘制（每段单独一条彩色线 + 中点里程标签），仿 weplan travel-map
        if (pts.length >= 2 && AMap.Driving) {
          const segColors = ['#f2520e', '#18b56a', '#2f6bff', '#a24bff', '#ff8a00']
          for (let i = 0; i < pts.length - 1; i++) {
            const a = pts[i]
            const b = pts[i + 1]
            const color = segColors[i % segColors.length]
            const driving = new AMap.Driving({ policy: (AMap.DrivingPolicy && AMap.DrivingPolicy.LEAST_TIME) || 0, showTraffic: false, hideMarkers: true, map: null })
            driving.search([a.lng, a.lat], [b.lng, b.lat], (status: string, result: any) => {
              if (disposed) return
              const route = status === 'complete' ? result?.routes?.[0] : null
              if (route) {
                const path: any[] = []
                for (const step of route.steps || []) for (const pt of step.path || []) path.push(pt)
                if (path.length) {
                  new AMap.Polyline({ path, map, strokeColor: color, strokeWeight: 6, strokeOpacity: 0.92, showDir: true, lineJoin: 'round', lineCap: 'round', zIndex: 60 })
                }
                // 段中点里程/时长标签
                const km = route.distance ? (route.distance / 1000).toFixed(1) : ''
                const min = route.time ? Math.round(route.time / 60) : ''
                if (km) {
                  const mid = path[Math.floor(path.length / 2)] || [a.lng, a.lat]
                  new AMap.Marker({
                    position: mid,
                    offset: new AMap.Pixel(-24, -10),
                    content: `<div style="background:${color};color:#fff;font-size:10px;font-weight:700;padding:1px 6px;border-radius:9px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.25)">${km}km${min ? ' · ' + min + '分' : ''}</div>`,
                    zIndex: 70
                  }).setMap(map)
                }
              } else {
                new AMap.Polyline({ path: [[a.lng, a.lat], [b.lng, b.lat]], map, strokeColor: color, strokeStyle: 'dashed', strokeWeight: 4, strokeOpacity: 0.7 })
              }
              try {
                map.setFitView(null, false, [40, 40, 40, 40])
              } catch {
                /* ignore */
              }
            })
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
  }, [plan, coords])

  const hasCoords = plan.nodes.some((n) => n.poi?.lng && n.poi?.lat)
  if (!hasCoords) return <></>
  if (err) {
    const last = [...plan.nodes].reverse().find((n) => n.poi?.lng && n.poi?.lat)
    const openHref =
      last?.poi?.lng && last.poi.lat
        ? `https://uri.amap.com/marker?position=${last.poi.lng},${last.poi.lat}&name=${encodeURIComponent(last.title)}&src=xiaonian&coordinate=gaode`
        : ''
    return (
      <div className="w-full h-52 rounded-xl border border-neutral-200 mb-3 bg-neutral-50 flex flex-col items-center justify-center gap-2 text-sm text-neutral-500">
        <span>地图加载失败（网络/域名校验）</span>
        {openHref && (
          <button
            className="px-3 py-1 rounded-lg bg-brand text-white text-xs"
            onClick={() => window.xiaonian.openExternal(openHref)}
          >
            在高德地图中打开
          </button>
        )}
      </div>
    )
  }
  return <div ref={ref} className="w-full h-52 rounded-xl overflow-hidden border border-neutral-200 mb-3 bg-neutral-100" />
}
