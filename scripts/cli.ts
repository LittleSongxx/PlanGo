// 轻量规划 CLI（headless）：一句话 → 完整行程 + 比价，用于无界面快速验证规划链路。
// 用法：npm run cli -- "周六下午带娃玩4小时 减脂 预算120 上海"
process.env.DATA_SOURCE = process.env.DATA_SOURCE || 'mock'
process.env.XIAONIAN_CITY = process.env.XIAONIAN_CITY || '上海'

import { planOuting } from '../src/main/planner'
import { findDeal } from '../src/main/brain/dealFinder'

function parseArgs(text: string) {
  const slots: any = { raw_input: text }
  const budget = /预算[人均]*¥?(\d+)/.exec(text) || /人均\s*¥?(\d+)/.exec(text)
  if (budget) slots.budget_per_person = Number(budget[1])
  const size = /(\d+)\s*人/.exec(text)
  if (size) slots.group_size = Number(size[1])
  const age = /(\d+)\s*岁/.exec(text)
  if (age) slots.child_age = Number(age[1])
  const dur = /(\d+)\s*小时/.exec(text)
  if (dur) slots.duration_hours = Number(dur[1])
  const cityMatch = /(北京|上海|广州|深圳|杭州|成都|太原|洛阳|南宁|大连|青岛|郑州|长沙)/.exec(text)
  if (cityMatch) slots.city = cityMatch[1]
  return slots
}

async function main() {
  const text = process.argv.slice(2).join(' ') || '周六下午带娃出去玩4小时 减脂 预算120 上海'
  console.log('\n> ' + text + '\n')
  const slots = parseArgs(text)
  const { plan, demand, report, sources, notes } = await planOuting(slots, process.env.XIAONIAN_CITY!)

  console.log(`【${plan.title}】人群：${demand.personas.join('/') || '通用'} | 合计约¥${plan.total_cost} | 数据来源：${[...sources].join(',')}`)
  console.log('—'.repeat(40))
  for (const n of plan.nodes) {
    if (n.route_from_prev && n.transit_from_prev_min) console.log(`   ↓ ${n.route_from_prev.desc}`)
    console.log(`${n.time_start}-${n.time_end} [${n.category}] ${n.title}`)
    console.log(`        ★${n.poi?.filtered_score ?? n.poi?.raw_score ?? '-'}(${n.poi?.trust}) 人均¥${n.poi?.price_per_person ?? '-'} — ${n.reason}`)
  }
  console.log('—'.repeat(40))
  console.log('校验：' + (report.passed ? '硬约束全部通过 ✅' : '存在问题'))
  report.issues.forEach((i) => console.log(`  [${i.severity}] ${i.code}: ${i.message}`))
  if (notes.length) console.log('规划过程：' + notes.join(' | '))

  console.log('\n比价（模拟券池）：')
  for (const n of plan.nodes) {
    if (!n.poi) continue
    const d = findDeal(n.poi, demand.group_size)
    console.log(`  ${d.shop}: 原价¥${d.original} → 到手¥${d.final}（${d.reason}）`)
  }
  console.log('\n发同行人：' + plan.share_message + '\n')
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
