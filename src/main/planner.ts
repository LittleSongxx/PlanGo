// 规划引擎：意图槽位 → SceneDemand → 并行取供给 → 抗污染排序 → 组装时间线 → 生成即校验 → 定向重生（≤2轮）。
// 绝不返回 0 方案（3 级降级）。移植 yoyu 规划/校验 + weplan 五维评分/deeplink。
import type { Plan, PlanNode, POISummary, SceneDemand, SceneType, VerifyReport, RouteInfo } from '@shared/types'
import { getAdapter, type SearchQuery } from './data/adapters'
import { cityCenter } from './data/amap'
import { cleanAndRank } from './brain/antiPollution'
import { mergedConstraints, inferPersonas } from './brain/persona'
import { verifyPlan } from './brain/verifier'
import { getConfig, isValidCity } from './config'

export interface PlanSlots {
  raw_input?: string
  city?: string
  scene_type?: SceneType
  group_size?: number
  budget_per_person?: number
  duration_hours?: number
  start_time?: string
  child_age?: number
  personas?: string[]
  dietary?: string[]
  interests?: string[]
  special_requests?: string[]
  location?: string
}

export function buildDemand(slots: PlanSlots, defaultCity: string): SceneDemand {
  const raw = slots.raw_input || ''
  const personas = Array.from(new Set([...(slots.personas || []), ...inferPersonas(raw)]))
  // 无明确地点时，用用户当前坐标作周边搜索圆心（打开即定位得到），让结果就近
  const originCoords = getConfig().coords
  const city = isValidCity(slots.city) ? slots.city! : isValidCity(defaultCity) ? defaultCity : '上海'
  return {
    scene_type: slots.scene_type || guessScene(personas, slots.group_size),
    city,
    group_size: slots.group_size ?? 2,
    duration_hours: slots.duration_hours ?? 5,
    start_time: slots.start_time || '14:00',
    budget_per_person: slots.budget_per_person,
    child_age: slots.child_age,
    dietary: slots.dietary || [],
    interests: slots.interests || [],
    special_requests: slots.special_requests || [],
    personas,
    location: slots.location || originCoords || undefined,
    raw_input: raw
  }
}

function guessScene(personas: string[], size?: number): SceneType {
  if (personas.includes('带娃')) return 'family'
  if (personas.includes('约会')) return 'couple'
  if (personas.includes('商务宴请')) return 'business'
  if (personas.includes('独处') || size === 1) return 'solo'
  return 'friends'
}

interface Pools {
  activity: POISummary[]
  dining: POISummary[]
  extra: POISummary[]
}

export interface PlanResult {
  plan: Plan
  report: VerifyReport
  sources: Set<string>
  demand: SceneDemand
  notes: string[]
}

