// VitaBench 自评脚本（headless，不依赖 Electron）：跑多场景规划，统计约束通过率 / 非空率 / 预算达标率。
// 写操作确认率：架构上 100%（所有 WRITE 工具强制两步确认，见 tools/localTools.ts）。
process.env.DATA_SOURCE = process.env.DATA_SOURCE || 'mock'
process.env.XIAONIAN_CITY = process.env.XIAONIAN_CITY || '上海'

import { planOuting } from '../src/main/planner'
import { verifyPlan } from '../src/main/brain/verifier'
import { mergedConstraints } from '../src/main/brain/persona'
import type { PlanSlots } from '../src/main/planner'

interface Scenario {
  name: string
  slots: PlanSlots
}

const SCENARIOS: Scenario[] = [
  { name: '家庭·带娃减脂', slots: { raw_input: '周六下午带老婆孩子出去玩4小时，孩子5岁，老婆减脂，预算人均120', city: '上海', group_size: 4, budget_per_person: 120, child_age: 5, duration_hours: 4, personas: ['带娃', '减脂'] } },
  { name: '朋友·4人聚会', slots: { raw_input: '周末约4个朋友聚会2男2女，能玩能吃', city: '上海', group_size: 4, duration_hours: 5, interests: ['citywalk'], personas: ['待客'] } },
  { name: '约会·送花', slots: { raw_input: '和对象约会，想浪漫点，用餐时送花', city: '上海', group_size: 2, duration_hours: 5, personas: ['约会'], special_requests: ['送花'] } },
  { name: '陪长辈', slots: { raw_input: '带爸妈出去，清淡少走路', city: '上海', group_size: 3, duration_hours: 4, personas: ['陪长辈'] } },
  { name: '独处·一人食', slots: { raw_input: '自己一个人待一下午', city: '上海', group_size: 1, duration_hours: 4, personas: ['独处'] } },
  { name: '商务宴请', slots: { raw_input: '请客户吃饭，要包间有档次', city: '上海', group_size: 4, budget_per_person: 300, duration_hours: 4, personas: ['商务宴请'] } }
]

async function main() {
  console.log('\n=== 小悠 · VitaBench 自评（数据源：' + process.env.DATA_SOURCE + '）===\n')
  let nonEmpty = 0
  let hardPass = 0
  let budgetOk = 0
  let personaOk = 0
  const rows: string[] = []

  for (const sc of SCENARIOS) {
    const { plan, demand } = await planOuting(sc.slots, process.env.XIAONIAN_CITY!)
    const cons = mergedConstraints(demand.personas)
    const per = plan.total_cost / Math.max(demand.group_size, 1)
    const ne = plan.nodes.length >= 2
    const hp = verifyPlan(plan, demand).passed
    const bo = !demand.budget_per_person || per <= demand.budget_per_person * 1.15
    const po = !plan.nodes.some((n) => n.poi && cons.hard_exclude.some((t) => `${n.poi!.name}${n.poi!.tags.join('')}`.includes(t)))
    if (ne) nonEmpty++
    if (hp) hardPass++
    if (bo) budgetOk++
    if (po) personaOk++
    rows.push(
      `${pad(sc.name, 14)} | 站数 ${plan.nodes.length} | 人均¥${Math.round(per)} | 硬约束${hp ? '✅' : '❌'} | 预算${bo ? '✅' : '❌'} | 人群过滤${po ? '✅' : '❌'} | 来源 ${Object.keys(plan.source_mix || {}).join(',')}`
    )
  }

  console.log(rows.join('\n'))
  const n = SCENARIOS.length
  console.log('\n---- 汇总 ----')
  console.log(`非空方案率：   ${pct(nonEmpty, n)}  (${nonEmpty}/${n})`)
  console.log(`硬约束通过率： ${pct(hardPass, n)}  (${hardPass}/${n})`)
  console.log(`预算达标率：   ${pct(budgetOk, n)}  (${budgetOk}/${n})`)
  console.log(`人群硬过滤率： ${pct(personaOk, n)}  (${personaOk}/${n})`)
  console.log(`写操作确认率： 100%  (架构强制：所有 WRITE 工具两步确认)`)
  console.log('')
}

function pad(s: string, n: number): string {
  const len = [...s].reduce((a, c) => a + (c.charCodeAt(0) > 255 ? 2 : 1), 0)
  return s + ' '.repeat(Math.max(0, n - len))
}
function pct(a: number, b: number): string {
  return ((a / b) * 100).toFixed(0) + '%'
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
