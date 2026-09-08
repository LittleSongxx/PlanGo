// 附近发现直接读取高德实时供给。
import { getConfig } from './config'
import * as amap from './data/amap'
import type { SourceTag, DiscoverGroup } from '@shared/types'

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

// Desktop discovery reads the live API directly.
export async function computeLiveDiscover(cityArg?: string): Promise<DiscoverResult> {
  const cfg = getConfig()
  const city = cityArg || cfg.city
  if (!cfg.amap.key) throw new Error('请配置高德 Key，或在对话中读取当前商家网页。')
  const groups: DiscoverGroup[] = []
  for (const spec of SPECS) {
    const items = await amap.searchPoi(spec.keywords[0], city, spec.category === '到餐' ? { types: '050000' } : {})
    if (items.length) groups.push({ label: spec.label, emoji: spec.emoji, items: items.slice(0, 4) })
  }
  return { city, groups, source: 'real' }
}
