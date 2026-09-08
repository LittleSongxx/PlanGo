import assert from 'node:assert/strict'
import { projectHarness, runBusy, canResolveAction } from '../src/renderer/src/lib/harnessProjection'
import type { HarnessSnapshot } from '../src/shared/types'

const plan = { plan_id: 'plan-1', version: 2, label: '真实方案', total_cost: 0, party_size: 4,
  stops: [{ place_id: 'browser:shop', name: '真实店铺', category: '餐厅', start_minute: 600, end_minute: 660, unit_price: null, supply_source: 'browser', evidence_ids: ['ev-1'] }] }
const snapshot: HarnessSnapshot = { run_id: 'run-1', input_text: '四个人吃饭，人均 100', phase: 'WAITING_APPROVAL', event_seq: 2, version: 2, interrupt_id: 'approval:proposal-1', state: {
  selected_plan: plan, candidate_plans: [plan], place_candidates: [{ place_id: 'browser:shop', source: 'browser', rating: 4, price_known: false }],
  evidence: [{ evidence_id: 'ev-1', source: 'browser', source_ref: 'https://example.org/menu', claim: '页面未展示价格' }],
  action_proposal: { proposal_id: 'proposal-1', plan_id: 'plan-1', plan_version: 2, actions: [{ tool_name: 'reserve_place', arguments: { place_id: 'browser:shop', name: '真实店铺', party_size: 4, at_minute: 600, estimated_cost: 0 } }] },
  action_results: [{ action_id: 'write-1', status: 'UNKNOWN', result: {} }],
  browser_artifacts: [{ type: 'offers', title: '真实套餐', data: { offers: [{ name: '双人套餐', price: null }] } }]
} }
const projected = projectHarness(snapshot)
const card = projected.cards.find(c => c.kind === 'plan')!
assert.equal(card.plan.total_cost, null, 'Unknown prices must not become free plans')
assert.equal(card.plan.nodes[0].poi?.price_per_person, undefined)
assert.equal(card.plan.nodes[0].poi?.raw_score, null, 'Browser model defaults are not observed ratings')
assert.equal(card.plan.party_size, 4)
assert.equal(card.plan.run_id, 'run-1')
assert.equal(projected.cards.find(c => c.kind === 'confirm')?.token, 'approval:proposal-1')
assert.equal(projected.cards.find(c => c.kind === 'receipt')?.items[0].status, 'pending')
assert.equal(projected.cards.find(c => c.kind === 'groupbuy')?.packages[0].price, null)
assert.equal(projected.evidence[0].source, 'browser')
assert.equal(runBusy(snapshot), false)

const stale = structuredClone(snapshot)
;(stale.state.action_proposal as Record<string, unknown>).plan_version = 1
assert.equal(projectHarness(stale).cards.some(c => c.kind === 'confirm'), false, 'Old plan approvals must not reappear')
const paused = { ...snapshot, state: { ...snapshot.state, browser_wait: { type: 'browser', message: '请登录后继续' } } }
assert.equal(runBusy(paused), false)
assert.equal(projectHarness(paused).cards.some(c => c.kind === 'confirm'), false)
assert.match(projectHarness(paused).messages.at(-1)!.content, /请登录/)
assert.equal(runBusy({ ...snapshot, command_pending: true }), true)
console.log('Harness UI projection checks passed')

