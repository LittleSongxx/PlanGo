// 高德 Web 服务客户端（TS）：POI 周边/关键字、四模式路线、天气、地理编码。
// 无 key 时抛错，由 Adapter 层降级到 VitaBench。
import { getConfig } from '../config'
import type { POISummary, RouteInfo } from '@shared/types'
import { fromAmap, type AmapPoi } from './converter'

const BASE = 'https://restapi.amap.com/v3'
const BASE_V5 = 'https://restapi.amap.com/v5'

function key(): string {
  const k = getConfig().amap.key
  if (!k) throw new Error('未配置 AMAP_WEBSERVICE_KEY')
  return k
}

// 免费 key QPS 有限：全局串行 + 最小间隔，避免 CUQPS_HAS_EXCEEDED_THE_LIMIT（移植自 yoyu）。
let lastCall = 0
const MIN_INTERVAL = 220
let chain: Promise<void> = Promise.resolve()
async function throttle(): Promise<void> {
  const mine = chain.then(async () => {
    const wait = MIN_INTERVAL - (Date.now() - lastCall)
    if (wait > 0) await new Promise((r) => setTimeout(r, wait))
    lastCall = Date.now()
  })
  chain = mine.catch(() => {})
  return mine
}

const cache = new Map<string, any>()

async function get(path: string, params: Record<string, string | number | undefined>, v5 = false): Promise<any> {
  const qs = new URLSearchParams({ key: key() })
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') qs.set(k, String(v))
  const url = `${(v5 ? BASE_V5 : BASE)}${path}?${qs.toString()}`
  const ckey = url.replace(/[?&]key=[^&]*/, '')
  if (cache.has(ckey)) return cache.get(ckey)

  for (let attempt = 0; attempt < 3; attempt++) {
    await throttle()
    const controller = new AbortController()
    const t = setTimeout(() => controller.abort(), 12_000)
    try {
      const res = await fetch(url, { signal: controller.signal })
      if (!res.ok) throw new Error(`高德 ${res.status}`)
      const data = await res.json()
      if (data.status === '1') {
        cache.set(ckey, data)
        return data
      }
      const info = String(data.info || 'unknown')
      if (/CUQPS|QPS|BUSINESS/i.test(info) && attempt < 2) {
        await new Promise((r) => setTimeout(r, 350 * (attempt + 1)))
        continue
      }
      throw new Error(`高德错误：${info}`)
    } catch (e) {
      if (attempt < 2) {
        await new Promise((r) => setTimeout(r, 300))
        continue
      }
      throw e
    } finally {
      clearTimeout(t)
    }
  }
  throw new Error('高德重试仍失败')
}

// v5 关键字搜索（region + city_limit 锁定同城，business/photos 富字段）。移植 yoyu text_search。
export async function searchPoi(keywords: string, city: string, opts: { types?: string; page?: number } = {}): Promise<POISummary[]> {
  const data = await get('/place/text', {
    keywords,
    region: city,
    city_limit: 'true',
    page_size: 20,
    page_num: opts.page ?? 1,
    types: opts.types,
    show_fields: 'business,photos'
  }, true)
  return (data.pois || []).map((p: AmapPoi) => fromAmap(p))
}

// v5 周边搜索（按距离排序，真正"附近"）。移植 yoyu around。
export async function searchAround(location: string, keywords: string, radius = 5000, types?: string): Promise<POISummary[]> {
  const data = await get('/place/around', {
    location,
    keywords,
    types,
    radius,
    sortrule: 'distance',
    page_size: 20,
    show_fields: 'business,photos'
  }, true)
  return (data.pois || []).map((p: AmapPoi) => fromAmap(p))
}

export async function geocode(address: string, city?: string): Promise<{ lng: number; lat: number } | null> {
  const data = await get('/geocode/geo', { address, city })
  const loc = data.geocodes?.[0]?.location
  if (!loc) return null
  const [lng, lat] = String(loc).split(',').map(Number)
  return { lng, lat }
}

// 逆地理：坐标 → 城市/区（判断坐标是否在目标城市）。移植 yoyu regeo。
export async function regeo(location: string): Promise<{ city: string; district?: string; address?: string } | null> {
  try {
    const data = await get('/geocode/regeo', { location, extensions: 'base' })
    const comp = data.regeocode?.addressComponent
    if (!comp) return null
    let city = comp.city
    if (Array.isArray(city) || !city) city = comp.province
    if (Array.isArray(city)) city = ''
    return { city: String(city || ''), district: String(comp.district || ''), address: String(data.regeocode?.formatted_address || '') }
  } catch {
    return null
  }
}

// 城市中心坐标（geocode 城市名），带进程内缓存，用作无 GPS 时的搜索圆心兜底。
const cityCenterCache = new Map<string, string>()
export async function cityCenter(city: string): Promise<string | null> {
  if (!city) return null
  if (cityCenterCache.has(city)) return cityCenterCache.get(city)!
  try {
    const g = await geocode(city, city)
    if (g) {
      const c = `${g.lng},${g.lat}`
      cityCenterCache.set(city, c)
      return c
    }
  } catch {
    /* ignore */
  }
  return null
}

export async function weather(city: string): Promise<{ text: string; temp: string; wind?: string } | null> {
  const data = await get('/weather/weatherInfo', { city, extensions: 'base' })
  const live = data.lives?.[0]
  if (!live) return null
  return { text: live.weather, temp: live.temperature, wind: live.winddirection }
}

const MODE_PATH: Record<RouteInfo['mode'], string> = {
  walking: '/direction/walking',
  transit: '/direction/transit/integrated',
  driving: '/direction/driving',
  riding: '/direction/bicycling'
}

export async function route(origin: string, destination: string, mode: RouteInfo['mode'], city?: string): Promise<RouteInfo | null> {
  try {
    const params: Record<string, string | number | undefined> = { origin, destination }
    if (mode === 'transit') {
      params.city = city
      params.cityd = city
    }
    const data = await get(MODE_PATH[mode], params)
    let distance = 0
    let duration = 0
    if (mode === 'transit') {
      const t = data.route?.transits?.[0]
      distance = Number(t?.distance || 0)
      duration = Number(t?.duration || 0)
    } else {
      const p = data.route?.paths?.[0]
      distance = Number(p?.distance || 0)
      duration = Number(p?.duration || 0)
    }
    const durMin = Math.round(duration / 60)
    const taxi = mode === 'driving' ? Number(data.route?.taxi_cost || 0) : undefined
    return {
      mode,
      distance_m: distance,
      duration_min: durMin,
      taxi_cost: taxi,
      desc: `${modeLabel(mode)}约 ${durMin} 分钟 · ${(distance / 1000).toFixed(1)}km`
    }
  } catch {
    return null
  }
}

function modeLabel(m: RouteInfo['mode']): string {
  return { walking: '步行', transit: '公交', driving: '驾车', riding: '骑行' }[m]
}
