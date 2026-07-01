// 自动找优惠 deal_finder（实惠感）。移植自 yoyu/infrastructure/tools/deal_finder.py。
// 券组合最优实付 + 识别"假低价"。诚实边界：模拟券池（演示），真实核销需平台风控授权。
import type { POISummary, DealRow } from '@shared/types'

interface Coupon {
  name: string
  kind: 'discount' | 'full_reduce' | 'groupbuy'
  value: number
  threshold?: number
  stackable?: boolean
  genuine: boolean
  need_credential?: string
}

const POLICY: Record<string, [string, number, string]> = {
  学生: ['学生票', 0.7, '学生证/校园卡'],
  老人: ['老年优待票', 0.5, '身份证/老年证'],
  儿童: ['儿童票', 0.5, '身高/年龄证明']
}

function mockCoupons(poi: POISummary, eligible: string[] = []): Coupon[] {
  const coupons: Coupon[] = []
  for (const p of (poi.products || []).slice(0, 2)) {
    if (p.price) coupons.push({ name: `团购:${p.name || '套餐'}`, kind: 'groupbuy', value: p.price, genuine: true })
  }
  const base = poi.price_per_person || 100
  coupons.push({ name: '满100减15', kind: 'full_reduce', value: 15, threshold: 100, stackable: true, genuine: true })
  coupons.push({ name: '新客85折', kind: 'discount', value: 0.85, stackable: false, genuine: true })
  const isAttraction = ['乐园', '景点', '馆', '公园', '展', '游'].some((k) => (poi.category + poi.tags.join('')).includes(k))
  for (const grp of eligible) {
    if (POLICY[grp] && (isAttraction || grp === '学生')) {
      const [nm, val, cred] = POLICY[grp]
      coupons.push({ name: nm, kind: 'discount', value: val, stackable: false, genuine: true, need_credential: cred })
    }
  }
  coupons.push({ name: '超值5折(限量)', kind: 'discount', value: 0.5, stackable: false, genuine: base < 80 })
  return coupons
}

function bestCombo(original: number, coupons: Coupon[]): Omit<DealRow, 'shop' | 'source'> {
  const usable = coupons.filter((c) => c.genuine)
  const credOf: Record<string, string> = {}
  usable.forEach((c) => c.need_credential && (credOf[c.name] = c.need_credential))
  let best = { original, final: original, saved: 0, used: [] as string[], reason: '无可用真实优惠，原价' }

  for (const c of usable) {
    if (c.kind === 'groupbuy' && c.value < best.final) {
      best = { original, final: Math.round(c.value), saved: original - Math.round(c.value), used: [c.name], reason: `团购套餐价直接替代原价(省¥${original - Math.round(c.value)})` }
    }
  }
  const full = usable.filter((c) => c.kind === 'full_reduce' && original >= (c.threshold || 0))
  const disc = usable.filter((c) => c.kind === 'discount')
  for (const fr of full.length ? full : [null]) {
    for (const dc of disc.length ? disc : [null]) {
      let price = original
      const used: string[] = []
      if (fr) {
        price -= fr.value
        used.push(fr.name)
      }
      if (dc) {
        price = price * dc.value
        used.push(dc.name)
      }
      price = Math.round(price)
      if (price < best.final) best = { original, final: price, saved: original - price, used, reason: `组合用券(省¥${original - price})` }
    }
  }
  const credentials = best.used.map((u) => credOf[u]).filter(Boolean)
  return { ...best, credentials }
}

export function findDeal(poi: POISummary, partySize = 1, eligible: string[] = []): DealRow {
  const original = (poi.price_per_person || 100) * Math.max(partySize, 1)
  const coupons = mockCoupons(poi, eligible)
  const combo = bestCombo(original, coupons)
  const fake = coupons.filter((c) => !c.genuine).map((c) => c.name)
  return { shop: poi.name, ...combo, filtered_fake: fake, source: 'simulated' }
}
