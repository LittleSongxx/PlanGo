// 本地生活工具层（官方风格 @is_tool）。READ 自动执行；WRITE 走两步确认（DPT System2）。
// 带随机失败率演异常处理（满座/售罄/超时）。
import { registerTool, type ToolOutcome } from './registry'
import { emitStep, updateStep, emitCard } from '../agent/bus'
import { session } from '../agent/session'
import { getConfig } from '../config'
import { getAdapter } from '../data/adapters'
import { findShopByName, queryShops } from '../data/mockdb'
import { planThreeStyles, alternativesFor, refreshPlan, reasonFor, buildDemand, type PlanSlots } from '../planner'
import { cleanAndRank } from '../brain/antiPollution'
import { mergedConstraints } from '../brain/persona'
import { findDeal } from '../brain/dealFinder'
import { rollFailure } from './harness'
import { computeDiscover } from '../discover'
import { proactive } from '../proactive'
import { favoriteShop, addPreference } from '../brain/memory'
import type { DealRow, DishReco, OutcomeCard, POISummary, ReceiptItem, SourceTag, TakeoutItem, GroupBuyPackage } from '@shared/types'

// ————————————————————————————— READ 工具 —————————————————————————————

registerTool({
  name: 'plan_outing',
  label: '规划周末行程',
  kind: 'READ',
  description:
    '核心工具：根据用户需求生成「周末几小时」的完整行程（玩乐→正餐→加场），自动做人群硬过滤、抗污染排序、生成即校验、定向重生，绝不返回空方案。当用户想安排出行/周末/带娃/约会/聚会时调用。',
  parameters: {
    type: 'object',
    properties: {
      raw_input: { type: 'string', description: '用户原话，用于兜底解析隐性约束' },
      city: { type: 'string', description: '城市，如 上海' },
      scene_type: { type: 'string', enum: ['family', 'friends', 'couple', 'solo', 'business'] },
      group_size: { type: 'number', description: '人数' },
      budget_per_person: { type: 'number', description: '人均预算（元）' },
      duration_hours: { type: 'number', description: '时长（小时），默认5' },
      start_time: { type: 'string', description: '出发时间 HH:MM，默认14:00' },
      child_age: { type: 'number', description: '如带娃，孩子年龄' },
      personas: { type: 'array', items: { type: 'string' }, description: '人群标签：减脂/带娃/约会/陪长辈/商务宴请/独处/解压/待客/经期关怀' },
      dietary: { type: 'array', items: { type: 'string' }, description: '饮食约束，如 减脂/清淡/忌辣' },
      interests: { type: 'array', items: { type: 'string' }, description: '兴趣，如 亲子/展览/citywalk' },
      special_requests: { type: 'array', items: { type: 'string' }, description: '特殊需求，如 送花/蛋糕' }
    }
  },
  async execute(args): Promise<ToolOutcome> {
    const s1 = emitStep('理解需求与隐性约束')
    const slots = args as PlanSlots
    updateStep(s1, 'done')
    const s2 = emitStep('并行检索玩乐/餐厅 + 抗污染去水分')
    const three = await planThreeStyles(slots, getConfig().city)
    const srcArr = [...three.sources]
    const mainSource = (srcArr.includes('real') ? 'real' : srcArr.includes('dataset') ? 'dataset' : 'simulated') as SourceTag
    updateStep(s2, 'done', `${three.demand.city}·候选来源：${srcArr.join('/')}`, mainSource)
    const s3 = emitStep('组装 3 套差异化方案 + 生成即校验')
    updateStep(s3, 'done', `经济 / 均衡 / 特色 三选一${three.notes.length ? '（' + three.notes.join('；') + '）' : ''}`)

    const budget = three.demand.budget_per_person
    const perOf = (p: (typeof three.variants)[number]['plan']) => Math.round(p.total_cost / Math.max(three.demand.group_size, 1))
    const variants = three.variants.map((v) => ({ plan: v.plan, styleLabel: v.styleLabel, per: perOf(v.plan), overBudget: budget && perOf(v.plan) > budget ? perOf(v.plan) - budget : undefined }))
    emitCard({ kind: 'plans', variants, city: three.demand.city, budget })

    // 默认推荐"均衡"，把它设为可继续 refine 的当前方案
    const primary = three.variants[0]
    session.setLastPlan(primary.plan)
    session.setLastDemand(three.demand)

    const result = { plan: primary.plan, demand: three.demand, report: primary.report }
    const per = perOf(primary.plan)
    // 诚实的预算结论：不许在超预算时说"预算内"
    let budgetLine = ''
    if (budget) {
      if (per <= budget) budgetLine = `人均约¥${per}，在预算¥${budget}以内 ✅`
      else if (per <= budget * 1.15) budgetLine = `人均约¥${per}，略超预算¥${budget}（+¥${per - budget}），已是该城当前候选里较省的组合`
      else budgetLine = `人均约¥${per}，超预算¥${budget}（+¥${per - budget}）；${result.demand.city}符合条件的更低价商家不足，建议放宽预算或换城市/时段`
    } else {
      budgetLine = `人均约¥${per}`
    }
    const lines = result.plan.nodes.map((n) => `${n.time_start} ${n.title}（${n.reason}）`)
    const issues = result.report.issues.filter((i) => i.severity === 'soft').map((i) => i.message)
    const hardLeft = result.report.issues.filter((i) => i.severity === 'hard').map((i) => i.message)
    // 三套方案概览，便于小悠口语化对比推荐
    const overview = three.variants
      .map((v) => `【${v.styleLabel}】人均¥${perOf(v.plan)}：${v.plan.nodes.map((n) => n.title).join(' → ')}`)
      .join('\n')
    const weatherLine = three.weather ? `本地天气：${three.weather.text} ${three.weather.temp}℃${three.weather.rainy ? '，有降水，已优先室内活动' : '，适合户外'}。\n` : ''
    return {
      result:
        `已在「${three.demand.city}」生成 3 套差异化方案（默认推荐"均衡之选"）：\n${weatherLine}${overview}\n\n默认方案「${result.plan.title}」明细（${budgetLine}）：\n` +
        lines.join('\n') +
        (hardLeft.length ? `\n⚠️ 仍未满足的硬约束（务必如实告知用户，不要谎称已满足）：${hardLeft.join('；')}` : '') +
        (issues.length ? `\n软提示：${issues.join('；')}` : '') +
        `\n数据来源：${srcArr.join('/')}。可继续：换成经济/特色 / 比价 / 点菜 / 发同行人确认 / 一键预约排号。`,
      activities: [`为你规划了 3 套周末行程（${three.demand.city}）`]
    }
  }
})

