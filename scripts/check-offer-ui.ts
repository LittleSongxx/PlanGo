import assert from 'node:assert/strict'
import type { HarnessSnapshot, OfferComparison, OfferSelection } from '../src/shared/types'
import { canSelectOffer, projectHarness } from '../src/renderer/src/lib/harnessProjection'

const source_ref = { command_id: 'offer-command', artifact_id: 'page:offer-command' }
const source = { ...source_ref, valid: true, url: 'https://fixture.invalid/shop/one', observed_at: '2026-09-09T04:00:00Z', expires_at: '2026-09-09T04:10:00Z' }
const comparison: OfferComparison = {
  source_ref, source, merchant: { name: '受控门店（甲店）', address: '受控路 1 号' }, constraints: { timezone: 'Asia/Shanghai', party_size: 3, visit_date: '2026-09-11', budget: 120, per_person_budget: null },
  entries: [
    { offer_index: 0, offer_hash: 'a'.repeat(64), grounded: true, name: '50 元代金券', kind: 'voucher', price_basis: 'voucher', status: 'unknown', price: 47, face_value: 50, original_price: null, people: null, known_cost: 47, total_cost: null, within_budget: null, reasons: ['券售价 47 元，抵扣面值 50 元'], missing_rules: ['叠加和最低消费待核对'], quote: '50元代金券 售价47元', source_ref: { command_id: source_ref.command_id, offer_index: 0, offer_hash: 'a'.repeat(64) } },
    { offer_index: 1, offer_hash: 'b'.repeat(64), grounded: true, name: '精选双人餐', kind: 'package', price_basis: 'per_package', status: 'ineligible', price: 98, face_value: null, original_price: null, people: 2, known_cost: 98, total_cost: null, within_budget: null, reasons: ['套餐标注 2 人，本次 3 人，不能认定覆盖全员'], missing_rules: ['超出人数费用待核对'], quote: '精选双人餐98元', source_ref: { command_id: source_ref.command_id, offer_index: 1, offer_hash: 'b'.repeat(64) } },
    { offer_index: 2, offer_hash: 'c'.repeat(64), grounded: true, name: '冷面鸡单品', kind: 'single_item', price_basis: 'single_item', status: 'unknown', price: 19.9, face_value: null, original_price: 29.9, people: null, known_cost: 19.9, total_cost: null, within_budget: null, reasons: ['单品不能证明完整餐食足量'], missing_rules: ['份量及适用日期待核对'], quote: '冷面鸡19.9元 原价29.9元', source_ref: { command_id: source_ref.command_id, offer_index: 2, offer_hash: 'c'.repeat(64) } }
  ], limitations: ['未购买，额外费用未知'], summary: '按同一门店的已读优惠逐项核对'
}
const snapshot: HarnessSnapshot = { run_id: 'offer-run', input_text: '读取门店优惠', phase: 'PARTIAL_FAILED', outcome: 'PARTIAL_FAILED', version: 7, event_seq: 4, offer_comparison: comparison,
  state: { browser_artifacts: [{ artifact_id: source_ref.artifact_id, type: 'browser_page', source: 'browser', url: source.url, observed_at: source.observed_at, data: { places: [{ name: comparison.merchant.name, address: comparison.merchant.address }], offers: [{ name: '50 元代金券', price: 47 }] } }] } }
const before = structuredClone(snapshot)
const priced: HarnessSnapshot = { ...snapshot, offer_comparison: undefined, state: {
  trip_spec: { party_size: 3, budget: 155, travel_mode: 'transit' },
  selected_plan: { plan_id: 'fare-plan', version: 1, party_size: 3, total_cost: 163.5,
    stops: [{ place_id: 'fare-poi', name: '受控餐厅', category: '餐厅', start_minute: 1080, end_minute: 1140,
      unit_price: 50, estimated_cost: 163.5, transport_cost: 13.5, transport_summary: '受控公交 · 标准票价4.5元/人' }] }
} }
const fareCard = projectHarness(priced).cards.find(card => card.kind === 'plan')
assert(fareCard?.kind === 'plan')
assert.deepEqual(fareCard.plan.cost_breakdown, { dining: 150, activities: 0, transport: 13.5, pending: ['返程与额外消费另需核对。'] })
assert.equal(fareCard.plan.total_cost, 163.5, 'The transport total already includes party size and is added once')
assert.equal(fareCard.plan.nodes[0].transport_cost, 13.5)
assert(fareCard.plan.share_message.includes('已知估算小计'))
const tighter = structuredClone(priced)
;(tighter.state.trip_spec as any).per_person_budget = 40
const tighterCard = projectHarness(tighter).cards.find(card => card.kind === 'plan')
assert(tighterCard?.kind === 'plan')
assert.equal(tighterCard.plan.budget_limit, 120, 'Both total and per-person caps apply; the smaller confirmed cap wins')
const legacyFare = structuredClone(priced)
const oldPlan = legacyFare.state.selected_plan as any
oldPlan.total_cost = oldPlan.stops[0].estimated_cost = 150
delete oldPlan.stops[0].transport_cost
const oldCard = projectHarness(legacyFare).cards.find(card => card.kind === 'plan')
assert(oldCard?.kind === 'plan')
assert.equal(oldCard.plan.cost_breakdown?.transport, null, 'Missing transport price cannot display as a free trip')
assert(oldCard.plan.cost_breakdown?.pending.some(note => note.includes('交通费')))
const cards = projectHarness(snapshot).cards.filter(card => card.kind === 'groupbuy')
assert.equal(cards.length, 1, 'A derived comparison replaces the same source preview without duplicate cards')
const projected = cards[0].comparison!
assert.equal(projected.entries[0].price, 47)
assert.equal(projected.entries[0].face_value, 50)
assert.equal(projected.entries[0].original_price, null)
assert.equal(projected.entries[1].people, 2)
assert.equal(projected.constraints.party_size, 3)
assert.equal(projected.entries[1].total_cost, null, 'A two-person package cannot become a whole meal for three via division')
assert.equal(projected.entries[2].kind, 'single_item')
assert.equal(projected.entries[2].original_price, 29.9)
assert.deepEqual(projected.source_ref, source_ref)
assert.deepEqual(snapshot, before, 'Projection does not create another mutable source of merchant facts')

