// 高德 POI 转换为桌面统一的 POISummary。
import type { POISummary } from '@shared/types'

// 高德 POI → POISummary（兼容 v5 business 结构 + v3 biz_ext）
export interface AmapPoi {
  id: string
  name: string
  type?: string
  address?: string
  location?: string // "lng,lat"
  tel?: string
  cityname?: string
  adname?: string
  distance?: string | number
  photos?: { url: string }[]
  // v3
  biz_ext?: { rating?: string; cost?: string; open_time?: string }
  tag?: string
  // v5
  business?: { rating?: string; cost?: string; opentime_today?: string; opentime_week?: string; tag?: string; tel?: string; keytag?: string; rectag?: string }
}

// 高德在 extensions=all 时，缺失的字符串字段会返回空数组 []（而非 ''），
// 直接 .split 会导致整批 POI 转换失败。统一安全取字符串。
function s(v: unknown): string {
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return ''
  if (v == null) return ''
  return String(v)
}

export function fromAmap(p: AmapPoi): POISummary {
  const [lng, lat] = s(p.location).split(',').map(Number)
  const biz = p.business
  const ratingText = s(biz?.rating ?? p.biz_ext?.rating)
  const rating = ratingText && Number.isFinite(Number(ratingText)) ? Number(ratingText) : null
  const cost = Number(s(biz?.cost ?? p.biz_ext?.cost)) || undefined
  const hours = s(biz?.opentime_today || biz?.opentime_week || p.biz_ext?.open_time)
  const tagStr = s(biz?.tag ?? p.tag)
  // v5 keytag/rectag（如"亲子/停车场/包间"）也并入标签，供人群契合与理由
  const extra = [s(biz?.keytag), s(biz?.rectag)].filter(Boolean).join(';')
  // 高德 type 是"大类;中类;小类"（如 餐饮服务;快餐厅;麦当劳）。中类最适合做品类判别，
  // 全部并入 tags，保证下游"活动/餐饮归位"能识别（否则只留品牌名"麦当劳"会漏判）。
  const typeSegs = s(p.type).split(/[;,]/).map((t) => t.trim()).filter(Boolean)
  const tags = [...tagStr.split(/[;,、]/), ...extra.split(/[;,、]/), ...typeSegs].map((t) => t.trim()).filter(Boolean)
  // 特色标签（供 UI 展示"招牌/特色"）：优先 business.tag/keytag/rectag，剔除宽泛的行业大类词
  const GENERIC = /服务|场所|相关|其他|公司企业|地名地址/
  const recommended = Array.from(
    new Set([...tagStr.split(/[;,、]/), ...extra.split(/[;,、]/)].map((t) => t.trim()).filter((t) => t && t.length <= 8 && !GENERIC.test(t)))
  ).slice(0, 4)
  const dist = Number(p.distance)
  return {
    poi_id: s(p.id),
    name: s(p.name),
    category: typeSegs.length >= 2 ? typeSegs[1] : typeSegs.slice(-1)[0] || '本地生活',
    raw_score: rating,
    trust: 'unknown',
    trust_reason: '',
    address: s(p.address) || s(p.adname),
    city: s(p.cityname),
    lng: isNaN(lng) ? undefined : lng,
    lat: isNaN(lat) ? undefined : lat,
    price_per_person: cost,
    tags: Array.from(new Set(tags)),
    enable_book: false,
    enable_reservation: false,
    business_hours: hours,
    products: [],
    source: 'real',
    image: p.photos?.[0]?.url,
    images: (p.photos || []).map((ph) => ph.url).filter(Boolean).slice(0, 6),
    recommended,
    tel: s(biz?.tel || p.tel) || undefined,
    distance_m: isNaN(dist) ? undefined : dist,
    is_distraction: false
  }
}