registerTool({
  name: 'search_nearby_poi',
  label: '搜索周边商家',
  kind: 'READ',
  description: '按关键词/品类搜索本地商家（餐饮/休闲娱乐），返回去水分后的可信榜单。',
  parameters: {
    type: 'object',
    properties: {
      keywords: { type: 'string', description: '关键词，如 亲子餐厅/轻食/密室' },
      category: { type: 'string', enum: ['到餐', '到综'], description: '品类' },
      city: { type: 'string' }
    },
    required: ['keywords']
  },
  async execute(args): Promise<ToolOutcome> {
    const city = String(args.city || getConfig().city)
    const st = emitStep(`搜索「${args.keywords}」`)
    const r = await getAdapter().searchPoi({ city, keywords: String(args.keywords).split(/\s+/), category: args.category as string, limit: 8 })
    const ranked = cleanAndRank(r.data)
    updateStep(st, 'done', `${ranked.length} 个结果`, r.source)
    if (!ranked.length) return { result: `没搜到「${args.keywords}」相关商家，建议换个关键词或放宽品类。` }
    const top = ranked.slice(0, 6).map((p) => `${p.name}｜评分${p.filtered_score ?? p.raw_score}(${p.trust})｜${p.price_per_person ? '人均¥' + p.price_per_person : '价格未知'}｜${p.tags.slice(0, 3).join('/')}`)
    return { result: `找到（已去水分）：\n${top.join('\n')}\n来源：${r.source}` }
  }
})

registerTool({
  name: 'discover_nearby',
  label: '附近发现',
  kind: 'READ',
  description: '用户没想法/想找灵感时：以用户当前定位为圆心，拉「今日附近」的美食、玩乐、咖啡甜品热点，按去水分榜单分组展示。不需要参数。',
  parameters: { type: 'object', properties: { city: { type: 'string' } } },
  async execute(args): Promise<ToolOutcome> {
    const st = emitStep('扫描附近热点')
    const { city, groups, source } = await computeDiscover(args.city as string | undefined)
    if (!groups.length) {
      updateStep(st, 'error', '附近暂无数据')
      return { result: '附近暂时没拉到热点，确认定位后再试，或直接跟我说想干嘛。' }
    }
    updateStep(st, 'done', `${groups.reduce((n, g) => n + g.items.length, 0)} 个热点`, source)
    emitCard({ kind: 'discover', city, groups, source })
    const summary = groups.map((g) => `${g.emoji}${g.label}：${g.items.map((p) => p.name).slice(0, 3).join('、')}`).join('\n')
    return {
      result: `${city}你附近的热点（点卡片可去规划/导航）：\n${summary}\n想选哪个方向？我可以就近排一套周末方案。`
    }
  }
})

registerTool({
  name: 'get_weather',
  label: '查询天气',
  kind: 'READ',
  description: '查询指定城市天气，用于判断室内/室外活动。',
  parameters: { type: 'object', properties: { city: { type: 'string' } } },
  async execute(args): Promise<ToolOutcome> {
    const city = String(args.city || getConfig().city)
    const st = emitStep(`查询${city}天气`)
    const w = await getAdapter().getWeather(city)
    updateStep(st, 'done', w.data ? `${w.data.text} ${w.data.temp}℃` : '未知', w.source)
    if (!w.data) return { result: `${city}天气获取失败，按室内活动稳妥安排。` }
    return { result: `${city}：${w.data.text}，${w.data.temp}℃（${w.source}）。${/雨|雪/.test(w.data.text) ? '有降水，建议室内为主。' : '天气不错，可安排室外。'}` }
  }
})

registerTool({
  name: 'check_availability',
  label: '查可订/排队',
  kind: 'READ',
  description: '查询某商家当前是否可订座/需排队（模拟真实并发，含满座/排队情况）。',
  parameters: { type: 'object', properties: { shop_name: { type: 'string' } }, required: ['shop_name'] },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    const st = emitStep(`查询「${name}」可订状态`)
    // 模拟：15% 满座、25% 需排队、其余可订
    if (rollFailure(0.1)) {
      updateStep(st, 'error', '查询超时')
      return { result: `「${name}」状态查询超时（错误即信息）。建议：重试一次，或先看同类备选。` }
    }
    const roll = Math.random()
    let status: string
    if (roll < 0.15) status = `「${name}」当前满座，最近可订为 19:30 之后。建议：调整时段，或换同类备选。`
    else if (roll < 0.4) status = `「${name}」需排队，前面约 ${3 + Math.floor(Math.random() * 8)} 桌，预计等 ${20 + Math.floor(Math.random() * 40)} 分钟。建议：边逛边取号（可调用取号）。`
    else status = `「${name}」可订座，当前有空位。可直接预约。`
    updateStep(st, 'done', 'dataset', 'dataset')
    return { result: status }
  }
})