async function fetchPools(demand: SceneDemand): Promise<{ pools: Pools; sources: Set<string> }> {
  const cons = mergedConstraints(demand.personas)
  const adapter = getAdapter()
  const sources = new Set<string>()

  // 就近保障（对齐 weplan/yoyu）：没有精确坐标时，用城市中心作圆心，保证走"周边按距离搜"，而非全城乱搜（会显得离得远）
  if (!demand.location && getConfig().amap.key) {
    try {
      const c = await cityCenter(demand.city)
      if (c) demand.location = c
    } catch {
      /* ignore */
    }
  }

  // 饮食向关键词（用于正餐检索）；其余归为活动向
  const FOOD_RE = /餐|美食|轻食|沙拉|健康|低卡|粗粮|蒸|高蛋白|热汤|温补|粥|红糖|姜|暖|粤菜|本帮|私房|宴请|包间|正餐|一人食|吧台|小份|清淡|茶楼|养生|西餐|特色小吃|老字号|火锅/
  const activityKw = Array.from(new Set([...demand.interests, ...cons.query_keywords])).filter((k) => !FOOD_RE.test(k))
  const diningKw = Array.from(new Set([...demand.dietary, ...cons.query_keywords.filter((k) => FOOD_RE.test(k))]))

  const q = (category: string, keywords: string[]): SearchQuery => ({
    city: demand.city,
    category,
    keywords,
    excludeTags: cons.hard_exclude,
    location: demand.location,
    limit: 14
  })

  const [act, din] = await Promise.all([adapter.searchPoi(q('到综', activityKw)), adapter.searchPoi(q('到餐', diningKw))])
  sources.add(act.source)
  sources.add(din.source)

  // 品类归位：活动池剔除纯餐饮（麦当劳/咖啡不能当"带娃活动"）；餐饮池优先真正的餐饮
  const isDiningCat = (p: POISummary) => /餐饮|餐厅|快餐|咖啡|奶茶|饮品|面包|甜品|小吃/.test(p.category)
  const activity = cleanAndRank(dedupe(act.data).filter((p) => !isDiningCat(p)), cons.soft_prefer)
  const dinRaw = dedupe(din.data)
  const dinFood = dinRaw.filter(isDiningCat)
  const dining = cleanAndRank(dinFood.length ? dinFood : dinRaw, cons.soft_prefer)
  // 加场：从活动池里除去已用第一项作为备选来源
  const extra = activity.slice(1)
  return { pools: { activity, dining, extra }, sources }
}

function dedupe(list: POISummary[]): POISummary[] {
  const seen = new Set<string>()
  return list.filter((p) => (seen.has(p.poi_id) ? false : (seen.add(p.poi_id), true)))
}

// 场景默认软预算（仅用于候选筛选，避免无预算时选到天价场所；不作硬校验）
function softBudget(demand: SceneDemand): number {
  if (demand.budget_per_person) return demand.budget_per_person
  const byScene: Record<string, number> = { family: 150, friends: 160, couple: 260, solo: 130, business: 450 }
  return byScene[demand.scene_type] ?? 180
}

function withinBudget(p: POISummary, demand: SceneDemand): boolean {
  const cap = softBudget(demand)
  return (p.price_per_person || 0) <= cap * 1.15
}

async function routeBetween(a?: POISummary, b?: POISummary, demand?: SceneDemand): Promise<RouteInfo | undefined> {
  if (!a || !b || !demand) return undefined
  const adapter = getAdapter()
  const mode: RouteInfo['mode'] = demand.personas.includes('陪长辈') || demand.child_age != null ? 'driving' : 'transit'
  const origin = a.lng && a.lat ? `${a.lng},${a.lat}` : ''
  const dest = b.lng && b.lat ? `${b.lng},${b.lat}` : ''
  const r = await adapter.route(origin, dest, mode, demand.city)
  return r.data || undefined
}

function addMinutes(hhmm: string, min: number): string {
  const [h, m] = hhmm.split(':').map(Number)
  const total = h * 60 + m + min
  const nh = Math.floor((total % (24 * 60)) / 60)
  const nm = total % 60
  return `${String(nh).padStart(2, '0')}:${String(nm).padStart(2, '0')}`
}

function reason(poi: POISummary, demand: SceneDemand, cons: ReturnType<typeof mergedConstraints>): string {
  const hits = cons.soft_prefer.filter((k) => `${poi.name}${poi.tags.join('')}`.includes(k))
  const bits: string[] = []
  if (hits.length) bits.push(`契合「${hits.slice(0, 2).join('、')}」`)
  if (poi.trust === 'high') bits.push('去水分后评分可信')
  if (poi.trust === 'low') bits.push('已识别刷量、谨慎推荐')
  if (demand.budget_per_person && poi.price_per_person)
    bits.push(poi.price_per_person <= demand.budget_per_person ? `人均¥${poi.price_per_person}在预算内` : `人均¥${poi.price_per_person}略高于预算`)
  if (poi.distance_m) bits.push(poi.distance_m < 1000 ? `离你约${poi.distance_m}m` : `离你约${(poi.distance_m / 1000).toFixed(1)}km`)
  if (poi.enable_reservation) bits.push('可预约')
  return bits.join('，') || '综合评分与距离较优'
}

