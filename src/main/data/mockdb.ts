// VitaBench 到店 611 门店 → 内存 Mock DB。断网/无高德时的诚实兜底数据源（标 dataset）。
import { readFileSync, existsSync } from 'fs'
import { join } from 'path'
import type { POISummary } from '@shared/types'
import { fromVita, type RawVitaShop } from './converter'

let SHOPS: POISummary[] = []
let loaded = false

function candidatePaths(): string[] {
  const paths = [join(process.cwd(), 'data', 'shops.json'), join(process.cwd(), 'xiaonian', 'data', 'shops.json')]
  if (typeof __dirname !== 'undefined') {
    paths.push(join(__dirname, '..', '..', 'data', 'shops.json'), join(__dirname, '..', '..', '..', 'data', 'shops.json'))
  }
  return paths
}

export function loadMockDb(): POISummary[] {
  if (loaded) return SHOPS
  loaded = true
  for (const p of candidatePaths()) {
    if (existsSync(p)) {
      try {
        const raw = JSON.parse(readFileSync(p, 'utf-8')) as RawVitaShop[]
        SHOPS = raw.map((r) => fromVita(r, 'dataset'))
        return SHOPS
      } catch (e) {
        console.error('[mockdb] 解析失败：', (e as Error).message)
      }
    }
  }
  console.warn('[mockdb] 未找到 shops.json，使用内置极简兜底')
  SHOPS = FALLBACK
  return SHOPS
}

export interface QueryOpts {
  city?: string
  category?: string // 到餐 / 到综
  keywords?: string[]
  excludeTags?: string[]
  limit?: number
}

export function queryShops(opts: QueryOpts = {}): POISummary[] {
  const all = loadMockDb()
  let list = all
  if (opts.city && opts.city !== '本地') {
    const inCity = all.filter((s) => (s.city || '').includes(opts.city!) || (s.address || '').includes(opts.city!))
    list = inCity.length >= 3 ? inCity : all // 城市数据太少则回退全量
  }
  if (opts.category) list = list.filter((s) => s.category === opts.category)
  if (opts.excludeTags?.length) {
    list = list.filter((s) => {
      const blob = s.name + s.category + s.tags.join('')
      return !opts.excludeTags!.some((t) => blob.includes(t))
    })
  }
  if (opts.keywords?.length) {
    const scored = list.map((s) => {
      const blob = s.name + s.category + s.tags.join('') + s.recommended.join('')
      const hit = opts.keywords!.reduce((n, kw) => (blob.includes(kw) ? n + 1 : n), 0)
      return { s, hit }
    })
    const anyHit = scored.some((x) => x.hit > 0)
    if (anyHit) list = scored.sort((a, b) => b.hit - a.hit).map((x) => x.s)
  }
  const lim = opts.limit ?? 12
  return list.slice(0, lim)
}

export function getShopById(id: string): POISummary | undefined {
  return loadMockDb().find((s) => s.poi_id === id)
}

export function findShopByName(name: string): POISummary | undefined {
  const all = loadMockDb()
  return all.find((s) => s.name === name) || all.find((s) => s.name.includes(name) || name.includes(s.name))
}

export function citiesWithData(): { city: string; count: number }[] {
  const map = new Map<string, number>()
  for (const s of loadMockDb()) {
    const c = s.city || '本地'
    map.set(c, (map.get(c) || 0) + 1)
  }
  return Array.from(map.entries())
    .map(([city, count]) => ({ city, count }))
    .sort((a, b) => b.count - a.count)
}

const FALLBACK: POISummary[] = [
  {
    poi_id: 'FB001',
    name: '示例·亲子餐厅',
    category: '到餐',
    raw_score: 4.6,
    trust: 'unknown',
    trust_reason: '',
    address: '本地·示范路1号',
    city: '本地',
    price_per_person: 90,
    tags: ['亲子友好', '儿童餐', '宽敞'],
    enable_book: true,
    enable_reservation: true,
    business_hours: '10:00-22:00',
    products: [{ name: '双人套餐', price: 188 }],
    source: 'dataset',
    recommended: ['儿童餐', '亲子友好'],
    is_distraction: false
  }
]