registerTool({
  name: 'recommend_dishes',
  label: 'AI 点菜',
  kind: 'READ',
  description: '按人群硬过滤+软偏好，为某餐厅推荐菜品，每道给理由（如减脂排油炸、带娃加儿童餐）。',
  parameters: {
    type: 'object',
    properties: {
      shop_name: { type: 'string' },
      personas: { type: 'array', items: { type: 'string' } }
    },
    required: ['shop_name']
  },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    const activePersonas = effectivePersonas(args.personas as string[]) // 自动带入减脂/带娃等（越用越懂）
    const shop = findShopByName(name) || session.lastPlan?.nodes.find((n) => n.title === name)?.poi
    const st = emitStep(`为「${name}」AI 点菜`)
    if (!shop) {
      updateStep(st, 'error', '未找到该餐厅')
      return { result: `没找到「${name}」的菜单信息。可以先用 search_nearby_poi 定位它。` }
    }
    const dishes = recommendDishes(shop, activePersonas)
    updateStep(st, 'done', `${dishes.filter((d) => !d.excluded).length} 道推荐`)
    emitCard({ kind: 'dishes', shopName: shop.name, dishes })
    const recos = dishes.filter((d) => !d.excluded).map((d) => `${d.name}${d.price ? '(¥' + d.price + ')' : ''} — ${d.reason}`)
    const excluded = dishes.filter((d) => d.excluded).map((d) => d.name)
    const pTag = activePersonas.length ? `（已按「${activePersonas.join('/')}」定制）` : ''
    return {
      result: `「${shop.name}」单点推荐${pTag}：\n${recos.join('\n')}${excluded.length ? `\n⚠️ 避雷菜（按人群约束避开）：${excluded.join('、')}` : ''}\n想更省可以让我 compare_deal 对比"单点 vs 团购套餐"哪个划算。`
    }
  }
})

registerTool({
  name: 'order_takeout',
  label: '外卖点单',
  kind: 'READ',
  description: '不想出门/在家在办公室时：按人群偏好从附近可配送商家推荐"点什么外卖 + 送到哪 + 预计送达 + 配送/打包费 + 合计"，生成一张外卖清单卡。用户确认后可再走下单。',
  parameters: {
    type: 'object',
    properties: {
      shop_name: { type: 'string', description: '指定商家；不填则用当前方案的餐厅或附近可配送商家' },
      deliver_to: { type: 'string', description: '送达地址，如"家/公司/我的位置"' },
      party_size: { type: 'number' },
      personas: { type: 'array', items: { type: 'string' } }
    }
  },
  async execute(args): Promise<ToolOutcome> {
    const st = emitStep('挑可配送商家 + 按人群配外卖')
    const size = Number(args.party_size) || session.lastPlan?.nodes.length || 2
    const personas = effectivePersonas(args.personas as string[])
    const deliverTo = String(args.deliver_to || '我的位置（当前定位）')
    let shop: POISummary | undefined
    if (args.shop_name) shop = findShopByName(String(args.shop_name))
    if (!shop) shop = session.lastPlan?.nodes.find((n) => n.category === 'dining')?.poi
    if (!shop) {
      const near = queryShops({ city: session.lastDemand?.city || getConfig().city, category: '到餐', limit: 8 })
      shop = near[0]
    }
    if (!shop) {
      updateStep(st, 'error', '附近暂无可配送商家')
      return { result: '附近没找到可配送的商家，先定位或指定一家餐厅。' }
    }
    const dishes = recommendDishes(shop, personas).filter((d) => !d.excluded)
    // 份量：主菜按人头，配菜/主食适量
    const items: TakeoutItem[] = dishes.slice(0, 4).map((d, i) => ({
      name: d.name,
      price: d.price || 25 + i * 6,
      qty: i === 0 ? Math.max(1, Math.round(size / 2)) : 1,
      reason: d.reason
    }))
    const goods = items.reduce((s, it) => s + it.price * it.qty, 0)
    const deliveryFee = goods >= 60 ? 0 : 5
    const packFee = Math.min(items.length, 4)
    const total = goods + deliveryFee + packFee
    const etaMin = 30 + Math.floor(Math.random() * 15)
    updateStep(st, 'done', `${items.length} 件 · 约${etaMin}分钟送达`)
    emitCard({ kind: 'takeout', shopName: shop.name, deliverTo, etaMin, deliveryFee, packFee, items, total, source: shop.source })
    const list = items.map((it) => `${it.name}×${it.qty}（¥${it.price}）— ${it.reason}`).join('\n')
    return {
      result: `已为你配好「${shop.name}」外卖，送到「${deliverTo}」，预计${etaMin}分钟：\n${list}\n配送费¥${deliveryFee}${deliveryFee === 0 ? '(满减免)' : ''} + 打包¥${packFee}，合计约¥${total}。\n说一声"下单"，我走两步确认后帮你提交（履约走美团跑腿/外卖官方通道，演示阶段为模拟提交）。`
    }
  }
})

