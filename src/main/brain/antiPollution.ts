// 推荐抗污染（多阶段漏斗）。移植自 yoyu/infrastructure/tools/anti_pollution.py。
// 去水分(贝叶斯收缩) → 团伙降权 → 流行度去偏 → 软偏好加权 → 重排。诚实边界：POI 侧信号近似。
import type { POISummary } from '@shared/types'

const PRIOR_MEAN = 4.0
const PRIOR_WEIGHT = 8.0

function evidenceWeight(poi: POISummary): number {
  let v = 0
  v += Math.min(poi.products.length, 6) * 1.5
  v += Math.min(poi.tags.length, 6) * 0.6
  if (poi.enable_book || poi.enable_reservation) v += 2.0
  if (poi.address) v += 1.0
  if (poi.price_per_person) v += 1.0
  return Math.max(v, 0.5)
}

export function reviewTrust(poi: POISummary): POISummary {
  const r = poi.raw_score || 0
  const v = evidenceWeight(poi)
  let filtered = (r * v + PRIOR_MEAN * PRIOR_WEIGHT) / (v + PRIOR_WEIGHT)
  const suspicious = r >= 4.8 && v < 4.0
  if (suspicious) filtered -= 0.25

  if (v >= 6 && !suspicious) {
    poi.trust = 'high'
    poi.trust_reason = `证据充分(套餐${poi.products.length}/标签${poi.tags.length}/可预约)，评分可信`
  } else if (suspicious) {
    poi.trust = 'low'
    poi.trust_reason = '评分近满分但真实经营信号偏少，已去水分降权'
  } else {
    poi.trust = 'medium'
    poi.trust_reason = '证据中等，按贝叶斯收缩校准'
  }
  poi.filtered_score = Math.round(Math.max(filtered, 0) * 100) / 100
  return poi
}

function popularityDebias(pois: POISummary[]): void {
  for (const p of pois) {
    if ((p.raw_score ?? 0) >= 4.9 && evidenceWeight(p) < 4.0 && p.filtered_score != null) {
      p.filtered_score = Math.round((p.filtered_score - 0.1) * 100) / 100
    }
  }
}

function shillGroupFlag(pois: POISummary[]): void {
  for (const p of pois) {
    if (p.is_distraction) {
      p.trust = 'low'
      if (!p.trust_reason) p.trust_reason = '疑似干扰/刷量项，已降权'
      if (p.filtered_score != null) p.filtered_score = Math.round((p.filtered_score - 0.3) * 100) / 100
    }
  }
}

export function cleanAndRank(pois: POISummary[], softPrefer: string[] = []): POISummary[] {
  for (const p of pois) reviewTrust(p)
  shillGroupFlag(pois)
  popularityDebias(pois)

  const trustBonus: Record<string, number> = { high: 0.2, medium: 0, low: -0.4, unknown: -0.1 }
  return [...pois].sort((a, b) => rankKey(b) - rankKey(a))

  function rankKey(p: POISummary): number {
    const base = p.filtered_score ?? p.raw_score ?? 0
    let bonus = 0
    const blob = `${p.name} ${p.tags.join(' ')}`
    for (const kw of softPrefer) if (blob.includes(kw)) bonus += 0.15
    return base + bonus + (trustBonus[p.trust] ?? 0)
  }
}
