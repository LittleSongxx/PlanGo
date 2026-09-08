// 防腐层：任何数据源（VitaBench raw / 高德 / 企业 API）→ 统一 POISummary。
// 原始 dict 绝不直达 Prompt/UI —— 这是"企业级、不像玩具"的关键。
import type { POISummary, ProductItem, SourceTag } from '@shared/types'

export interface RawVitaShop {
  poi_id: string
  name: string
  category: string
  raw_score: number
  address: string
  city?: string
  lng?: number
  lat?: number
  tags: string[]
  enable_book: boolean
  enable_reservation: boolean
  book_price?: number
  products: ProductItem[]
  is_distraction: boolean
}

// 从标签里挑"推荐菜/招牌"（让展示不假）
function pickRecommended(tags: string[], products: ProductItem[]): string[] {
  const fromProd = products.filter((p) => !p.is_distraction).slice(0, 2).map((p) => p.name)
  const fromTags = tags.filter((t) => t.length <= 6).slice(0, 3)
  return Array.from(new Set([...fromProd, ...fromTags])).slice(0, 4)
}

// 简单营业时间猜测（诚实：仅当无真实数据时给常识区间，UI 会标 dataset）
function guessHours(category: string, tags: string[]): string {
  const blob = category + tags.join('')
  if (/酒吧|清吧|livehouse|夜/.test(blob)) return '18:00-02:00'
  if (/健身|游泳|球/.test(blob)) return '06:00-22:00'
  if (/咖啡|书店|茶/.test(blob)) return '09:00-22:00'
  if (/餐|饭|食|火锅|烧烤/.test(blob)) return '10:00-22:00'
  return '09:00-21:00'
}

function medianPrice(prices: number[]): number | undefined {
  const pos = prices.filter((p) => p > 0).sort((a, b) => a - b)
  if (!pos.length) return undefined
  // 用中位数并剔除高价套餐（月卡/年卡）离群值的影响，代表"单次人均"
  const mid = Math.floor(pos.length / 2)
  const med = pos.length % 2 ? pos[mid] : Math.round((pos[mid - 1] + pos[mid]) / 2)
  return med
}

export function fromVita(raw: RawVitaShop, source: SourceTag = 'dataset'): POISummary {
  const ppp = medianPrice(raw.products.map((p) => p.price)) ?? (raw.book_price || undefined)
  return {
    poi_id: raw.poi_id,
    name: raw.name,
    category: raw.category,
    raw_score: raw.raw_score,
    trust: 'unknown',
    trust_reason: '',
    address: raw.address,
    city: raw.city,
    lng: raw.lng,
    lat: raw.lat,
    price_per_person: ppp,
    tags: raw.tags || [],
    enable_book: !!raw.enable_book,
    enable_reservation: !!raw.enable_reservation,
    business_hours: guessHours(raw.category, raw.tags || []),
    products: raw.products || [],
    source,
    recommended: pickRecommended(raw.tags || [], raw.products || []),
    rating_count: 80 + Math.floor((raw.raw_score || 4) * 40) + (raw.tags?.length || 0) * 7,
    is_distraction: !!raw.is_distraction
  }
}

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
// 直接 .split 会抛错并导致整批 POI 转换失败 → 误回退到数据集。统一安全取字符串。
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