registerTool({
  name: 'compare_deal',
  label: '单点vs套餐比价',
  kind: 'READ',
  description: '对行程中的餐厅/商家做单点vs团购套餐、券后到手价比价，识别"假低价"，给出省了多少。',
  parameters: {
    type: 'object',
    properties: {
      shop_names: { type: 'array', items: { type: 'string' }, description: '要比价的商家名；不填则对当前方案里的商家比价' },
      party_size: { type: 'number' }
    }
  },
  async execute(args): Promise<ToolOutcome> {
    const st = emitStep('计算券后最优实付')
    const size = Number(args.party_size) || session.lastPlan?.nodes.length || 2
    let shops: POISummary[] = []
    const names = args.shop_names as string[] | undefined
    if (names?.length) shops = names.map((n) => findShopByName(n)).filter(Boolean) as POISummary[]
    else if (session.lastPlan) shops = session.lastPlan.nodes.map((n) => n.poi).filter(Boolean) as POISummary[]
    if (!shops.length) {
      updateStep(st, 'error', '无可比价商家')
      return { result: '还没有可比价的商家，先生成方案或指定商家名。' }
    }
    const eligible = inferEligible()
    const rows: DealRow[] = shops.map((p) => findDeal(p, size, eligible))
    updateStep(st, 'done', 'simulated', 'simulated')
    emitCard({ kind: 'deal', title: '券后到手价比价（模拟券池）', rows })
    const total = rows.reduce((s, r) => s + r.saved, 0)
    const summary = rows.map((r) => `${r.shop}：原价¥${r.original}→到手¥${r.final}（${r.reason}）`).join('\n')
    return { result: `比价完成，合计可省约¥${total}：\n${summary}\n注：模拟券池演示，真实核销需平台授权。` }
  }
})

registerTool({
  name: 'find_groupbuy',
  label: '团购套餐',
  kind: 'READ',
  description: '查某家店的团购套餐（双人餐/家庭餐/畅吃套餐等），给出原价、团购价、包含内容、适合人数、已售，供用户挑一份下单。',
  parameters: {
    type: 'object',
    properties: { shop_name: { type: 'string' }, party_size: { type: 'number' } },
    required: ['shop_name']
  },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    const size = Number(args.party_size) || session.lastPlan?.nodes.length || 2
    const shop = findShopByName(name) || session.lastPlan?.nodes.find((n) => n.title === name)?.poi
    const st = emitStep(`拉「${name}」团购套餐`)
    const base = shop?.price_per_person || 80
    const packages = buildGroupBuyPackages(shop?.name || name, base, size, shop)
    updateStep(st, 'done', `${packages.length} 个套餐`, shop?.source || 'simulated')
    emitCard({ kind: 'groupbuy', shopName: shop?.name || name, packages, source: shop?.source || 'simulated' })
    const list = packages.map((p) => `${p.name}：团购¥${p.price}（原价¥${p.originalPrice}，省¥${p.originalPrice - p.price}）· ${p.fitPeople}`).join('\n')
    return { result: `「${shop?.name || name}」团购套餐（含 AI 估算，最终以门店为准）：\n${list}\n看中哪个说一声，我走两步确认帮你下单。` }
  }
})

function buildGroupBuyPackages(_name: string, base: number, size: number, shop?: POISummary): GroupBuyPackage[] {
  const real = (shop?.products || []).filter((p) => p.price && !p.is_distraction).slice(0, 2)
  const pkgs: GroupBuyPackage[] = []
  for (const p of real) {
    const original = Math.round(p.price * 1.3)
    pkgs.push({ name: p.name, price: p.price, originalPrice: original, includes: p.tags?.slice(0, 4) || ['门店真实套餐'], fitPeople: '按套餐说明', sold: `已售${100 + Math.floor(Math.random() * 900)}` })
  }
  const dbl = Math.round(base * 2 * 0.82)
  pkgs.push({
    name: '双人超值套餐',
    price: dbl,
    originalPrice: Math.round(base * 2),
    includes: ['主菜×2', '小食×1', '饮品×2'],
    fitPeople: '适合2人',
    sold: `已售${300 + Math.floor(Math.random() * 700)}`,
    recommended: true
  })
  if (size >= 3) {
    const fam = Math.round(base * size * 0.78)
    pkgs.push({ name: `家庭欢聚套餐(${size}人)`, price: fam, originalPrice: Math.round(base * size), includes: ['主菜×3', '配菜×2', '主食×2', '饮品×' + size], fitPeople: `适合${size}人` })
  }
  return pkgs
}

// ————————————————————————————— WRITE 工具（两步确认）—————————————————————————————

function writeConfirm(title: string, detail: string, danger: boolean, run: () => Promise<{ text: string; cards: OutcomeCard[] }>): ToolOutcome {
  const token = session.registerConfirm({ title, detail, danger, run })
  emitCard({ kind: 'confirm', token, title, detail, danger })
  return { result: `已生成两步确认卡：「${title}」。${detail} 请等待用户点击确认后再继续（切勿假装已完成）。` }
}