interface AssembleOpts {
  strategy?: 'economic' | 'balanced' | 'special'
  exclude?: Set<string>
  rainy?: boolean // 天气有降水：活动优先室内
}

// 室内/户外判定（天气自适应用）：命中户外词=户外，命中室内词=室内
const OUTDOOR_RE = /公园|广场|绿道|海滩|沙滩|步道|风筝|户外|野餐|露营|骑行|徒步|花海|山/
const INDOOR_RE = /室内|馆|中心|乐园|商场|购物|影院|剧院|水族|科技|美术|博物|展|亲子|儿童|密室|游戏|书店|咖啡|茶/
function isOutdoor(p: POISummary): boolean {
  const blob = `${p.name}${p.category}${p.tags.join('')}`
  return OUTDOOR_RE.test(blob) && !INDOOR_RE.test(blob)
}

async function assemble(pools: Pools, demand: SceneDemand, opts: AssembleOpts = {}): Promise<Plan> {
  const cons = mergedConstraints(demand.personas)
  const strat = opts.strategy ?? 'balanced'
  const excl = opts.exclude ?? new Set<string>()
  const cheapest = (list: POISummary[]) => [...list].sort((a, b) => (a.price_per_person || 0) - (b.price_per_person || 0))[0]
  const avail = (list: POISummary[]) => list.filter((p) => !excl.has(p.poi_id))
  // 特色分：高评分 + 出片/网红/地标/老字号加权（special 用）
  const specialScore = (p: POISummary) =>
    (p.filtered_score || p.raw_score || 0) + (/网红|打卡|地标|出片|特色|老字号|景点|主题|艺术|展/.test(`${p.name}${p.tags.join('')}`) ? 0.8 : 0)
  // economic=最省；balanced=去水分榜首（默认最佳）；special=最出片高分。三套跨方案互斥，保证不重复。
  // indoorBias（雨雪天）：对活动池先把户外场所排到后面，尽量选室内。
  const pick = (list: POISummary[], indoorBias = false): POISummary | undefined => {
    let a = avail(list)
    if (!a.length) return list[0]
    if (indoorBias) {
      const indoor = a.filter((p) => !isOutdoor(p))
      if (indoor.length) a = indoor
    }
    if (strat === 'economic') {
      const inb = a.filter((p) => withinBudget(p, demand))
      return cheapest(inb.length ? inb : a)
    }
    if (strat === 'special') {
      const s = [...a].sort((x, y) => specialScore(y) - specialScore(x))
      return s.find((p) => withinBudget(p, demand)) || s[0]
    }
    return a.find((p) => withinBudget(p, demand)) || a[0]
  }
  const act = pick(pools.activity, opts.rainy)
  const din = pick(pools.dining)
  const extra = avail(pools.extra).find((p) => p.poi_id !== act?.poi_id && withinBudget(p, demand))

  const nodes: PlanNode[] = []
  let t = demand.start_time
  let idx = 0
  const push = async (poi: POISummary | undefined, category: PlanNode['category'], durMin: number, prevPoi?: POISummary) => {
    if (!poi) return
    const route = await routeBetween(prevPoi, poi, demand)
    if (route && prevPoi) {
      t = addMinutes(t, route.duration_min)
    }
    const start = t
    const end = addMinutes(start, durMin)
    nodes.push({
      node_id: `n${idx++}`,
      time_start: start,
      time_end: end,
      category,
      title: poi.name,
      poi,
      reason: reason(poi, demand, cons),
      locked: false,
      transit_from_prev_min: route?.duration_min || 0,
      route_from_prev: route,
      verify_state: poi.business_hours ? 'verified' : 'suggested'
    })
    t = end
  }

  await push(act, 'activity', 120)
  await push(din, 'dining', 90, act)
  if (demand.duration_hours >= 4.5 && extra) await push(extra, 'activity', 75, din)

  const diningCost = (din?.price_per_person || 0) * demand.group_size
  const activityCost = (act?.price_per_person || act?.products?.[0]?.price || 0) * demand.group_size
  const extraCost = extra ? (extra.price_per_person || 0) * demand.group_size : 0
  const total = Math.round(diningCost + activityCost + extraCost)

  const plan: Plan = {
    plan_id: 'plan_' + Date.now().toString(36),
    style: demand.budget_per_person && demand.budget_per_person < 100 ? 'economic' : demand.personas.includes('约会') ? 'special' : 'balanced',
    title: planTitle(demand),
    nodes,
    total_cost: total,
    radar: {},
    share_message: ''
  }
  applyRadar(plan, demand)
  plan.share_message = shareMessage(plan, demand)
  return plan
}