// Minimal desktop shim checks state races without a renderer/test framework.
const cache = new Map<string, string>()
Object.defineProperty(globalThis, 'localStorage', { value: { getItem: (k: string) => cache.get(k) ?? null, setItem: (k: string, v: string) => cache.set(k, v), removeItem: (k: string) => cache.delete(k) } })
let resolveCreate!: (value: HarnessSnapshot) => void
const calls: string[] = []
Object.defineProperty(globalThis, 'window', { value: { xiaonian: { harness: {
  createRun: () => new Promise<HarnessSnapshot>(resolve => { resolveCreate = resolve }),
  getRun: async (id: string) => { calls.push(`get:${id}`); return snapshot },
  events: async () => ({ events: [] }),
  resume: async (...args: string[]) => { calls.push(`resume:${args.join(':')}`); return snapshot },
  cancel: async () => snapshot
} } } })
const { useStore, loadSessions } = await import('../src/renderer/src/store')
useStore.setState({ run: snapshot, backendReady: true, busy: false })
useStore.getState().applyHarness(snapshot)
useStore.getState().applyHarness({ ...snapshot, version: 1, phase: 'FAILED' })
assert.equal(useStore.getState().run?.phase, 'WAITING_APPROVAL', 'Stale events cannot replace canonical state')
await useStore.getState().confirm('old-approval', true)
assert.equal(calls.length, 0, 'A cached approval cannot reach backend resume')
const session = useStore.getState().sessions[0]
assert.equal(session.runId, snapshot.run_id)
assert.deepEqual(session.cards, [], 'History must not persist executable approval cards')
useStore.getState().newSession()
assert.equal(cache.get('xy_active_run'), undefined)
const pending = useStore.getState().send('新的安排')
useStore.getState().newSession()
resolveCreate(snapshot)
await pending
assert.equal(useStore.getState().run, null, 'An old request cannot overwrite a new session')
useStore.getState().restoreSession(session.id)
await new Promise(resolve => setTimeout(resolve, 0))
assert(calls.includes(`get:${snapshot.run_id}`), 'History restore must fetch the canonical backend run')
assert.equal(useStore.getState().run?.run_id, snapshot.run_id)
console.log('Harness UI state/recovery checks passed')

const browserApproval: HarnessSnapshot = { ...snapshot, state: {
  browser_steps: 1, turn_id: 2, browser_action: { action_id: 'browser-write', snapshot_id: 'snap-1', tab_id: 'tab-1' },
  browser_observation: { snapshot_id: 'snap-1', tab_id: 'tab-1', url: 'https://www.meituan.com/order', elements: [{ idx: 1, text: '立即购买', name: '购买套餐' }] },
  action_proposal: { proposal_id: 'browser-proposal', run_id: 'run-1', plan_id: 'browser-artifact:run-1:1', plan_version: 2, rationale: '查看',
    actions: [{ action_id: 'browser-write', tool_name: 'click', arguments: { operation: 'click', arguments: { idx: 1 }, snapshot_id: 'snap-1', tab_id: 'tab-1', url: 'https://www.meituan.com/order' } }] }
} }
assert.equal(projectHarness(browserApproval).cards.find(c => c.kind === 'confirm')?.title, '确认本次浏览器操作')
const changedPage = { ...browserApproval, state: { ...browserApproval.state, browser_observation: { snapshot_id: 'snap-2', tab_id: 'tab-1' } } }
assert.equal(projectHarness(changedPage).cards.some(c => c.kind === 'confirm'), false)
console.log('Standalone browser approval checks passed')

;(window.xiaonian.harness as any).selectPlan = async (runId: string, planId: string, version: number) => {
  calls.push(`select:${runId}:${planId}:${version}`)
  return snapshot
}
await useStore.getState().selectPlan(card.plan)
assert(calls.includes('select:run-1:plan-1:2'), 'Candidate selection must submit its exact persisted identity/version')
const costed = { ...plan, total_cost: 400, stops: [{ ...plan.stops[0], unit_price: 100 }] }
const expensive = projectHarness({ ...snapshot, state: { ...snapshot.state, place_candidates: [], trip_spec: { per_person_budget: 80 }, candidate_plans: [costed, { ...costed, plan_id: 'plan-2' }] } }).cards.find(c => c.kind === 'plans')!
assert.equal(expensive.variants[0].overBudget, 20, 'Over-budget candidates must not display as within budget')
console.log('Exact candidate selection and budget display checks passed')