registerTool({
  name: 'make_reservation',
  label: '预约订座',
  kind: 'WRITE',
  description: '为某商家预约订座（写操作，必须两步确认）。',
  parameters: {
    type: 'object',
    properties: {
      shop_name: { type: 'string' },
      time: { type: 'string', description: '预约时间 HH:MM' },
      party_size: { type: 'number' }
    },
    required: ['shop_name']
  },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    const time = String(args.time || session.lastPlan?.nodes.find((n) => n.category === 'dining')?.time_start || '18:00')
    const size = Number(args.party_size) || session.lastPlan?.nodes.length || 2
    return writeConfirm(`预约「${name}」`, `时间 ${time}，${size} 人`, false, async () => {
      const st = emitStep(`提交预约「${name}」`)
      await delay(500)
      if (rollFailure(0.15)) {
        updateStep(st, 'error', '满座')
        const items: ReceiptItem[] = [{ label: `预约「${name}」`, status: 'fail', detail: '该时段满座，已自动改约 19:30 或建议换同类', source: 'simulated' }]
        emitCard({ kind: 'receipt', items, shareMessage: '' })
        return { text: `「${name}」${time}满座（错误即信息），我已把它改到 19:30，或换同类备选，你定？`, cards: [] }
      }
      updateStep(st, 'done', '预约成功', 'simulated')
      const items: ReceiptItem[] = [{ label: `预约「${name}」`, status: 'ok', detail: `已订 ${time} ${size}人（模拟）`, source: 'simulated' }]
      emitCard({ kind: 'receipt', items, shareMessage: '' })
      return { text: `「${name}」${time} ${size}人已预约成功（模拟）。`, cards: [] }
    })
  }
})

registerTool({
  name: 'take_queue_number',
  label: '在线取号',
  kind: 'WRITE',
  description: '为某商家在线取排队号（写操作，必须两步确认），返回号码/前面几桌/预计等待。',
  parameters: { type: 'object', properties: { shop_name: { type: 'string' } }, required: ['shop_name'] },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    return writeConfirm(`为「${name}」取号`, '在线排队取号，可边逛边等', false, async () => {
      const st = emitStep(`为「${name}」取号`)
      await delay(400)
      const number = 'A' + (10 + Math.floor(Math.random() * 80))
      const ahead = 2 + Math.floor(Math.random() * 9)
      const eta = ahead * (6 + Math.floor(Math.random() * 4))
      updateStep(st, 'done', `${number}，前${ahead}桌`, 'simulated')
      emitCard({ kind: 'queue', shopName: name, number, ahead, etaMin: eta, source: 'simulated' })
      // 主动关心：到点催你出发（现场稍后自动触发一条提醒）
      proactive.scheduleDeparture(name, eta)
      return { text: `已为「${name}」取号 ${number}，前面 ${ahead} 桌，预计等 ${eta} 分钟。建议先去逛，我到点提醒你。`, cards: [] }
    })
  }
})

registerTool({
  name: 'place_takeout_order',
  label: '外卖下单',
  kind: 'WRITE',
  description: '在 order_takeout 生成清单后，用户确认下单时调用（写操作，必须两步确认）。提交外卖订单并返回回执。',
  parameters: {
    type: 'object',
    properties: {
      shop_name: { type: 'string' },
      deliver_to: { type: 'string' },
      total: { type: 'number' }
    },
    required: ['shop_name']
  },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    const to = String(args.deliver_to || '我的位置（当前定位）')
    const total = Number(args.total) || 0
    return writeConfirm(`外卖下单「${name}」`, `送到 ${to}${total ? `，合计约¥${total}` : ''}`, false, async () => {
      const st = emitStep(`提交外卖订单「${name}」`)
      await delay(500)
      if (rollFailure(0.12)) {
        updateStep(st, 'error', '商家繁忙')
        const items: ReceiptItem[] = [{ label: `外卖「${name}」`, status: 'fail', detail: '商家出餐繁忙，建议换一家或稍后再试', source: 'simulated' }]
        emitCard({ kind: 'receipt', items, shareMessage: '' })
        return { text: `「${name}」出餐繁忙下单失败（错误即信息），要我换一家附近可配送的吗？`, cards: [] }
      }
      const eta = 30 + Math.floor(Math.random() * 15)
      updateStep(st, 'done', `已接单 · 约${eta}分钟`, 'simulated')
      const items: ReceiptItem[] = [{ label: `外卖「${name}」`, status: 'ok', detail: `骑手已接单，预计${eta}分钟送到${to}（美团跑腿/外卖官方通道·演示模拟）`, source: 'simulated' }]
      emitCard({ kind: 'receipt', items, shareMessage: '' })
      return { text: `「${name}」外卖已下单成功，预计${eta}分钟送到「${to}」（官方履约通道·演示模拟）。`, cards: [] }
    })
  }
})

registerTool({
  name: 'place_groupbuy_order',
  label: '团购下单',
  kind: 'WRITE',
  description: '在 find_groupbuy 出套餐后，用户选定某套餐下单时调用（写操作，必须两步确认）。购买团购券并返回回执。',
  parameters: {
    type: 'object',
    properties: {
      shop_name: { type: 'string' },
      package_name: { type: 'string' },
      price: { type: 'number' },
      qty: { type: 'number' }
    },
    required: ['shop_name', 'package_name']
  },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    const pkg = String(args.package_name)
    const qty = Number(args.qty) || 1
    const price = Number(args.price) || 0
    return writeConfirm(`团购下单「${name}」`, `购买「${pkg}」×${qty}${price ? `，合计约¥${price * qty}` : ''}`, false, async () => {
      const st = emitStep(`购买团购券「${pkg}」`)
      await delay(450)
      if (rollFailure(0.1)) {
        updateStep(st, 'error', '库存不足')
        const items: ReceiptItem[] = [{ label: `团购「${pkg}」`, status: 'fail', detail: '该套餐今日售罄，建议换个套餐或明日再来', source: 'simulated' }]
        emitCard({ kind: 'receipt', items, shareMessage: '' })
        return { text: `「${pkg}」今日售罄下单失败（错误即信息），要不要换一份套餐？`, cards: [] }
      }
      const code = 'GB' + (100000 + Math.floor(Math.random() * 899999))
      updateStep(st, 'done', `券码 ${code}`, 'simulated')
      const items: ReceiptItem[] = [{ label: `团购「${pkg}」×${qty}`, status: 'ok', detail: `购买成功，券码 ${code}，到店出示核销（模拟）`, source: 'simulated' }]
      emitCard({ kind: 'receipt', items, shareMessage: '' })
      return { text: `「${name}」的「${pkg}」团购券已购买成功，券码 ${code}，到店直接核销（模拟）。`, cards: [] }
    })
  }
})

