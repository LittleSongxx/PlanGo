// 附近发现 / 优惠发现 的纯数据计算（不 emit 卡片），供 Agent 工具与"独立窗口"IPC 复用。
import { getConfig } from './config'
import { getAdapter } from './data/adapters'
import { cleanAndRank } from './brain/antiPollution'
import { findDeal } from './brain/dealFinder'
import type { POISummary, SourceTag, DiscoverGroup, DealRow } from '@shared/types'

const SPECS: { label: string; emoji: string; keywords: string[]; category?: '到餐' | '到综' }[] = [
  { label: '附近美食', emoji: '🍜', keywords: ['美食', '餐厅'], category: '到餐' },
  { label: '玩乐去处', emoji: '🎡', keywords: ['休闲娱乐', '景点', '亲子'], category: '到综' },
  { label: '咖啡甜品', emoji: '☕', keywords: ['咖啡', '甜品'], category: '到餐' }
]

export interface DiscoverResult {
  city: string
  groups: DiscoverGroup[]
  source: SourceTag
}

// 以用户当前定位为圆心，拉"今日附近"美食/玩乐/咖啡热点（去水分榜单，按组）。
export async function computeDiscover(cityArg?: string): Promise<DiscoverResult> {
  const cfg = getConfig()
  const city = String(cityArg || cfg.city)
  const coords = cfg.coords || undefined
  const groups: DiscoverGroup[] = []
  let source: SourceTag = 'dataset'
  for (const s of SPECS) {
    try {
      const r = await getAdapter().searchPoi({ city, keywords: s.keywords, category: s.category, location: coords, limit: 8 })
      if (r.source === 'real') source = 'real'
      const items = cleanAndRank(r.data).slice(0, 4)
      if (items.length) groups.push({ label: s.label, emoji: s.emoji, items })
    } catch {
      /* 单组失败不影响其它 */
    }
  }
  return { city, groups, source }
}

export interface DealDiscoverItem {
  poi: POISummary
  deal: DealRow
}
export interface DealDiscoverResult {
  city: string
  items: DealDiscoverItem[]
  source: SourceTag
}

// 优惠发现：附近可团购/用券的商家，算券后到手价 + 省多少，按省得多排序（诚实标注模拟券池）。
export async function computeDeals(cityArg?: string): Promise<DealDiscoverResult> {
  const cfg = getConfig()
  const city = String(cityArg || cfg.city)
  const coords = cfg.coords || undefined
  let source: SourceTag = 'dataset'
  let pois: POISummary[] = []
  try {
    const r = await getAdapter().searchPoi({ city, keywords: ['美食', '餐厅'], category: '到餐', location: coords, limit: 12 })
    if (r.source === 'real') source = 'real'
    pois = cleanAndRank(r.data)
  } catch {
    /* ignore */
  }
  const items: DealDiscoverItem[] = pois
    .filter((p) => p.price_per_person)
    .slice(0, 8)
    .map((p) => ({ poi: p, deal: findDeal(p, 2) }))
    .filter((x) => x.deal.saved > 0)
    .sort((a, b) => b.deal.saved - a.deal.saved)
    .slice(0, 6)
  return { city, items, source: 'simulated' as SourceTag }
}