const unresolved: HarnessSnapshot = { ...snapshot, phase: 'PARTIAL_FAILED', outcome: 'PARTIAL_FAILED', interrupt_id: null }
assert(canResolveAction(unresolved, 'run-1', 'write-1'))
assert(!canResolveAction(unresolved, 'other-run', 'write-1'))
assert(!canResolveAction(unresolved, 'run-1', 'missing-action'))
assert(!canResolveAction({ ...unresolved, command_pending: true }, 'run-1', 'write-1'))
const userConfirmed: HarnessSnapshot = { ...unresolved, version: 3, phase: 'SUCCEEDED', outcome: 'SUCCEEDED', state: { ...unresolved.state,
  action_results: [{ action_id: 'write-1', status: 'SUCCEEDED', resolution_required: false, result: { source: 'user', scope: 'user_confirmation', note: '已在订单页核对本次预约', reference: 'order-42', automatically_verified: false, user_confirmed: true } }] } }
const manualReceipt = projectHarness(userConfirmed).cards.find(c => c.kind === 'receipt')!
assert.equal(manualReceipt.items[0].source, 'user', 'User assertions are never projected as browser verification')
assert.match(manualReceipt.items[0].detail, /已在订单页核对/)
assert.equal(manualReceipt.items[0].resolution_required, false)
assert(!canResolveAction(userConfirmed, 'run-1', 'write-1'))
let manualCalls = 0
;(window.xiaonian.harness as any).resolveAction = async (runId: string, actionId: string, status: string, note: string) => {
  manualCalls++
  assert.equal(runId, 'run-1'); assert.equal(actionId, 'write-1'); assert.equal(status, 'SUCCEEDED'); assert(note.trim())
  return userConfirmed
}
useStore.setState({ run: unresolved, busy: false, backendReady: true })
await useStore.getState().resolveAction('old-run', 'write-1', 'SUCCEEDED', '旧记录')
await useStore.getState().resolveAction('run-1', 'missing-action', 'SUCCEEDED', '未知动作')
await useStore.getState().resolveAction('run-1', 'write-1', 'SUCCEEDED', '   ')
assert.equal(manualCalls, 0, 'Stale, missing or unsubstantiated manual resolutions cannot reach backend')
await useStore.getState().resolveAction('run-1', 'write-1', 'SUCCEEDED', '已在订单页核对本次预约', 'order-42')
assert.equal(manualCalls, 1)
assert.equal(useStore.getState().run?.version, 3)
assert.equal(useStore.getState().cards.find(c => c.kind === 'receipt')?.items[0].source, 'user')
await useStore.getState().resolveAction('run-1', 'write-1', 'SUCCEEDED', '重复确认')
assert.equal(manualCalls, 1, 'Resolved actions cannot be confirmed again')
console.log('Manual UNKNOWN resolution and provenance checks passed')


const provisionalPlan = { ...plan, stops: [{ ...plan.stops[0], estimated_wait_min: null, travel_min: 0, tags: ['supply_unknown', 'route_unknown'] }] }
const provisional = projectHarness({ ...snapshot, state: { ...snapshot.state, selected_plan: provisionalPlan, candidate_plans: [provisionalPlan],
  verifier: { plan_id: plan.plan_id, executable: false, unknown_evidence: [{ name: 'queue', detail: '排队和路线证据尚未取得' }] } } }).cards.find(c => c.kind === 'plan')!
assert.equal(provisional.plan.nodes[0].wait_min, null, 'Unknown queue duration must not become zero')
assert(provisional.plan.nodes[0].poi?.tags.includes('route_unknown'))
assert(provisional.plan.validation_notes?.some(note => note.includes('排队和路线证据尚未取得')))
assert(provisional.plan.validation_notes?.some(note => note.includes('尚不具备执行条件')))
assert.equal(provisional.plan.nodes[0].verify_state, 'suggested')
console.log('Provisional plan wait/route/verification checks passed')