registerTool({
  name: 'cancel_action',
  label: '取消已确认动作',
  kind: 'WRITE',
  description: '计划有变时取消一个已执行/已确认的动作（预约/取号/团购券/外卖/送花）。写操作，必须两步确认。',
  parameters: {
    type: 'object',
    properties: { what: { type: 'string', description: '要取消的动作，如 预约「楼外楼」' } },
    required: ['what']
  },
  async execute(args): Promise<ToolOutcome> {
    const what = String(args.what)
    return writeConfirm(`取消「${what}」`, '将撤销这项已确认的安排。', true, async () => {
      const st = emitStep(`取消「${what}」`)
      await delay(350)
      updateStep(st, 'done', '已取消', 'simulated')
      const items: ReceiptItem[] = [{ label: `取消「${what}」`, status: 'ok', detail: '已为你撤销（模拟），如需可重新安排', source: 'simulated' }]
      emitCard({ kind: 'receipt', items, shareMessage: '' })
      return { text: `已取消「${what}」。要不要我给你换个时间/换一家重新安排？`, cards: [] }
    })
  }
})

registerTool({
  name: 'send_gift',
  label: '跑腿送花/蛋糕',
  kind: 'WRITE',
  description: '约会/生日/关怀场景，下单跑腿送鲜花/蛋糕到指定地点、指定时机（写操作，必须两步确认）。可指定"惊喜时点"如"正餐上桌时送到餐厅"。',
  parameters: {
    type: 'object',
    properties: {
      item: { type: 'string', description: '如 鲜花/生日蛋糕' },
      to: { type: 'string', description: '送达地点，如 餐厅/家/公司' },
      when: { type: 'string', description: '惊喜时点，如 正餐时/晚上8点/到店后' },
      budget: { type: 'number', description: '预算（元），可选' }
    },
    required: ['item']
  },
  async execute(args): Promise<ToolOutcome> {
    const item = String(args.item)
    const to = String(args.to || '餐厅')
    const when = String(args.when || '')
    const budget = Number(args.budget) || 0
    const detail = `送达：${to}${when ? ` · ${when}` : ''}${budget ? ` · 预算约¥${budget}` : ''}`
    return writeConfirm(`跑腿送${item}`, detail, false, async () => {
      const st = emitStep(`下单送${item}到${to}`)
      await delay(400)
      updateStep(st, 'done', '已接单', 'simulated')
      const items: ReceiptItem[] = [{ label: `送${item}`, status: 'ok', detail: `骑手已接单，${when ? when + '，' : ''}预计送达${to}（美团跑腿官方通道·演示模拟）`, source: 'simulated' }]
      emitCard({ kind: 'receipt', items, shareMessage: '' })
      return { text: `已下单送${item}到${to}${when ? '（' + when + '）' : ''}（跑腿官方通道·演示模拟），骑手已接单。`, cards: [] }
    })
  }
})

registerTool({
  name: 'send_message',
  label: '发同行人/群',
  kind: 'WRITE',
  description: '把方案/文案发给同行人或家庭群（写操作，必须两步确认）。不填 text 则用方案的分享文案。',
  parameters: {
    type: 'object',
    properties: { to: { type: 'string', description: '如 家庭群/老婆/朋友' }, text: { type: 'string' } },
    required: ['to']
  },
  async execute(args): Promise<ToolOutcome> {
    const to = String(args.to)
    const text = String(args.text || session.lastPlan?.share_message || '周末一起出去玩呀')
    return writeConfirm(`发送给「${to}」`, text, false, async () => {
      const st = emitStep(`发送到「${to}」`)
      await delay(300)
      updateStep(st, 'done', '已发送', 'simulated')
      const items: ReceiptItem[] = [{ label: `发送给「${to}」`, status: 'ok', detail: '消息已送达（模拟 IM Bridge）', source: 'simulated' }]
      emitCard({ kind: 'receipt', items, shareMessage: text })
      return { text: `已把方案发给「${to}」（模拟）：${text}`, cards: [] }
    })
  }
})

