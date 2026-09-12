import assert from 'node:assert/strict'
import { projectHarness } from '../src/renderer/src/lib/harnessProjection'
import {
  amapNavUrl,
  carryOutFileStem,
  carryOutForbiddenHit,
  carryOutFromPlan,
  carryOutReminderAt,
  formatCarryOutHtml,
  formatCarryOutIcs,
  formatCarryOutText
} from '../src/shared/carryOut'
import type { HarnessSnapshot, Plan, PlanNode } from '../src/shared/types'

function node(partial: Partial<PlanNode> & Pick<PlanNode, 'title'>): PlanNode {
  return {
    node_id: partial.title,
    time_start: '',
    time_end: '',
    category: 'dining',
    reason: '',
    locked: false,
    transit_from_prev_min: null,
    verify_state: 'suggested',
    ...partial
  }
}

function plan(partial: Partial<Plan> = {}): Plan {
  return {
    plan_id: 'plan-1',
    run_id: 'run-1',
    version: 2,
    style: 'balanced',
    title: '周末下午',
    visit_date: '2026-09-13',
    timezone: 'Asia/Shanghai',
    party_size: 4,
    total_cost: 128,
    radar: {},
    share_message: '行程草案：真实店铺；4 人；已知估算小计 ¥128。',
    nodes: [node({
      title: '真实店铺',
      time_start: '14:00',
      time_end: '16:00',
      poi: {
        poi_id: 'shop-1', name: '真实店铺', category: '餐厅', raw_score: null, trust: 'unknown', trust_reason: '',
        address: '南滨路 1 号', tags: [], enable_book: false, enable_reservation: false, business_hours: '',
        products: [], source: 'amap', recommended: [], is_distraction: false, lng: 106.58, lat: 29.55
      }
    })],
    ...partial
  }
}

const go = carryOutFromPlan(plan({
  validation_notes: ['排队和路线证据尚未取得', '当前是待核验方案，尚不具备执行条件。'],
  cost_breakdown: { dining: 100, activities: null, transport: 28, pending: ['返程与额外消费另需核对。'] },
  origin: { name: '解放碑', latitude: 29.56, longitude: 106.57 },
  travel_mode: 'transit'
}))
assert.equal(go.verdict, 'go')
assert.equal(go.headline, '可以带着走')
assert(!go.pending.some(note => note.includes('尚不具备执行条件')))
assert(go.pending.includes('排队和路线证据尚未取得'))
const text = formatCarryOutText(go)
assert.match(text, /^可以带着走/)
assert(text.includes('14:00–16:00  真实店铺'))
assert(text.includes('地址：南滨路 1 号'))
assert(text.includes('uri.amap.com/navigation'))
assert(text.includes('mode=bus'))
assert(text.includes('仍要现场核对'))
assert(text.includes('这不是预约或支付回执'))
assert(!text.includes('行程草案'))
assert.equal(carryOutForbiddenHit(text), undefined)

const html = formatCarryOutHtml(go)
assert(html.includes('可以带着走'))
assert(html.includes('南滨路 1 号'))
assert(!html.includes('radar'))
assert.equal(carryOutForbiddenHit(html), undefined)

const ics = formatCarryOutIcs(plan(), new Date('2026-09-11T00:00:00+08:00'))
assert(ics)
assert(ics.includes('DTSTART;TZID=Asia/Shanghai:20260913T140000'))
assert(ics.includes('DTEND;TZID=Asia/Shanghai:20260913T160000'))
assert(ics.includes('这不是预约或支付回执'))
assert(!ics.includes('20260912T'))
assert.equal(carryOutForbiddenHit(ics), undefined)

const midnight = plan({
  nodes: [node({ title: '江边步道', time_start: '00:30', time_end: '02:00' })]
})
assert(formatCarryOutIcs(midnight)!.includes('DTSTART;TZID=Asia/Shanghai:20260913T003000'))
assert.equal(carryOutReminderAt(midnight, new Date('2026-09-12T10:00:00+08:00')), '2026-09-12T23:30:00+08:00')
assert.equal(carryOutReminderAt(plan(), new Date('2026-09-13T13:30:00+08:00')), undefined)
assert.equal(carryOutReminderAt(plan(), new Date('2026-09-13T12:00:00+08:00')), '2026-09-13T13:00:00+08:00')

const noDate = carryOutFromPlan(plan({ visit_date: undefined }))
assert.equal(noDate.verdict, 'hold')
assert.equal(noDate.headline, '先别出门 · 还不知道哪天')
assert.match(formatCarryOutText(noDate), /^先别出门 · 还不知道哪天/)
assert.equal(formatCarryOutIcs(plan({ visit_date: undefined })), undefined)

const noStops = carryOutFromPlan(plan({ nodes: [] }))
assert.equal(noStops.verdict, 'hold')
assert.equal(noStops.headline, '先别出门 · 还没有下一站')

const unknownCost = carryOutFromPlan(plan({ total_cost: null, party_size: undefined }))
assert.equal(unknownCost.verdict, 'go')
assert(formatCarryOutText(unknownCost).includes('人数待确认'))
assert(formatCarryOutText(unknownCost).includes('费用待核验'))

assert.equal(carryOutFileStem(plan()), 'PlanGo-2026-09-13-周末下午-v2')
assert.equal(amapNavUrl({ destLng: 106.58, destLat: 29.55, destName: '真实店铺', mode: 'walking' }),
  'https://uri.amap.com/navigation?to=106.58,29.55,%E7%9C%9F%E5%AE%9E%E5%BA%97%E9%93%BA&mode=walk&policy=1&src=plango&coordinate=gaode&callnative=1')

const snapshot: HarnessSnapshot = { run_id: 'run-1', input_text: '四个人吃饭', phase: 'WAITING_APPROVAL', event_seq: 2, version: 2, state: {
  selected_plan: { plan_id: 'plan-1', version: 2, label: '真实方案', total_cost: 0, party_size: 4,
    stops: [{ place_id: 'browser:shop', name: '真实店铺', category: '餐厅', start_minute: 600, end_minute: 660, unit_price: null, supply_source: 'browser' }] },
  candidate_plans: [{ plan_id: 'plan-1', version: 2, label: '真实方案', total_cost: 0, party_size: 4,
    stops: [{ place_id: 'browser:shop', name: '真实店铺', category: '餐厅', start_minute: 600, end_minute: 660, unit_price: null, supply_source: 'browser' }] }],
  verifier: { plan_id: 'plan-1', executable: false, unknown_evidence: [{ name: 'queue', detail: '排队和路线证据尚未取得' }] }
} }
const projected = projectHarness(snapshot).cards.find(card => card.kind === 'plan')!.plan
const fromProjection = carryOutFromPlan(projected)
assert.equal(fromProjection.verdict, 'hold', 'Missing visit_date keeps the outing on hold')
assert.equal(fromProjection.headline, '先别出门 · 还不知道哪天')
assert(fromProjection.stops.some(stop => stop.title === '真实店铺'))
assert(projected.validation_notes?.some(note => note.includes('尚不具备执行条件')))
assert(!fromProjection.pending.some(note => note.includes('尚不具备执行条件')))
assert.equal(projected.visit_date, undefined)
assert.equal(projected.nodes[0].verify_state, 'suggested')

console.log('Carry-out projection, text, calendar and safety checks passed')