const incomplete = structuredClone(snapshot)
incomplete.offer_comparison!.entries[0].status = 'eligible'
incomplete.offer_comparison!.entries[0].total_cost = 47
incomplete.offer_comparison!.entries[0].within_budget = true
const guarded = projectHarness(incomplete).cards.find(card => card.kind === 'groupbuy')!.comparison!
assert.equal(guarded.entries[0].status, 'unknown')
assert.equal(guarded.entries[0].total_cost, null)
assert.equal(guarded.entries[0].within_budget, null)
const brokenSource = structuredClone(snapshot)
brokenSource.offer_comparison!.entries[0].source_ref.command_id = 'other-command'
assert.equal(projectHarness(brokenSource).cards.find(card => card.kind === 'groupbuy')!.comparison!.entries[0].offer_hash, '', 'An unbound offer cannot expose an actionable selection')
const selection: OfferSelection = { expected_version: 7, source_ref, offer_index: 0, offer_hash: 'a'.repeat(64), poi_id: 'canonical-poi-one' }
assert(canSelectOffer(snapshot, selection))
for (const selectionPatch of [{ expected_version: 6 }, { source_ref: { ...source_ref, artifact_id: 'other-page' } }, { offer_hash: 'b'.repeat(64) }, { offer_index: 1, offer_hash: 'b'.repeat(64) }]) assert(!canSelectOffer(snapshot, { ...selection, ...selectionPatch }))
assert(!canSelectOffer({ ...snapshot, offer_comparison: { ...comparison, source: { ...source, valid: false, expires_at: null } } }, selection))
assert(!canSelectOffer({ ...snapshot, offer_comparison: { ...comparison, entries: comparison.entries.map(entry => ({ ...entry, grounded: false })) } }, selection))
const perPerson = structuredClone(snapshot)
Object.assign(perPerson.offer_comparison!.entries[1], { price_basis: 'per_person', known_cost: 196, reasons: ['原文每人98元，两人已知费用196元超出预算120元'] })
const perPersonCard = projectHarness(perPerson).cards.find(card => card.kind === 'groupbuy')!.comparison!.entries[1]
assert.equal(perPersonCard.price, 98)
assert.equal(perPersonCard.price_basis, 'per_person')
assert.equal(perPersonCard.known_cost, 196, 'Per-person quoted price and known multi-person cost remain separate')
assert(!canSelectOffer({ ...snapshot, state: { ...snapshot.state, action_results: [{ status: 'UNKNOWN' }] } }, selection))

const cache = new Map<string, string>()
Object.defineProperty(globalThis, 'localStorage', { value: { getItem: (key: string) => cache.get(key) ?? null, setItem: (key: string, value: string) => cache.set(key, value), removeItem: (key: string) => cache.delete(key) } })
let calls = 0, edits = 0
const selected = { ...snapshot, version: 8, event_seq: 5 }
Object.defineProperty(globalThis, 'window', { value: { plango: { harness: { editRequirements: async () => { edits++; return snapshot }, selectOffer: async (runId: string, body: OfferSelection) => { calls++; assert.equal(runId, snapshot.run_id); assert.deepEqual(body, selection); return selected } } } } })
const { useStore } = await import('../src/renderer/src/store')
useStore.setState({ run: snapshot, busy: false, backendReady: true })
await useStore.getState().selectOffer('other-run', selection)
await useStore.getState().selectOffer(snapshot.run_id, { ...selection, expected_version: 6 })
assert.equal(calls, 0)
await useStore.getState().selectOffer(snapshot.run_id, selection)
assert.equal(calls, 1)
assert.equal(useStore.getState().run?.run_id, snapshot.run_id)
assert.equal(useStore.getState().run?.version, 8)
useStore.setState({ run: snapshot, pendingDelivery: { request: { requestId: 'pending-input', text: '原输入待核对', runId: snapshot.run_id }, sessionId: useStore.getState().activeSessionId, status: 'unconfirmed' } })
await useStore.getState().selectOffer(snapshot.run_id, selection)
await useStore.getState().editRequirements({ expected_version: 7, fields: { party_size: 4 }, offer_source: source_ref })
assert.equal(calls, 1)
assert.equal(edits, 0, 'An unresolved D1 input blocks competing comparison edits and selections')
console.log(JSON.stringify({ scope: 'controlled offer comparison projection and exact selection', real_business_actions: 0, checks: ['source replacement', 'price versus face value', 'two-person package at three people', 'single-item scope', 'missing-rules guard', 'original evidence retained', 'exact source/version/hash selection', 'UNKNOWN guard', 'same-run selected snapshot', 'invalid-source and grounding guards', 'per-person known cost', 'pending-input edit guard'] }))
