// 企业可自定义 API（Adapter 模式，对齐美团 To-A + 扣子 OpenAPI 导入）。
// Agent 只认稳定的领域方法；底层数据源热插拔：Mock(VitaBench) / AmapLive(高德) / Enterprise(企业自有)。
import type { POISummary, RouteInfo, SourceTag } from '@shared/types'
import { getConfig } from '../config'
import { queryShops, type QueryOpts } from './mockdb'
import * as amap from './amap'
import { withHarness } from '../tools/harness'

export interface SearchQuery {
  city: string
  keywords?: string[]
  category?: string // 到餐/到综
  excludeTags?: string[]
  location?: string // lng,lat
  limit?: number
}

export interface IDataSourceAdapter {
  readonly id: SourceTag | 'enterprise'
  readonly label: string
  searchPoi(q: SearchQuery): Promise<{ data: POISummary[]; source: SourceTag }>
  getWeather(city: string): Promise<{ data: { text: string; temp: string } | null; source: SourceTag }>
  route(origin: string, destination: string, mode: RouteInfo['mode'], city?: string): Promise<{ data: RouteInfo | null; source: SourceTag }>
}

// —— Mock（VitaBench）——
export class MockAdapter implements IDataSourceAdapter {
  id = 'dataset' as const
  label = 'VitaBench 数据集（Mock）'
  async searchPoi(q: SearchQuery) {
    const opts: QueryOpts = { city: q.city, category: q.category, keywords: q.keywords, excludeTags: q.excludeTags, limit: q.limit }
    return { data: queryShops(opts), source: 'dataset' as SourceTag }
  }
  async getWeather(city: string) {
    // 数据集无实时天气：给一个稳定的"周末晴"占位，诚实标 simulated
    void city
    return { data: { text: '晴', temp: '24' }, source: 'simulated' as SourceTag }
  }
  async route(origin: string, destination: string, mode: RouteInfo['mode']) {
    void origin
    void destination
    // 无坐标真实路网：给按模式的经验估算，标 simulated
    const est: Record<RouteInfo['mode'], RouteInfo> = {
      walking: { mode, distance_m: 900, duration_min: 12, desc: '步行约 12 分钟 · 0.9km' },
      riding: { mode, distance_m: 1800, duration_min: 8, desc: '骑行约 8 分钟 · 1.8km' },
      transit: { mode, distance_m: 3500, duration_min: 18, desc: '公交约 18 分钟 · 3.5km' },
      driving: { mode, distance_m: 3500, duration_min: 14, taxi_cost: 18, desc: '驾车约 14 分钟 · 3.5km · 打车≈¥18' }
    }
    return { data: est[mode], source: 'simulated' as SourceTag }
  }
}

// —— AmapLive（高德，失败自动回退 Mock）——
export class AmapLiveAdapter implements IDataSourceAdapter {
  id = 'real' as const
  label = '高德实时（真实）'
  private fallback = new MockAdapter()
  async searchPoi(q: SearchQuery) {
    try {
      // 高德 place/text 是"短语匹配"，多个词空格拼接会检索到无关结果 → 逐词 OR 合并。
      const defaults = q.category === '到餐' ? ['美食', '餐厅'] : ['休闲娱乐', '景点', '亲子']
      const kws = (q.keywords && q.keywords.length ? q.keywords : defaults).slice(0, 3)
      const types = q.category === '到餐' ? '050000' : undefined // 050000=餐饮服务

      // 只有当"用户坐标确实落在目标城市"时才用周边搜索（附近、按距离排序，像 weplan/yoyu）；
      // 否则一律用 region+city_limit 的同城关键字搜索，避免跨城（北京+河南）串味。
      const coordsInCity = await this.coordsInCity(q.location, q.city)
      const center = coordsInCity ? q.location! : undefined

      const merged = new Map<string, POISummary>()
      for (const kw of kws) {
        try {
          const pois = center ? await amap.searchAround(center, kw, 5000, types) : await amap.searchPoi(kw, q.city, { types })
          for (const p of pois) if (!merged.has(p.poi_id)) merged.set(p.poi_id, p)
        } catch {
          /* 单个关键词失败不影响其它 */
        }
        if (merged.size >= (q.limit ?? 14) * 1.5) break
      }
      let data = [...merged.values()]
      // 兜底同城过滤：剔除城市对不上的（防跨城）
      if (q.city) {
        const cityKey = q.city.replace(/(省|市|自治区)$/, '')
        const inCity = data.filter((p) => !p.city || p.city.includes(cityKey) || cityKey.includes(p.city.replace(/市$/, '')))
        if (inCity.length) data = inCity
      }
      // 人群禁忌硬排除
      if (q.excludeTags?.length) {
        data = data.filter((p) => !q.excludeTags!.some((t) => `${p.name}${p.tags.join('')}`.includes(t)))
      }
      if (data.length) return { data: data.slice(0, q.limit ?? 14), source: 'real' as SourceTag }
      return this.fallback.searchPoi(q)
    } catch {
      return this.fallback.searchPoi(q)
    }
  }