const concreteBrowserConfirm = projectHarness(browserApproval).cards.find(card => card.kind === 'confirm')!
assert.match(concreteBrowserConfirm.detail, /点击「立即购买/)
assert.match(concreteBrowserConfirm.detail, /https:\/\/www.meituan.com\/order/)
assert(!concreteBrowserConfirm.detail.includes('查看'), 'Model rationale cannot disguise the observed purchase action')
assert(!concreteBrowserConfirm.detail.includes('snapshot_id'), 'Approval should not dump internal transport data')
const noTarget = { ...browserApproval, state: { ...browserApproval.state, browser_observation: { snapshot_id: 'snap-1', tab_id: 'tab-1', url: 'https://www.meituan.com/order', elements: [] } } }
assert(!projectHarness(noTarget).cards.some(card => card.kind === 'confirm'))
assert.match(projectHarness(noTarget).messages.at(-1)!.content, /重新读取页面/)
const itineraryConfirm = projected.cards.find(card => card.kind === 'confirm')!
assert.match(itineraryConfirm.detail, /真实店铺.*10:00.*4 人.*费用待核验/)
assert(!itineraryConfirm.detail.includes('place_id'))
const typedApproval = structuredClone(browserApproval)
const typedAction = ((typedApproval.state.action_proposal as any).actions[0])
typedAction.tool_name = 'type'
typedAction.arguments.operation = 'type'
typedAction.arguments.arguments.text = '张三，2 人，周六 18:00'
assert.match(projectHarness(typedApproval).cards.find(card => card.kind === 'confirm')!.detail, /张三，2 人，周六 18:00/)
console.log('Concrete observed-target and itinerary approval checks passed')


const hiddenRunSession = useStore.getState().sessions.find(session => session.runId === 'run-1')!
assert(hiddenRunSession)
const priorCalls = [...calls]
const priorManualCalls = manualCalls
useStore.getState().deleteSession(hiddenRunSession.id)
assert.equal(useStore.getState().run, null, 'Hiding the active history item resets only the local view')
assert.equal(useStore.getState().cards.length, 0)
assert.equal(useStore.getState().view, 'browser')
assert.equal(cache.get('xy_active_run'), undefined)
assert(!useStore.getState().sessions.some(session => session.runId === 'run-1'))
useStore.getState().persistSession()
assert(!useStore.getState().sessions.some(session => session.runId === 'run-1'), 'Active upserts cannot resurrect hidden runs')
;(window.xiaonian.harness as any).status = async () => ({ ready: true })
;(window.xiaonian.harness as any).listRuns = async () => [snapshot]
await useStore.getState().hydrateHarness()
assert(!useStore.getState().sessions.some(session => session.runId === 'run-1'), 'Backend list refresh cannot resurrect hidden runs')
useStore.getState().applyHarness(snapshot)
assert.equal(useStore.getState().run, null, 'Late snapshots cannot restore a hidden run')
const legacySession = { id: 'legacy-hidden', title: '旧版会话', createdAt: 1, updatedAt: 1, city: '', messages: [{ role: 'user' as const, content: '旧记录' }], cards: [] }
useStore.setState({ sessions: [legacySession], activeSessionId: legacySession.id, messages: legacySession.messages })
useStore.getState().deleteSession(legacySession.id)
useStore.getState().persistSession()
await useStore.getState().hydrateHarness()
assert(!useStore.getState().sessions.some(session => session.id === legacySession.id))
cache.set('xy_sessions', JSON.stringify([hiddenRunSession, { ...hiddenRunSession, id: 'different-local-alias' }, legacySession]))
assert.deepEqual(loadSessions(), [], 'Reload filters hidden backend run aliases and legacy session IDs')
assert.deepEqual(calls, priorCalls, 'Local hiding must not issue backend business mutations')
assert.equal(manualCalls, priorManualCalls)
console.log('Durable local history hiding checks passed')