function planTitle(demand: SceneDemand): string {
  const who = demand.personas.includes('带娃') ? '带娃' : demand.personas.includes('约会') ? '约会' : `${demand.group_size}人`
  return `${demand.city}·周末${who}${demand.duration_hours}小时`
}

const clampScore = (v: number, lo = 35, hi = 99): number => Math.round(Math.max(lo, Math.min(hi, v)))

// 确定性五维打分（移植 weplan scoring.py）：用方案里的真实数据算分，可复现、可解释、与雷达图严格对齐。
// 维度：省钱 / 好玩 / 便捷 / 合适 / 特色（0-100），并给每一维一句自然语言理由。
function computeRadar(plan: Plan, demand: SceneDemand): { scores: Record<string, number>; reasons: Record<string, string> } {
  const venues = plan.nodes.filter((n) => n.category === 'activity' || n.category === 'dining')
  const activities = plan.nodes.filter((n) => n.category === 'activity')
  const ratings = venues.map((n) => n.poi?.filtered_score ?? n.poi?.raw_score ?? 0).filter((r) => r > 0)
  const avgRating = ratings.length ? ratings.reduce((a, b) => a + b, 0) / ratings.length : 0
  const highRating = ratings.filter((r) => r >= 4.5).length
  const per = plan.total_cost == null ? Infinity : plan.total_cost / Math.max(demand.group_size, 1)
  const totalTravel = plan.nodes.reduce((s, n) => s + (n.transit_from_prev_min || 0), 0)
  const categories = new Set(venues.map((n) => n.category))
  const reasons: Record<string, string> = {}

  // 省钱：真实人均 vs 预算
  let cost: number
  const budget = demand.budget_per_person
  if (budget) {
    const ratio = per / budget
    cost = ratio <= 0.7 ? 96 : ratio <= 0.9 ? 90 : ratio <= 1.0 ? 82 : ratio <= 1.1 ? 70 : ratio <= 1.3 ? 55 : 40
    reasons.省钱 = `人均约¥${Math.round(per)}，预算¥${budget}（${ratio < 1 ? '低于' : '接近/超出'}预算${Math.abs(1 - ratio) * 100 | 0}%）`
  } else {
    cost = per <= 80 ? 88 : per <= 150 ? 80 : per <= 300 ? 68 : 55
    reasons.省钱 = `人均约¥${Math.round(per)}，未设预算，按绝对价位评估`
  }

  // 好玩：真实评分 + 活动数量 + 品类多样
  let fun = 45
  if (avgRating) fun += ((avgRating - 3.5) / 1.5) * 35
  fun += Math.min(activities.length, 3) * 6
  fun += (categories.size - 1) * 6
  fun = clampScore(fun)
  reasons.好玩 = `含${activities.length}个游玩点、${categories.size}类业态${avgRating ? `，均分${avgRating.toFixed(1)}` : '，部分场所暂无评分'}`

  // 便捷：真实段间车程
  let conv = 100 - (Math.min(totalTravel, 120) / 120) * 50
  conv = clampScore(conv)
  reasons.便捷 = `全程通勤约${totalTravel}分钟${totalTravel <= 40 ? '，整体时间宽裕' : '，通勤稍多注意预留时间'}`

  // 合适：群体约束命中
  let fit = 70
  const cons = mergedConstraints(demand.personas)
  const hitPrefer = venues.filter((n) => cons.soft_prefer.some((k) => `${n.title}${n.poi?.tags.join('') || ''}`.includes(k))).length
  fit += Math.min(hitPrefer, 4) * 6
  if (demand.child_age && activities.some((n) => /亲子|公园|乐园|儿童/.test(n.title + (n.poi?.tags.join('') || '')))) fit += 8
  if (demand.dietary.length) fit += 4
  fit = clampScore(fit)
  reasons.合适 = hitPrefer ? `命中${hitPrefer}处群体偏好，按场景匹配` : '已按场景匹配'

  // 特色：高分口碑 + 多样性
  let uniq = 52 + highRating * 10 + (categories.size - 1) * 7
  if (avgRating >= 4.6) uniq += 8
  uniq = clampScore(uniq)
  reasons.特色 = highRating ? `${highRating}个4.5★+口碑点` : '以稳妥常规选择为主'

  return { scores: { 省钱: cost, 好玩: fun, 便捷: conv, 合适: fit, 特色: uniq }, reasons }
}

