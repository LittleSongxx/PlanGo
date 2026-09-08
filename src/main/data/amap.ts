// 高德 Web 服务客户端：桌面附近发现的实时 POI 搜索。
import { getConfig } from '../config'
import type { POISummary } from '@shared/types'
import { fromAmap, type AmapPoi } from './converter'

const BASE = 'https://restapi.amap.com/v5'

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

async function get(path: string, params: Record<string, string | number | undefined>): Promise<any> {
  const qs = new URLSearchParams({ key: key() })
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') qs.set(k, String(v))
  const url = `${BASE}${path}?${qs.toString()}`
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
  })
  return (data.pois || []).map((p: AmapPoi) => fromAmap(p))
}