registerTool({
  name: 'batch_execute',
  label: '一键执行',
  kind: 'WRITE',
  description: '确认方案后，一键并行执行：预约正餐 + 为热门店取号 + （可选）送花。写操作，必须两步确认。',
  parameters: {
    type: 'object',
    properties: {
      reserve: { type: 'boolean', description: '是否预约正餐' },
      queue: { type: 'boolean', description: '是否为需排队的店取号' },
      gift: { type: 'string', description: '如需送花/蛋糕填写' }
    }
  },
  async execute(args): Promise<ToolOutcome> {
    const plan = session.lastPlan
    if (!plan) return { result: '还没有方案可执行，请先生成行程。' }
    const dining = plan.nodes.find((n) => n.category === 'dining')
    const detail = [args.reserve !== false && dining ? `预约「${dining.title}」` : '', args.queue ? '热门店取号' : '', args.gift ? `送${args.gift}` : ''].filter(Boolean).join(' + ')
    return writeConfirm('一键执行本次方案', detail || '批量执行', true, async () => {
      const items: ReceiptItem[] = []
      const jobs: Promise<void>[] = []
      if (args.reserve !== false && dining) {
        jobs.push(
          (async () => {
            const st = emitStep(`预约「${dining.title}」`)
            await delay(500)
            if (rollFailure(0.15)) {
              updateStep(st, 'error', '满座→改约')
              items.push({ label: `预约「${dining.title}」`, status: 'fail', detail: '满座，已建议改约19:30/换同类', source: 'simulated' })
            } else {
              updateStep(st, 'done', '成功', 'simulated')
              items.push({ label: `预约「${dining.title}」`, status: 'ok', detail: `已订 ${dining.time_start}`, source: 'simulated' })
              dining.verify_state = 'booked'
            }
          })()
        )
      }
      if (args.queue) {
        const act = plan.nodes.find((n) => n.category === 'activity')
        if (act)
          jobs.push(
            (async () => {
              const st = emitStep(`为「${act.title}」取号`)
              await delay(450)
              const number = 'A' + (10 + Math.floor(Math.random() * 80))
              const ahead = 2 + Math.floor(Math.random() * 6)
              updateStep(st, 'done', number, 'simulated')
              items.push({ label: `取号「${act.title}」`, status: 'ok', detail: `${number}，前${ahead}桌`, source: 'simulated' })
            })()
          )
      }
      if (args.gift) {
        jobs.push(
          (async () => {
            const st = emitStep(`送${args.gift}`)
            await delay(400)
            updateStep(st, 'done', '已接单', 'simulated')
            items.push({ label: `送${args.gift}`, status: 'ok', detail: '骑手已接单', source: 'simulated' })
          })()
        )
      }
      await Promise.all(jobs)
      if (dining) favoriteShop(dining.title)
      emitCard({ kind: 'receipt', items, shareMessage: plan.share_message })
      const okN = items.filter((i) => i.status === 'ok').length
      return { text: `一键执行完成：${okN}/${items.length} 项成功。${plan.share_message}`, cards: [] }
    })
  }
})

// ————————————————————————————— refine（weplan 手术刀）—————————————————————————————

registerTool({
  name: 'refine_plan',
  label: '一句话改一站',
  kind: 'READ',
  description: '对当前方案做单点手术式修改，其余节点不动：换餐厅/换活动/换更便宜/提前/推后/去掉加场/钉住某站。',
  parameters: {
    type: 'object',
    properties: {
      op: { type: 'string', enum: ['swap_dining', 'swap_activity', 'cheaper_dining', 'earlier', 'later', 'remove_extra', 'lock'], description: '手术操作' },
      target: { type: 'string', description: '目标站名（lock/精确定位时用）' },
      minutes: { type: 'number', description: 'earlier/later 平移分钟数，默认30' }
    },
    required: ['op']
  },
  async execute(args): Promise<ToolOutcome> {
    const plan = session.lastPlan
    if (!plan) return { result: '还没有方案可修改，请先生成行程。' }
    const st = emitStep(`手术式修改：${args.op}`)
    const demand = buildDemand({ raw_input: '', city: plan.nodes[0]?.poi?.city }, getConfig().city)
    const op = String(args.op)
    let msg = ''
    if (op === 'swap_dining' || op === 'cheaper_dining') {
      const node = plan.nodes.find((n) => n.category === 'dining')
      if (node) {
        const alts = await alternativesFor(demand, '到餐')
        const usedIds = new Set(plan.nodes.map((n) => n.poi?.poi_id))
        let alt = alts.find((p) => !usedIds.has(p.poi_id))
        if (op === 'cheaper_dining') alt = alts.filter((p) => !usedIds.has(p.poi_id)).sort((a, b) => (a.price_per_person || 0) - (b.price_per_person || 0))[0]
        if (alt) {
          node.poi = alt
          node.title = alt.name
          node.reason = reasonFor(alt, demand)
          msg = `已把正餐换成「${alt.name}」，其余不动。`
        }
      }
    } else if (op === 'swap_activity') {
      const node = plan.nodes.find((n) => n.category === 'activity')
      if (node) {
        const alts = await alternativesFor(demand, '到综')
        const usedIds = new Set(plan.nodes.map((n) => n.poi?.poi_id))
        const alt = alts.find((p) => !usedIds.has(p.poi_id))
        if (alt) {
          node.poi = alt
          node.title = alt.name
          node.reason = reasonFor(alt, demand)
          msg = `已把玩乐换成「${alt.name}」，其余不动。`
        }
      }
    } else if (op === 'earlier' || op === 'later') {
      const delta = (Number(args.minutes) || 30) * (op === 'earlier' ? -1 : 1)
      plan.nodes[0].time_start = shiftTime(plan.nodes[0].time_start, delta)
      msg = `整体${op === 'earlier' ? '提前' : '推后'}${Math.abs(delta)}分钟。`
    } else if (op === 'remove_extra') {
      const acts = plan.nodes.filter((n) => n.category === 'activity')
      if (acts.length > 1) {
        plan.nodes = plan.nodes.filter((n) => n !== acts[acts.length - 1])
        msg = '已去掉加场，行程更轻松。'
      }
    } else if (op === 'lock') {
      const node = plan.nodes.find((n) => n.title === String(args.target))
      if (node) {
        node.locked = true
        msg = `已钉住「${node.title}」，后续重排不动它。`
      }
    }
    refreshPlan(plan, demand)
    session.setLastPlan(plan)
    emitCard({ kind: 'plan', plan })
    updateStep(st, 'done')
    return { result: (msg || '已按要求微调方案。') + `当前人均约¥${Math.round(plan.total_cost / Math.max(demand.group_size, 1))}。` }
  }
})