// 计算行程总里程/总通勤，供 UI 展示
function computeTravel(plan: Plan): { min: number; km: number } {
  let min = 0
  let m = 0
  for (const n of plan.nodes) {
    min += n.transit_from_prev_min || 0
    m += n.route_from_prev?.distance_m || 0
  }
  return { min, km: Math.round((m / 1000) * 10) / 10 }
}

// 把打分结果写进 plan（scores + reasons + 里程/通勤）
function applyRadar(plan: Plan, demand: SceneDemand): void {
  const { scores, reasons } = computeRadar(plan, demand)
  plan.radar = scores
  plan.radar_reasons = reasons
  const tv = computeTravel(plan)
  plan.total_travel_min = tv.min
  plan.total_distance_km = tv.km
}

function shareMessage(plan: Plan, demand: SceneDemand): string {
  const first = plan.nodes[0]
  const din = plan.nodes.find((n) => n.category === 'dining')
  const start = first?.time_start || demand.start_time
  const perStr = plan.total_cost ? `人均约¥${Math.round(plan.total_cost / Math.max(demand.group_size, 1))}` : ''
  const parts = [`搞定了，${start} 出发`]
  if (first) parts.push(`先去「${first.title}」`)
  if (din) parts.push(`然后到「${din.title}」吃饭`)
  const extra = plan.nodes.filter((n) => n.category === 'activity')[1]
  if (extra) parts.push(`最后去「${extra.title}」`)
  return parts.join('，') + '。' + (perStr ? ` ${perStr}，看看行不行？` : ' 看看行不行？')
}

