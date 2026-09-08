// 附近发现直接读取高德实时供给。
import type { HarnessClient } from './harnessClient'
import type { LocationInfo } from '../shared/location'
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
  scope: 'around' | 'city'
  observed_at: string
  expires_at: string
  cache_hit: boolean
}

// Each group uses the same captured location; no request can borrow a later city's coordinates.
export async function computeLiveDiscover(client: Pick<HarnessClient, 'request'>, location: LocationInfo, refresh = false): Promise<DiscoverResult> {
  const groups: DiscoverGroup[] = []
  const responses = []
  for (const spec of SPECS) {
    const result = await amap.searchPoi(client, spec.keywords[0], location, { ...(spec.category === '到餐' ? { types: '050000' } : {}), refresh })
    responses.push(result)
    if (result.items.length) groups.push({ label: result.scope === 'city' ? spec.label.replace('附近', '同城') : spec.label, emoji: spec.emoji, items: result.items.slice(0, 4) })
  }
  const result = { city: location.city, groups, source: 'amap' as const, scope: responses[0].scope,
    observed_at: responses.map(result => result.observed_at).sort()[0], expires_at: responses.map(result => result.expires_at).sort()[0], cache_hit: responses.some(result => result.cache_hit) }
  amap.requireFresh(result)
  return result
}