  // 判断坐标是否落在目标城市（逆地理），避免用异地坐标做"附近"搜索
  private async coordsInCity(coords: string | undefined, city: string): Promise<boolean> {
    if (!coords || !city) return false
    try {
      const r = await amap.regeo(coords)
      if (!r?.city) return false
      const a = r.city.replace(/市$/, '')
      const b = city.replace(/(省|市|自治区)$/, '')
      return a.includes(b) || b.includes(a)
    } catch {
      return false
    }
  }
  async getWeather(city: string) {
    try {
      const w = await amap.weather(city)
      if (w) return { data: { text: w.text, temp: w.temp }, source: 'real' as SourceTag }
    } catch {
      /* fallthrough */
    }
    return this.fallback.getWeather(city)
  }
  async route(origin: string, destination: string, mode: RouteInfo['mode'], city?: string) {
    try {
      const r = await amap.route(origin, destination, mode, city)
      if (r) return { data: r, source: 'real' as SourceTag }
    } catch {
      /* fallthrough */
    }
    return this.fallback.route(origin, destination, mode)
  }
}

// —— Enterprise（企业自有 API / 美团 To-A / MT-Paotui；HTTP 直连，失败回退 Mock）——
export class EnterpriseAdapter implements IDataSourceAdapter {
  id = 'enterprise' as const
  label = '企业自有 API'
  private fallback = new MockAdapter()
  private async post(path: string, payload: unknown): Promise<any> {
    const { base, key } = getConfig().enterprise
    if (!base) throw new Error('未配置企业 API 地址')
    const res = await fetch(`${base.replace(/\/$/, '')}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
      body: JSON.stringify(payload)
    })
    if (!res.ok) throw new Error(`企业API ${res.status}`)
    return res.json()
  }
  async searchPoi(q: SearchQuery) {
    try {
      const r = await this.post('/poi/search', q)
      return { data: (r.pois || []) as POISummary[], source: 'real' as SourceTag }
    } catch {
      return this.fallback.searchPoi(q)
    }
  }
  async getWeather(city: string) {
    return this.fallback.getWeather(city)
  }
  async route(o: string, d: string, m: RouteInfo['mode']) {
    return this.fallback.route(o, d, m)
  }
}

let current: IDataSourceAdapter | null = null

export function getAdapter(): IDataSourceAdapter {
  const src = getConfig().dataSource
  // 允许运行时切换：源变化则重建
  if (!current || (src === 'mock' && current.id !== 'dataset') || (src === 'amap' && current.id !== 'real') || (src === 'enterprise' && current.id !== 'enterprise')) {
    current = src === 'amap' ? new AmapLiveAdapter() : src === 'enterprise' ? new EnterpriseAdapter() : new MockAdapter()
  }
  return current
}

// 供工具层调用：带 harness（超时/重试/降级 + 来源标注）
export async function searchPoiHarnessed(q: SearchQuery): Promise<{ data: POISummary[]; source: SourceTag }> {
  return withHarness('searchPoi', () => getAdapter().searchPoi(q), { data: [], source: 'fallback' })
}