const inputStep = projectHarness({ ...snapshot, state: { ...snapshot.state, action_results: [{ action_id: 'typed-name', status: 'SUCCEEDED', result: { source: 'browser', scope: 'browser_interaction' } }] } }).cards.find(card => card.kind === 'receipt')!.items[0]
assert.equal(inputStep.status, 'ok', 'Technical browser success remains successful at its own scope')
assert.equal(inputStep.detail, '页面步骤完成，业务结果尚待核验。')
assert.equal(inputStep.business_confirmed, false, 'Technical input completion must not enable business cancel/modify')
assert.equal(projectHarness(userConfirmed).cards.find(card => card.kind === 'receipt')!.items[0].business_confirmed, true)
const verifiedBusiness = projectHarness({ ...snapshot, state: { ...snapshot.state, action_results: [{ action_id: 'verified', status: 'SUCCEEDED', result: { source: 'browser', scope: 'business_receipt', receipt: { identity_verified: true } } }] } }).cards.find(card => card.kind === 'receipt')!.items[0]
assert.equal(verifiedBusiness.business_confirmed, true)
const menuExcerpt = projectHarness({ ...snapshot, state: { browser_artifacts: [{ source: 'browser', title: '真实菜单', data: { menu: [{ name: '时价菜', price: null, quote: '时价菜 询价' }] } }] } }).cards.find(card => card.kind === 'dishes')!
assert.equal(menuExcerpt.mode, 'menu', 'Raw observed dishes must not claim recommendation selection')
assert.equal(menuExcerpt.source, 'browser')
assert.equal(menuExcerpt.dishes[0].price, undefined)
console.log('Scoped browser completion and menu excerpt checks passed')


const comparisonSnapshot: HarnessSnapshot = { ...snapshot, state: { browser_artifacts: [{ type: 'price_comparison', artifact_id: 'comparison-1', source: 'browser', title: '三人用餐价格比较', data: {
  basis: 'per_person', party_size: 3, total_budget: 240,
  entries: [{ name: 'A 店', unit_price: 68, total: 204, within_budget: true, source_url: 'https://example.org/a', quote: 'A 店人均68元', evidence_id: 'price-a' },
    { name: 'B 店', unit_price: 82, total: 246, within_budget: false, source_url: 'https://example.org/b', quote: 'B 店人均82元', evidence_id: 'price-b' }],
  recommendation: 'A 店', savings: 42, summary: '3 人到 A 店共 204 元，在总预算 240 元以内；B 店共 246 元，超出预算。两者相差 42 元。', limitations: ['以页面当前人均计价为依据。']
} }] } }
const comparison = projectHarness(comparisonSnapshot).cards.find(card => card.kind === 'price_comparison')!
assert.equal(comparison.data.party_size, 3)
assert.deepEqual(comparison.data.entries.map(entry => [entry.unit_price, entry.total, entry.within_budget]), [[68, 204, true], [82, 246, false]])
assert.equal(comparison.data.savings, 42)
assert.equal(comparison.data.recommendation, 'A 店')
assert.equal(comparison.data.entries[0].source_url, 'https://example.org/a')
assert.equal(comparison.data.entries[1].evidence_id, 'price-b')
assert.match(comparison.data.summary, /204.*240.*246.*42/)
assert(!projectHarness(comparisonSnapshot).cards.some(card => card.kind === 'browser_page'), 'Comparison must not fall through to an internal JSON dump')
const unknownComparison = structuredClone(comparisonSnapshot)
;(unknownComparison.state.browser_artifacts as any)[0].data.entries[0] = { name: '价格未知', unit_price: null, total: null, within_budget: null }
const unknownEntry = projectHarness(unknownComparison).cards.find(card => card.kind === 'price_comparison')!.data.entries[0]
assert.equal(unknownEntry.unit_price, null)
assert.equal(unknownEntry.total, null)
assert.equal(unknownEntry.within_budget, null)
console.log('Price comparison totals, budget and source projection checks passed')