// ————————————————————————————— consensus（群体确认）—————————————————————————————

registerTool({
  name: 'create_consensus',
  label: '发起群体确认',
  kind: 'READ',
  description: '为当前方案生成一张群体确认卡（同意/改一处/拒绝），发给同行人收集偏好后再定。',
  parameters: {
    type: 'object',
    properties: { question: { type: 'string' }, options: { type: 'array', items: { type: 'string' } } }
  },
  async execute(args): Promise<ToolOutcome> {
    const plan = session.lastPlan
    if (!plan) return { result: '还没有方案，先生成行程再发起群体确认。' }
    const question = String(args.question || `这份「${plan.title}」行程大家看行不行？`)
    const options = (args.options as string[]) || ['同意，就这么定', '想改正餐', '想改玩乐', '换个时间']
    emitCard({ kind: 'consensus', planId: plan.plan_id, question, options })
    return { result: `已发起群体确认：${question} 选项：${options.join(' / ')}。收集到偏好后我用一句话手术刀改一站。` }
  }
})

// ————————————————————————————— 辅助 —————————————————————————————

function inferEligible(): string[] {
  const plan = session.lastPlan
  const eligible: string[] = []
  const blob = plan?.nodes.map((n) => n.reason + n.title).join('') || ''
  if (/儿童|带娃|亲子|岁/.test(blob)) eligible.push('儿童')
  if (/长辈|老人/.test(blob)) eligible.push('老人')
  return eligible
}

function recommendDishes(shop: POISummary, personas: string[]): DishReco[] {
  const cons = mergedConstraints(personas)
  // 基础菜单：店内真实套餐 + 按品类合成的常见菜
  const base: { name: string; price?: number }[] = []
  for (const p of shop.products.filter((x) => !x.is_distraction)) base.push({ name: p.name, price: p.price })
  const synth = synthMenu(shop)
  for (const s of synth) if (!base.find((b) => b.name === s.name)) base.push(s)

  return base.slice(0, 10).map((d) => {
    const excluded = cons.hard_exclude.some((t) => d.name.includes(t))
    const soft = cons.soft_prefer.filter((t) => d.name.includes(t))
    const isRec = shop.recommended.includes(d.name)
    let reason = '招牌/热销'
    if (excluded) reason = `避雷：命中人群约束（${personas.join('/') || '忌口'}），建议避开`
    else if (soft.length) reason = `契合「${soft.join('、')}」`
    else if (isRec) reason = '店内招牌推荐'
    return { name: d.name, price: d.price, reason, excluded, signature: isRec && !excluded }
  })
}

// 合并人群：显式传入 + 上次规划需求里的人群（越用越懂，点菜/外卖自动带入减脂/带娃等）
function effectivePersonas(argPersonas?: string[]): string[] {
  const set = new Set<string>([...(argPersonas || []), ...((session.lastDemand?.personas as string[]) || [])])
  return [...set]
}

function synthMenu(shop: POISummary): { name: string }[] {
  const blob = shop.category + shop.tags.join('')
  if (/火锅/.test(blob)) return [{ name: '番茄锅底' }, { name: '肥牛卷' }, { name: '手打虾滑' }, { name: '时蔬拼盘' }]
  if (/轻食|沙拉|健康/.test(blob)) return [{ name: '鸡胸沙拉' }, { name: '牛油果藜麦碗' }, { name: '低卡能量餐' }]
  if (/粤|茶楼|港/.test(blob)) return [{ name: '虾饺' }, { name: '烧鹅' }, { name: '例汤' }, { name: '白灼时蔬' }]
  if (/日料|寿司/.test(blob)) return [{ name: '刺身拼盘' }, { name: '手握寿司' }, { name: '茶碗蒸' }]
  return [{ name: '招牌套餐' }, { name: '当季时蔬' }, { name: '例汤' }, { name: '主食拼盘' }]
}

function shiftTime(hhmm: string, delta: number): string {
  const [h, m] = hhmm.split(':').map(Number)
  let total = h * 60 + m + delta
  if (total < 0) total += 24 * 60
  const nh = Math.floor(total / 60) % 24
  const nm = total % 60
  return `${String(nh).padStart(2, '0')}:${String(nm).padStart(2, '0')}`
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

// 供记忆写入（用户明确表达偏好时，模型可调用）
registerTool({
  name: 'remember_preference',
  label: '记住偏好',
  kind: 'READ',
  description: '当用户表达出长期偏好（喜欢/讨厌某类）时，记住它，越用越懂你。',
  parameters: {
    type: 'object',
    properties: { text: { type: 'string' }, polarity: { type: 'string', enum: ['positive', 'negative'] } },
    required: ['text']
  },
  async execute(args): Promise<ToolOutcome> {
    addPreference(String(args.text), (args.polarity as 'positive' | 'negative') || 'positive')
    return { result: `已记住：${args.polarity === 'negative' ? '不喜欢' : '喜欢'}${args.text}。` }
  }
})