// 定向重生：把命中硬约束/超预算的节点，换成候选池里下一个合规项
function regenerate(plan: Plan, report: VerifyReport, pools: Pools, demand: SceneDemand): Plan {
  const cons = mergedConstraints(demand.personas)
  const badNodeIds = new Set(report.issues.filter((i) => i.severity === 'hard' && i.node_id).map((i) => i.node_id!))
  const used = new Set(plan.nodes.map((n) => n.poi?.poi_id).filter(Boolean) as string[])
  for (const n of plan.nodes) {
    if (!badNodeIds.has(n.node_id)) continue
    const pool = n.category === 'dining' ? pools.dining : pools.activity
    const alt = pool.find((p) => !used.has(p.poi_id) && withinBudget(p, demand) && !cons.hard_exclude.some((t) => `${p.name}${p.tags.join('')}`.includes(t)))
    if (alt) {
      n.title = alt.name
      n.poi = alt
      n.reason = reason(alt, demand, cons)
      used.add(alt.poi_id)
    }
  }
  // 预算整体超标：把最贵的餐饮换成更便宜的
  if (report.issues.some((i) => i.code === 'budget')) {
    const dNode = plan.nodes.find((n) => n.category === 'dining')
    if (dNode && demand.budget_per_person) {
      const cheaper = pools.dining.filter((p) => !used.has(p.poi_id)).sort((a, b) => (a.price_per_person || 0) - (b.price_per_person || 0))[0]
      if (cheaper && (cheaper.price_per_person || 0) < (dNode.poi?.price_per_person || 999)) {
        dNode.title = cheaper.name
        dNode.poi = cheaper
        dNode.reason = reason(cheaper, demand, cons)
      }
    }
  }
  plan.total_cost = recomputeCost(plan, demand)
  applyRadar(plan, demand)
  plan.share_message = shareMessage(plan, demand)
  return plan
}

function recomputeCost(plan: Plan, demand: SceneDemand): number {
  return Math.round(plan.nodes.reduce((s, n) => s + (n.poi?.price_per_person || n.poi?.products?.[0]?.price || 0) * demand.group_size, 0))
}

// 取供给池并保证非空（3 级降级），供单方案/多方案共用
async function ensurePools(demand: SceneDemand): Promise<{ pools: Pools; sources: Set<string>; notes: string[] }> {
  const notes: string[] = []
  let { pools, sources } = await fetchPools(demand)

  if (!pools.activity.length && !pools.dining.length) {
    notes.push('精确匹配无结果 → 放宽软约束重搜')
    const relaxed: SceneDemand = { ...demand, interests: [], dietary: [] }
    const r = await fetchPools(relaxed)
    pools = r.pools
    r.sources.forEach((s) => sources.add(s))
  }
  if (!pools.dining.length) {
    const adapter = getAdapter()
    const any = await adapter.searchPoi({ city: demand.city, category: '到餐', limit: 10 })
    any.source && sources.add(any.source)
    pools.dining = cleanAndRank(dedupe(any.data))
    notes.push('餐饮池为空 → 放宽品类兜底')
  }
  if (!pools.activity.length) {
    const adapter = getAdapter()
    const any = await adapter.searchPoi({ city: demand.city, category: '到综', limit: 10 })
    any.source && sources.add(any.source)
    pools.activity = cleanAndRank(dedupe(any.data))
    pools.extra = pools.activity.slice(1)
  }
  return { pools, sources, notes }
}

function finalizePlan(plan: Plan, demand: SceneDemand, report: VerifyReport, pools: Pools, notes: string[]): { plan: Plan; report: VerifyReport } {
  for (let round = 0; round < 2 && !report.passed; round++) {
    notes.push(`第${round + 1}轮定向重生：${report.issues.filter((i) => i.severity === 'hard').map((i) => i.message).join('；')}`)
    plan = regenerate(plan, report, pools, demand)
    report = verifyPlan(plan, demand)
  }
  const mix: Record<string, number> = {}
  for (const n of plan.nodes) if (n.poi) mix[n.poi.source] = (mix[n.poi.source] || 0) + 1
  plan.source_mix = mix
  return { plan, report }
}

export async function planOuting(slots: PlanSlots, defaultCity: string): Promise<PlanResult> {
  const demand = buildDemand(slots, defaultCity)
  const { pools, sources, notes } = await ensurePools(demand)
  let plan = await assemble(pools, demand)
  let report = verifyPlan(plan, demand)
  ;({ plan, report } = finalizePlan(plan, demand, report, pools, notes))
  return { plan, report, sources, demand, notes }
}

export interface PlanVariant {
  plan: Plan
  report: VerifyReport
  styleLabel: string
}