// A later browser snapshot can move away from already-produced cards; completion must reveal the answer once.
const reading: HarnessSnapshot = { ...comparisonSnapshot, run_id: 'view-regression', phase: 'RESEARCHING', outcome: undefined, version: 1, event_seq: 1, state: { ...comparisonSnapshot.state, turn_id: 1 } }
useStore.setState({ run: null, cards: [], view: 'browser', activeSessionId: 'view-regression', requestBusy: false })
useStore.getState().applyHarness(reading)
useStore.getState().setView('browser')
const finished: HarnessSnapshot = { ...reading, phase: 'SUCCEEDED', outcome: 'SUCCEEDED', version: 2, event_seq: 2 }
useStore.getState().applyHarness(finished)
assert.equal(useStore.getState().view, 'outcome', 'Final answer must become visible after later browser reads')
useStore.getState().setView('browser')
useStore.getState().applyHarness(finished)
assert.equal(useStore.getState().view, 'browser', 'Repeating a completed snapshot must respect manual view selection')

const pageObservations = {
  ...snapshot, state: { evidence: snapshot.state.evidence, browser_artifacts: [
    { type: 'browser_page', source: 'browser', url: 'https://example.org/a', title: 'A 店旧观测', data: { text: '旧报价', menu: [{ name: '午餐', price: 88 }], offers: [{ name: '套餐', price: 128 }] } },
    { type: 'browser_page', source: 'browser', url: 'https://example.org/b', title: 'B 店', data: { text: '另一家店', menu: [{ name: '午餐', price: 90 }] } },
    { type: 'image', source: 'user', url: 'https://example.org/a', data: { text: '截图一' } },
    { type: 'image', source: 'user', url: 'https://example.org/a', data: { text: '截图二' } },
    ...(comparisonSnapshot.state.browser_artifacts as object[]),
    ...(comparisonSnapshot.state.browser_artifacts as object[]),
    { type: 'browser_page', source: 'user', url: 'https://example.org/a', data: { text: '用户提供的页面' } },
    { type: 'browser_page', source: 'browser', data: { text: '无 URL 观测一' } },
    { type: 'browser_page', source: 'browser', data: { text: '无 URL 观测二' } },
    { type: 'browser_page', source: 'browser', url: 'https://example.org/a', title: 'A 店最新观测', observed_at: '2026-09-08T14:00:00Z', data: { text: '最新报价', menu: [{ name: '午餐', price: 68 }], offers: [{ name: '套餐', price: 108 }] } }
  ] }
}
const originalObservations = structuredClone(pageObservations)
const latestPages = projectHarness(pageObservations)
assert.deepEqual(latestPages.cards.filter(c => c.kind === 'dishes').map(c => [c.shopName, c.dishes[0].price]), [['B 店', 90], ['A 店最新观测', 68]], 'Latest prices replace earlier observations only for the same page')
assert.deepEqual(latestPages.cards.filter(c => c.kind === 'groupbuy').map(c => c.packages[0].price), [108])
assert.deepEqual(latestPages.cards.filter(c => c.kind === 'browser_page').map(c => c.text), ['另一家店', '截图一', '截图二', '用户提供的页面', '无 URL 观测一', '无 URL 观测二', '最新报价'])
assert.equal(latestPages.cards.filter(c => c.kind === 'browser_page').at(-1)?.observedAt, '2026-09-08T14:00:00Z')
assert.equal(latestPages.cards.filter(c => c.kind === 'price_comparison').length, 2, 'Non-page comparison artifacts remain separate')
assert.deepEqual(pageObservations, originalObservations, 'Projection must preserve all raw audit artifacts and evidence')
assert.deepEqual(latestPages.evidence, projected.evidence)
console.log('Latest browser page observation projection checks passed')
