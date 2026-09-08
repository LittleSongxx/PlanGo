// 生成即验证 Verifier（确定性约束校验，代码非 LLM）。移植自 yoyu/application/verifier.py。
// 规划与校验分离；约束 by construction；营业不可得标"未知"而非猜（诚实边界）。
import type { Plan, SceneDemand, ConstraintIssue, VerifyReport } from '@shared/types'
import { mergedConstraints } from './persona'

// 场景互斥对（人群/场景 A 与 出现 B 互斥）
const SCENE_MUTEX: [string, string][] = [
  ['带娃', '酒吧'],
  ['带娃', '密室'],
  ['带娃', '夜店'],
  ['减脂', '自助'],
  ['经期关怀', '冰'],
  ['陪长辈', '蹦迪']
]

function parseHHMM(s: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec((s || '').trim())
  if (!m) return null
  return Number(m[1]) * 60 + Number(m[2])
}

function withinBusinessHours(hours: string, nodeTime: string): boolean | null {
  if (!hours) return null
  const nt = parseHHMM(nodeTime)
  if (nt == null) return null
  if (hours.includes('24') && hours.includes('小时')) return true
  const m = /(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})/.exec(hours)
  if (!m) return null
  const start = parseHHMM(m[1])
  const end = parseHHMM(m[2])
  if (start == null || end == null) return null
  if (end < start) return nt >= start || nt <= end
  return start <= nt && nt <= end
}

export function verifyPlan(plan: Plan, demand: SceneDemand): VerifyReport {
  const issues: ConstraintIssue[] = []
  const cons = mergedConstraints(demand.personas)
  const hardTags = cons.hard_exclude

  // 1) 预算（硬）
  if (demand.budget_per_person) {
    const per = plan.total_cost == null ? Infinity : plan.total_cost / Math.max(demand.group_size, 1)
    if (per > demand.budget_per_person * 1.15) {
      issues.push({ code: 'budget', severity: 'hard', message: `人均≈¥${Math.round(per)} 超预算¥${demand.budget_per_person}` })
    }
  }

  let prevEnd: number | null = null
  let hasActivity = false
  for (const n of plan.nodes) {
    const poi = n.poi
    const blob = `${n.title} ${n.category} ` + (poi ? `${poi.name} ${poi.tags.join(' ')}` : '')

    // 2) persona 硬约束
    for (const t of hardTags) {
      if (blob.includes(t)) {
        issues.push({ code: 'persona', node_id: n.node_id, severity: 'hard', message: `「${n.title}」命中人群硬约束「${t}」(${demand.personas.join('/')})` })
      }
    }

    // 3) 场景互斥
    for (const [a, b] of SCENE_MUTEX) {
      const active = demand.personas.includes(a)
      if (active && blob.includes(b)) {
        issues.push({ code: 'mutex', node_id: n.node_id, severity: 'hard', message: `「${n.title}」与场景「${a}」互斥(含${b})` })
      }
    }

    // 4) 带娃适龄 + 不太晚（soft）
    if (demand.child_age != null && demand.personas.includes('带娃')) {
      const nt = parseHHMM(n.time_start)
      if (nt != null && nt >= 20 * 60 && n.category !== 'commute') {
        issues.push({ code: 'age', node_id: n.node_id, severity: 'soft', message: `带${demand.child_age}岁娃，「${n.title}」安排在${n.time_start}偏晚` })
      }
    }

    // 5) 营业状态（有数据硬校验；无数据标建议态，诚实）
    if (poi && poi.business_hours) {
      const ok = withinBusinessHours(poi.business_hours, n.time_start)
      if (ok === false) {
        issues.push({ code: 'business_hours', node_id: n.node_id, severity: 'hard', message: `「${poi.name}」${n.time_start}不在营业时间(${poi.business_hours})` })
      }
    } else if (poi) {
      n.verify_state = 'suggested'
    }

    // 6) 时序单调（soft）
    const nt = parseHHMM(n.time_start)
    if (prevEnd != null && nt != null && nt < prevEnd) {
      issues.push({ code: 'time_order', node_id: n.node_id, severity: 'soft', message: `「${n.title}」开始时间早于上一节点结束` })
    }
    const ne = parseHHMM(n.time_end)
    prevEnd = ne != null ? ne : nt

    if (n.category === 'activity') hasActivity = true
  }

  // 7) 必须有玩乐（soft）
  if (!hasActivity) issues.push({ code: 'no_activity', severity: 'soft', message: '方案缺少非餐饮的玩乐节点' })

  const passed = issues.filter((i) => i.severity === 'hard').length === 0
  return { passed, issues }
}