// 三方案差异化：经济 / 均衡 / 特色（对齐 weplan/yoyu）。共享一次供给检索。
export async function planThreeStyles(slots: PlanSlots, defaultCity: string): Promise<{ variants: PlanVariant[]; sources: Set<string>; demand: SceneDemand; notes: string[]; weather?: { text: string; temp: string; rainy: boolean } }> {
  const demand = buildDemand(slots, defaultCity)
  const { pools, sources, notes } = await ensurePools(demand)

  // 天气感知：做攻略前先查本地天气；有降水则活动优先室内，并诚实提示用户。
  let weather: { text: string; temp: string; rainy: boolean } | undefined
  try {
    const w = await getAdapter().getWeather(demand.city)
    if (w.data) {
      const rainy = /雨|雪|雷|冰雹/.test(w.data.text)
      weather = { text: w.data.text, temp: w.data.temp, rainy }
      notes.push(rainy ? `${demand.city}${w.data.text} ${w.data.temp}℃，已优先安排室内活动` : `${demand.city}${w.data.text} ${w.data.temp}℃，适合户外`)
    }
  } catch {
    /* 天气拿不到不影响规划 */
  }

  const styleMeta: { key: Plan['style']; label: string; strategy: 'economic' | 'balanced' | 'special' }[] = [
    { key: 'balanced', label: '均衡之选', strategy: 'balanced' },
    { key: 'economic', label: '经济实惠', strategy: 'economic' },
    { key: 'special', label: '特色出片', strategy: 'special' }
  ]

  const used = new Set<string>()
  const variants: PlanVariant[] = []
  for (const m of styleMeta) {
    // 三套跨方案互斥：每套都避开前面已用门店，保证经济/均衡/特色是三份不同的方案
    const exclude = new Set(used)
    let plan = await assemble(pools, demand, { strategy: m.strategy, exclude, rainy: weather?.rainy })
    plan.style = m.key
    plan.title = `${planTitle(demand)}·${m.label}`
    let report = verifyPlan(plan, demand)
    ;({ plan, report } = finalizePlan(plan, demand, report, pools, []))
    applyRadar(plan, demand)
    plan.share_message = shareMessage(plan, demand)
    for (const n of plan.nodes) if (n.poi) used.add(n.poi.poi_id)
    variants.push({ plan, report, styleLabel: m.label })
  }
  return { variants, sources, demand, notes, weather }
}

// —— refine 用：取某品类的候选池（已抗污染排序）——
export async function alternativesFor(demand: SceneDemand, category: '到餐' | '到综'): Promise<POISummary[]> {
  const { pools } = await fetchPools(demand)
  return category === '到餐' ? pools.dining : pools.activity
}

// 单点手术后刷新派生字段（费用/五维/分享文案）
export function refreshPlan(plan: Plan, demand: SceneDemand): Plan {
  // 重排时间线（保留 locked 节点顺序，串行推时间）
  let t = plan.nodes[0]?.time_start || demand.start_time
  for (let i = 0; i < plan.nodes.length; i++) {
    const n = plan.nodes[i]
    if (i > 0) t = addMinutes(t, n.transit_from_prev_min || 10)
    const dur = durationOf(n)
    n.time_start = t
    n.time_end = addMinutes(t, dur)
    t = n.time_end
  }
  plan.total_cost = recomputeCost(plan, demand)
  applyRadar(plan, demand)
  plan.share_message = shareMessage(plan, demand)
  return plan
}

function durationOf(n: PlanNode): number {
  const s = n.time_start.split(':').map(Number)
  const e = n.time_end.split(':').map(Number)
  const d = e[0] * 60 + e[1] - (s[0] * 60 + s[1])
  return d > 0 ? d : n.category === 'dining' ? 90 : 100
}

export function reasonFor(poi: POISummary, demand: SceneDemand): string {
  return reason(poi, demand, mergedConstraints(demand.personas))
}
