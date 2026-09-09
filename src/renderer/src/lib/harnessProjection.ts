import type { AgentStep, ChatMessage, HarnessEvidence, HarnessEvent, HarnessSnapshot, OutcomeCard, Plan, POISummary, SourceTag } from '../../../shared/types'

type Row = Record<string, unknown>
export const row = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(row) : []
const str = (value: unknown): string => typeof value === 'string' ? value : ''
const num = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : []
const source = (value: unknown): SourceTag => ['real', 'browser', 'amap', 'user', 'unknown', 'dataset', 'simulated', 'cache', 'fallback'].includes(str(value)) ? value as SourceTag : 'unknown'
const time = (value: unknown): string => num(value) === undefined ? '时间待确认' : `${String(Math.floor(Number(value) / 60)).padStart(2, '0')}:${String(Number(value) % 60).padStart(2, '0')}`

export function readProgress(run: HarnessSnapshot | null): { observed: string[]; missing: string[]; partial: string[] } {
  const data = row(row(run?.state.execution_outcome).data)
  const labels: Record<string, string> = { merchant: '门店身份', address: '门店地址', menu: '菜单详情', recommended_dishes: '推荐菜', offers: '套餐条目', offer_conditions: '套餐使用条件' }
  const fields = (name: string) => data.scope === 'read_only' && ['SUCCEEDED', 'PARTIAL_FAILED'].includes(run?.phase || '') ? strings(data[name]).map(field => labels[field]).filter(Boolean) : []
  return { observed: fields('observed_fields'), missing: fields('missing_fields'), partial: fields('partial_fields') }
}

export function phaseLabel(run: HarnessSnapshot | null): string {
  if (!run) return '准备就绪'
  if (run.command_pending) return '处理中'
  if (!run.outcome && !['FAILED', 'CANCELLED', 'INFEASIBLE', 'PARTIAL_FAILED', 'SUCCEEDED'].includes(run.phase) && run.draft_review && run.interrupt_id === run.draft_review.interrupt_id) return '草案待核对'
  if (!run.outcome && !['FAILED', 'CANCELLED', 'INFEASIBLE', 'PARTIAL_FAILED', 'SUCCEEDED'].includes(run.phase) && Object.keys(row(run.state.browser_wait)).length) return '等待浏览器处理'
  const outcome = row(run.state.execution_outcome), result = row(outcome.data)
  const completed = run.phase === 'SUCCEEDED' && outcome.status === 'satisfied'
  if (['SUCCEEDED', 'PARTIAL_FAILED'].includes(run.phase) && result.business_completed === false) {
    if (result.scope === 'draft_ready') return completed ? '草案已保存 · 待核验' : '草案保存待核对'
    if (result.scope === 'ready_to_review') return completed ? '准备就绪 · 待核对' : '准备事项待完善'
    if (result.scope === 'preparation_incomplete') return '准备事项待完善'
    if (result.scope === 'image_text') return completed ? '图片识别已完成' : '图片识别待补充'
    if (result.scope === 'read_only') return completed ? '资料读取已完成' : '资料读取待补充'
  }
  return ({ CREATED: '已接收', PENDING: '排队中', RUNNING: '运行中', REQUIREMENTS_READY: '已理解需求', RESEARCHING: '查找真实信息', PLAN_DRAFTED: '方案已生成', REVIEWING: '校验方案', WAITING_APPROVAL: '等待确认', WAITING_BROWSER: '等待浏览器', EXECUTING: '执行中', REPLANNING: '重新规划', SUCCEEDED: '已完成', PARTIAL_FAILED: '部分完成', INFEASIBLE: '当前约束下不可行', FAILED: '运行失败', CANCELLED: '已取消' } as Record<string, string>)[run.phase] || run.phase
}

export function runBusy(run: HarnessSnapshot | null): boolean {
  return !!run && (!!run.command_pending || (!run.outcome && !run.interrupt_id && !Object.keys(row(run.state.browser_wait)).length && !['WAITING_APPROVAL', 'WAITING_BROWSER', 'SUCCEEDED', 'PARTIAL_FAILED', 'INFEASIBLE', 'FAILED', 'CANCELLED'].includes(run.phase)))
}

export function canResolveAction(run: HarnessSnapshot | null, runId: string, actionId: string): boolean {
  return !!run && run.run_id === runId && !run.command_pending && rows(run.state.action_results).some(action => action.action_id === actionId && action.status === 'UNKNOWN' && action.resolution_required !== false)
}

function evidenceOf(state: Row): HarnessEvidence[] {
  return rows(state.evidence).map(e => ({ evidence_id: str(e.evidence_id), source: source(e.source), source_ref: str(e.source_ref), claim: str(e.claim), observed_at: str(e.observed_at) || undefined, expires_at: str(e.expires_at) || undefined }))
}

function projectPlan(candidate: Row, run: HarnessSnapshot, evidence: HarnessEvidence[]): Plan {
  const state = run.state
  const spec = row(state.trip_spec)
  const cap = num(spec.budget) ?? (num(spec.per_person_budget) !== undefined && num(spec.party_size) !== undefined ? Number(spec.per_person_budget) * Number(spec.party_size) : undefined)
  const places = rows(state.place_candidates)
  const checks = rows(candidate.checks)
  const verifier = row(state.verifier)
  const unknownNotes = rows(verifier.unknown_evidence).map(c => str(c.detail) || str(c.name)).concat(strings(verifier.unknown_evidence)).filter(Boolean)
  const stops = rows(candidate.stops)
  const unitPrice = (stop: Row): number | undefined => {
    const place = places.find(p => p.place_id === stop.place_id) || {}
    return stop.price_known === false || place.price_known === false || strings(stop.tags).includes('price_unknown') ? undefined : num(stop.unit_price) ?? num(place.average_price)
  }
  const supplyUnknown = (stop: Row): boolean => source(stop.supply_source) === 'unknown' || num(stop.estimated_wait_min) === undefined || strings(stop.tags).includes('supply_unknown')
  const partySize = num(candidate.party_size) ?? num(spec.party_size)
  const totalCost = !stops.length || stops.some(stop => unitPrice(stop) === undefined) ? null : num(candidate.total_cost) ?? null
  const pending = [
    ...(stops.some(supplyUnknown) ? ['营业/排队'] : []),
    ...(stops.some(stop => source(stop.supply_source) === 'unknown' || strings(stop.tags).includes('reservation_unknown')) ? ['可订情况'] : []),
    ...(stops.some(stop => stop.distance_kind !== 'route' || strings(stop.tags).includes('route_unknown')) ? ['路线'] : [])
  ]
  const candidateSource = (stop: Row): SourceTag => source(places.find(p => p.place_id === stop.place_id)?.source || stop.supply_source)
  return {
    plan_id: str(candidate.plan_id), run_id: run.run_id, version: num(candidate.version), party_size: partySize,
    visit_date: str(spec.visit_date) || undefined, timezone: str(spec.timezone) || undefined,
    origin: num(row(spec.location).latitude) !== undefined && num(row(spec.location).longitude) !== undefined ? { name: str(row(spec.location).name), latitude: Number(row(spec.location).latitude), longitude: Number(row(spec.location).longitude) } : undefined,
    travel_mode: ['driving', 'walking', 'transit'].includes(str(spec.travel_mode)) ? spec.travel_mode as 'driving' | 'walking' | 'transit' : undefined,
    title: ['PlanDraft', 'PlanCandidate', '确定性 fallback'].includes(str(candidate.label)) ? '基础方案' : str(candidate.label) || '当前方案', budget_limit: cap ?? (spec.budget === null && spec.per_person_budget === null ? null : undefined), style: 'balanced', total_cost: totalCost,
    // Historical model prose remains in the snapshot; only structured facts become shareable claims.
    radar: {}, share_message: `行程草案：${stops.map(stop => str(stop.name)).join(' → ')}；${partySize === undefined ? '人数待确认' : `${partySize} 人`}；${totalCost === null ? '总费用待核验' : `估算总费用 ¥${totalCost}`}。${pending.length ? `待核验：${pending.join('、')}。` : ''}`,
    evidence: evidence.filter(e => strings(candidate.evidence_ids).includes(e.evidence_id) || stops.some(s => strings(s.evidence_ids).includes(e.evidence_id))),
    validation_notes: [...new Set(checks.filter(c => c.passed !== true).map(c => str(c.detail) || str(c.name)).concat(unknownNotes, verifier.executable === false ? ['当前是待核验方案，尚不具备执行条件。'] : []))],
    source_mix: Object.fromEntries([...new Set(stops.map(candidateSource))].map(s => [s, stops.filter(x => candidateSource(x) === s).length])),
    nodes: stops.map((stop, i) => {
      const place = places.find(p => p.place_id === stop.place_id) || {}
      const kind = /餐|美食|咖啡|dining|restaurant|food/i.test(str(stop.category)) ? 'dining' as const : 'activity' as const
      const poi: POISummary = {
        poi_id: str(stop.place_id), name: str(stop.name), category: str(stop.category),
        raw_score: source(place.source) === 'browser' && place.rating_known !== true ? null : num(place.rating) ?? null,
        trust: 'unknown', trust_reason: '', address: str(place.address), city: str(row(row(state.trip_spec).location).name),
        lng: num(place.longitude), lat: num(place.latitude),
        price_per_person: unitPrice(stop),
        tags: strings(stop.tags), enable_book: false, enable_reservation: false, business_hours: '', products: [],
        source: candidateSource(stop), recommended: [], is_distraction: false
      }
      return { node_id: `${str(stop.place_id)}:${i}`, time_start: time(stop.start_minute), time_end: time(stop.end_minute), category: kind, title: str(stop.name), poi,
        reason: `计划停留 ${time(stop.start_minute)}–${time(stop.end_minute)}；${poi.price_per_person === undefined ? '费用待核验' : `人均估算 ¥${poi.price_per_person}`}。${supplyUnknown(stop) ? '营业/排队待核验。' : ''}`, locked: stop.locked === true, transit_from_prev_min: strings(stop.tags).includes('route_unknown') || stop.distance_kind === 'straight_line_lower_bound' ? null : num(stop.travel_min) ?? null, distance_km: num(stop.distance_km) ?? null,
        distance_kind: ['route', 'straight_line_lower_bound'].includes(str(stop.distance_kind)) ? stop.distance_kind as 'route' | 'straight_line_lower_bound' : undefined, wait_min: supplyUnknown(stop) ? null : num(stop.estimated_wait_min) ?? null,
        verify_state: row(state.verifier).plan_id === candidate.plan_id && row(state.verifier).executable === true ? 'verified' as const : 'suggested' as const }
    })
  }
}

export function projectHarness(run: HarnessSnapshot): { cards: OutcomeCard[]; messages: ChatMessage[]; evidence: HarnessEvidence[] } {
  const state = row(run.state)
  const evidence = evidenceOf(state)
  const readOutcome = row(state.execution_outcome), readGoal = row(state.execution_goal)
  const currentRead = ['SUCCEEDED', 'PARTIAL_FAILED'].includes(run.phase) && !run.command_pending && !run.cancel_requested && !run.interrupt_id && !state.browser_wait
    && ['page_read', 'menu_read'].includes(str(readOutcome.kind)) && readGoal.kind === readOutcome.kind && row(readOutcome.data).scope === 'read_only'
  const readEvidenceIds = currentRead ? strings(readOutcome.evidence_ids) : []
  const cards: OutcomeCard[] = []
  const candidateRows = rows(state.candidate_plans)
  const selected = row(state.selected_plan)
  const plans = (candidateRows.length ? candidateRows : selected.plan_id ? [selected] : []).map(p => projectPlan(p, run, evidence))
  if (plans.length > 1) {
    const budget = num(row(state.trip_spec).per_person_budget)
    cards.push({ kind: 'plans', city: str(row(row(state.trip_spec).location).name), budget, variants: plans.map(p => {
      const per = p.total_cost !== null && p.party_size ? Math.round(p.total_cost / p.party_size * 100) / 100 : null
      return { plan: p, styleLabel: p.title, per, overBudget: per !== null && budget !== undefined && per > budget ? Math.round((per - budget) * 100) / 100 : undefined }
    }) })
  }
  else if (plans.length) cards.push({ kind: 'plan', plan: plans[0] })
  if (evidence.length) cards.push({ kind: 'evidence', items: evidence })

  // Receipt backfill may append an older command after the canonical latest observation.
  const observed = rows(state.browser_artifacts), latestPages = new Map<string, Row>()
  const pageKey = (artifact: Row) => artifact.type === 'browser_page' && str(artifact.url) ? JSON.stringify([str(artifact.source), artifact.type, artifact.url]) : ''
  const capturedAt = (artifact: Row) => Date.parse(str(artifact.observed_at)) || 0
  const isCurrentPage = (artifact: Row) => !!str(row(state.browser_observation).snapshot_id) && artifact.snapshot_id === row(state.browser_observation).snapshot_id && artifact.url === row(state.browser_observation).url
  for (const artifact of observed) {
    const key = pageKey(artifact), previous = latestPages.get(key)
    if (key && (!previous || capturedAt(artifact) > capturedAt(previous) || (capturedAt(artifact) === capturedAt(previous) && (isCurrentPage(artifact) || !isCurrentPage(previous))))) latestPages.set(key, artifact)
  }
  const artifacts = observed.filter(artifact => !pageKey(artifact) || latestPages.get(pageKey(artifact)) === artifact)
  // A guarded stop can persist its preparation outcome before an optional artifact is emitted.
  // The current canonical outcome wins over an older success artifact for the same goal.
  const preparation = row(state.execution_outcome), preparationGoal = row(state.execution_goal)
  if (preparation.kind === 'itinerary_preparation' && preparationGoal.kind === 'itinerary_preparation' && str(preparationGoal.approval_id)) {
    const artifactId = `preparation:${preparationGoal.approval_id}`
    const index = artifacts.findIndex(artifact => artifact.type === 'browser_preparation' && artifact.artifact_id === artifactId)
    const observation = row(state.browser_observation), observationId = `page:${str(observation.command_id)}`
    const linked = strings(preparation.evidence_ids).includes(observationId) || rows(row(preparation.data).issues).some(issue => rows(issue.differences).some(difference => difference.evidence_id === observationId))
    const sameArtifact = index >= 0 && JSON.stringify(artifacts[index].data) === JSON.stringify(preparation)
    const projected = { type: 'browser_preparation', artifact_id: artifactId, data: preparation, observed_at: linked ? observation.observed_at : sameArtifact ? artifacts[index].observed_at : undefined }
    if (index < 0) artifacts.push(projected)
    else artifacts[index] = projected
  }
  for (const artifact of artifacts) {
    const data = row(artifact.data)
    if (artifact.type === 'browser_preparation') {
      const result = row(data.data), goal = row(state.execution_goal), stops = rows(goal.stops)
      const name = (placeId: unknown) => str(stops.find(stop => stop.place_id === placeId)?.name) || '待核对地点'
      const current = !run.command_pending && !run.cancel_requested && !state.browser_wait && !!str(goal.approval_id) && !!str(selected.plan_id) && artifact.artifact_id === `preparation:${str(goal.approval_id)}` && goal.plan_id === selected.plan_id && goal.plan_version === selected.version
      const resume = run.preparation_resume
      cards.push({ kind: 'preparation', current, resume: current && resume && resume.plan_id === goal.plan_id && resume.plan_version === goal.plan_version && resume.approval_id === goal.approval_id ? { ...resume, run_id: run.run_id } : undefined, ready: current && data.status === 'satisfied' && result.scope === 'ready_to_review' && result.business_completed === false,
        summary: current ? str(data.summary) : '这是先前方案的准备记录，当前方案需要重新核对。', pendingChecks: rows(result.pending_checks).map(check => str(check.detail) || str(check.name)), observedAt: str(artifact.observed_at),
        entries: rows(result.entries).map(entry => ({ name: name(entry.place_id), address: str(stops.find(stop => stop.place_id === entry.place_id)?.address),
          partySize: num(entry.party_size), date: str(entry.visit_date), time: str(entry.visit_time), timezone: str(entry.timezone) })),
        issues: rows(result.issues).map(issue => ({ name: name(issue.place_id), mismatch: issue.status === 'mismatch', detail: [str(issue.detail), ...rows(issue.differences).map(difference => {
          const expected = row(difference.expected), observed = row(difference.observed)
          return `目标：${str(expected.party_size) || '未知'} 人 · ${str(expected.date) || '日期未知'} ${str(expected.time) || ''}；页面：${str(observed.party_size) || '未知'} 人 · ${str(observed.date) || '日期未知'} ${str(observed.time) || ''}`
        })].filter(Boolean).join('；') })) })
      continue
    }
    if (artifact.type === 'browser_visual') {
      cards.push({ kind: 'browser_page', title: str(artifact.title) || '截图理解', url: str(artifact.url), text: str(data.visual_text), source: source(artifact.source),
        observedAt: str(artifact.observed_at), scope: 'visual_observation', limitations: strings(data.limitations) })
      continue
    }
    if (artifact.type === 'price_comparison' && data.basis === 'per_person') {
      cards.push({ kind: 'price_comparison', title: str(artifact.title) || '价格比较', source: source(artifact.source), data: {
        basis: 'per_person', party_size: num(data.party_size) ?? null, total_budget: num(data.total_budget) ?? null,
        entries: rows(data.entries).map(entry => ({ name: str(entry.name), unit_price: num(entry.unit_price) ?? null, total: num(entry.total) ?? null,
          within_budget: num(entry.total) !== undefined && num(data.total_budget) !== undefined && typeof entry.within_budget === 'boolean' ? entry.within_budget : null,
          source_url: str(entry.source_url), quote: str(entry.quote), evidence_id: str(entry.evidence_id) })),
        recommendation: str(data.recommendation) || null, savings: num(data.savings) ?? null, summary: str(data.summary), limitations: strings(data.limitations)
      } })
      continue
    }
    const menu = rows(data.menu)
    const offers = rows(data.offers)
    const places = rows(data.places).filter(place => str(place.name))
    const title = (places.length === 1 ? str(places[0].name) : '') || str(artifact.title).match(/^【([^】]+)】/)?.[1] || str(artifact.title) || (artifact.type === 'image' ? '图片识别' : '页面观测')
    const readFields = readEvidenceIds.includes(str(artifact.artifact_id)) ? strings(row(readOutcome.data).observed_fields) : []
    if (menu.length) cards.push({ kind: 'dishes', mode: readFields.includes('menu') ? 'menu' : readFields.includes('recommended_dishes') ? 'recommended_dishes' : 'excerpt', source: source(artifact.source), shopName: title, dishes: menu.filter(x => str(x.name)).map(x => ({ name: str(x.name), price: num(x.price), priceUnit: str(x.unit) || undefined, reason: str(x.quote) })) })
    if (offers.length) cards.push({ kind: 'groupbuy', shopName: title, source: source(artifact.source), packages: offers.filter(x => str(x.name)).map(x => ({ name: str(x.name), price: num(x.price) ?? null, originalPrice: num(x.original_price) ?? null, includes: strings(x.conditions), fitPeople: num(x.people) ? `适用 ${x.people} 人` : '适用人数待确认', quote: str(x.quote) || undefined })) })
    if (str(data.text) || data.tables || places.length || (!menu.length && !offers.length)) {
      const tableText = rows(data.tables).map(table => [strings(table.headers).join(' · '), ...(Array.isArray(table.rows) ? table.rows.filter(Array.isArray).map(cells => cells.map(value => String(value ?? '')).join(' · ')) : [])].filter(Boolean).join('\n')).filter(Boolean).join('\n\n')
      cards.push({ kind: 'browser_page', title, rawTitle: str(artifact.title), scope: artifact.type === 'image' ? 'image_text' : undefined, url: str(artifact.url), text: str(data.text) || tableText || '此页面暂无可直接显示的文字摘录，可在浏览器中继续查看。', source: source(artifact.source), observedAt: str(artifact.observed_at),
        menuCount: menu.length, offerCount: offers.length, places: places.map(place => ({ name: str(place.name), address: str(place.address) || undefined, averagePrice: num(place.average_price), priceUnit: str(place.price_unit) || undefined, quote: str(place.quote) || undefined })) })
    }
  }

  const proposal = row(state.action_proposal)
  const interruptId = run.interrupt_id || str(state.interrupt_id)
  // Bind both plan approvals and standalone browser approvals to their current
  // durable artifact. The latter intentionally has no fabricated selected_plan.
  const browserAction = row(state.browser_action)
  const browserObservation = row(state.browser_observation)
  const proposedActions = rows(proposal.actions)
  const proposedBrowserAction = proposedActions.find(action => action.action_id === browserAction.action_id) || {}
  const approvedPage = row(proposedBrowserAction.arguments)
  const approvedArguments = row(approvedPage.arguments)
  const approvedIndex = num(approvedArguments.idx)
  const observedTarget = approvedIndex === undefined ? undefined : rows(browserObservation.elements).find(element => element.idx === approvedIndex)
  const targetLabel = observedTarget ? [...new Set([str(observedTarget.text), str(observedTarget.name)].filter(Boolean))].join(' / ') : ''
  let pageUrl: URL | undefined
  try { pageUrl = new URL(str(browserObservation.url)) } catch { /* Invalid/missing source cannot support an approval. */ }
  const matchesBrowser = proposal.plan_id === `browser-artifact:${run.run_id}:${state.browser_steps}` && proposal.run_id === run.run_id && proposal.plan_version === state.turn_id &&
    proposedActions.length === 1 && !!browserAction.action_id && !!targetLabel &&
    !!pageUrl && ['http:', 'https:'].includes(pageUrl.protocol) && !pageUrl.username && !pageUrl.password &&
    approvedPage.url === browserObservation.url && approvedPage.snapshot_id === browserObservation.snapshot_id && approvedPage.tab_id === browserObservation.tab_id &&
    !!browserAction.snapshot_id && browserAction.snapshot_id === browserObservation.snapshot_id && browserAction.tab_id === browserObservation.tab_id &&
    ['click', 'type'].includes(str(proposedBrowserAction.tool_name)) && approvedPage.operation === proposedBrowserAction.tool_name &&
    (proposedBrowserAction.tool_name !== 'type' || typeof approvedArguments.text === 'string')
  const selectedStops = rows(selected.stops)
  const matchesPlan = !!selected.plan_id && proposal.plan_id === selected.plan_id && proposal.plan_version === selected.version && proposedActions.length > 0 &&
    proposedActions.every(action => ['reserve_place', 'visit_place'].includes(str(action.tool_name)) && selectedStops.some(stop => stop.place_id === row(action.arguments).place_id))
  const needsFreshObservation = run.phase === 'WAITING_APPROVAL' && str(proposal.plan_id).startsWith('browser-artifact:') && !matchesBrowser
  if (run.phase === 'WAITING_APPROVAL' && interruptId && proposal.proposal_id && (matchesPlan || matchesBrowser) && (!proposal.run_id || proposal.run_id === run.run_id) && (!proposal.expires_at || new Date(str(proposal.expires_at)).getTime() > Date.now()) && !row(state.clarification).question && !state.browser_wait && !run.command_pending) {
    let detail: string
    if (matchesBrowser) {
      detail = `网站：${pageUrl!.host}\n页面：${pageUrl!.href}\n操作：${proposedBrowserAction.tool_name === 'type' ? '输入文字到' : '点击'}「${targetLabel}」`
      if (proposedBrowserAction.tool_name === 'type') detail += `\n待输入文本（完整内容）：\n${approvedArguments.text === '' ? '（空文本，将清空此输入项）' : String(approvedArguments.text)}`
    } else {
      const currentPlan = projectPlan(selected, run, evidence)
      detail = ['确认行程后会开始准备页面；预约、下单或支付等提交会再次确认。', ...proposedActions.map(action => {
        const args = row(action.arguments)
        const index = selectedStops.findIndex(stop => stop.place_id === args.place_id)
        const stop = selectedStops[index]
        const people = num(args.party_size)
        const cost = currentPlan.nodes[index]?.poi?.price_per_person !== undefined ? num(args.estimated_cost) : undefined
        return `${action.tool_name === 'reserve_place' ? '准备预约页面' : '安排到访'}：${str(stop.name)} · ${time(args.at_minute)} · ${people === undefined ? '人数待确认' : `${people} 人`} · ${cost === undefined ? '费用待核验' : `约 ¥${cost}`}`
      })].join('\n')
    }
    cards.push({ kind: 'confirm', token: interruptId, title: matchesBrowser ? '确认本次浏览器操作' : `确认行程 · 方案 v${proposal.plan_version}`, detail, danger: true })
  }
  const draft = run.draft_review
  if (!run.outcome && !['FAILED', 'CANCELLED', 'INFEASIBLE', 'PARTIAL_FAILED', 'SUCCEEDED'].includes(run.phase) && draft?.scope === 'draft_ready' && draft.interrupt_id === run.interrupt_id && draft.plan_id === selected.plan_id && draft.plan_version === selected.version && !run.command_pending) {
    const conflicts = rows(draft.conflicts).filter(check => check.passed !== true).map(check => str(check.detail) || str(check.name))
    const blockers = strings(draft.preparation_blockers)
    cards.push({ kind: 'draft_review', draft: { runId: run.run_id, interruptId: draft.interrupt_id, planId: draft.plan_id, planVersion: draft.plan_version,
      unknowns: rows(draft.unknowns).map(check => str(check.detail) || str(check.name)), canPrepare: draft.can_prepare === true && !conflicts.length && !blockers.length,
      blockedReason: [...new Set([...conflicts, ...blockers])].join('；') || undefined } })
  }
  const results = rows(state.action_results)
  if (results.length) cards.push({ kind: 'receipt', shareMessage: '', items: results.map(a => {
    const result = row(a.result)
    const observation = row(result.observation)
    const operationDetail = str(result.note) || str(observation.error) || str(a.error) || str(result.message) || (a.status === 'SUCCEEDED' ? '该步骤已结束，业务结果仍需核对。' : '该步骤未完成，请查看当前页面。')
    const businessConfirmed = a.status === 'SUCCEEDED' && ((result.scope === 'business_receipt' && row(result.receipt).identity_verified === true) || (result.scope === 'user_confirmation' && result.source === 'user' && result.user_confirmed === true))
    return { business_confirmed: businessConfirmed, run_id: run.run_id, action_id: str(a.action_id), resolution_required: a.status === 'UNKNOWN' && a.resolution_required !== false, label: str(result.shop_name) || str(result.name) || (result.scope === 'browser_interaction' || result.source === 'browser' ? '页面操作' : '操作记录'), status: a.status === 'SUCCEEDED' ? 'ok' : a.status === 'FAILED' || a.status === 'CANCELLED' ? 'fail' : 'pending',
      detail: result.scope === 'browser_interaction' && a.status === 'SUCCEEDED' ? '页面步骤完成，业务结果尚待核验。' : a.status === 'UNKNOWN' ? '提交结果未知，请核查实际订单或业务记录；不会自动重复提交。' : [operationDetail, str(result.reference) ? `业务编号：${str(result.reference)}` : ''].filter(Boolean).join(' · '),
      source: source(result.source) }
  }) })

  const messages: ChatMessage[] = rows(state.messages).flatMap(m => {
    const role = m.role || m.type
    const content = str(m.content) || (Array.isArray(m.content) ? rows(m.content).map(x => str(x.text)).join('\n') : '')
    return content && ['human', 'user', 'ai', 'assistant'].includes(str(role)) ? [{ role: role === 'human' || role === 'user' ? 'user' as const : 'assistant' as const, content }] : []
  })
  if (!messages.length && run.input_text) messages.push({ role: 'user', content: run.input_text })
  const wait = row(state.browser_wait)
  const pending = str(state.pending_message)
  if (pending && messages.at(-1)?.content !== pending) messages.push({ role: 'user', content: pending })
  const summary = (run.outcome ? str(state.reason) : '') || (needsFreshObservation ? '当前页面目标无法核对，请重新读取页面后再确认。' : '') || str(row(state.clarification).question) || str(wait.message) || str(wait.reason) || str(state.reason) || (plans.length ? `已生成${plans.length > 1 ? `${plans.length} 份候选` : ''}方案，请查看成果区。${run.phase === 'WAITING_APPROVAL' ? '实际操作需在确认卡中批准。' : ''}` : run.outcome ? phaseLabel(run) : '')
  if (summary && messages.at(-1)?.content !== summary) messages.push({ role: 'assistant', content: summary })
  return { cards, messages, evidence }
}

const eventLabels: Record<string, string> = {
  USER_MESSAGE: '补充要求已收到', GRAPH_INTERRUPTED: '等待你的下一步', REPLAN_REQUESTED: '按新需求调整', REQUIREMENTS_EDITED: '行程需求已更新',
  SUPERVISOR_DECISION: '确认下一处理步骤', DISCOVERY_COMPLETE: '地点资料已查到',
  ADVOCATE_FANOUT_STARTED: '正在核对同行需求', ADVOCATE_COMPLETE: '同行需求已核对',
  PLAN_SYNTHESIZED: '方案草稿已生成', PLAN_VERIFIED: '方案核验已完成',
  CLARIFICATION_REQUESTED: '需要补充要求', CLARIFICATION_RECEIVED: '补充要求已收到',
  BUDGET_EXHAUSTED: '本轮处理时限或额度已到', PLAN_REPAIR_SELECTED: '已选择修订方案',
  BROWSER_COMMAND_DISPATCHED: '浏览器操作已发出',
  BROWSER_COMMAND_BLOCKED: '操作范围受限，等待人工核对',
  DRAFT_DECISION_REQUESTED: '正在处理草案选择', DRAFT_SAVED: '草案已保存', DRAFT_PREPARATION_STARTED: '正在核对表单',
  RUN_CREATED: '任务已接收', RUN_STARTED: '开始处理', RUN_GRAPH_INTERRUPTED: '等待下一步',
  BROWSER_OBSERVATION: '浏览器观测结果', BROWSER_RESUME_REQUESTED: '继续处理页面', BROWSER_OBSERVED: '页面内容已读取',
  RUN_MESSAGE_RECEIVED: '补充要求已收到', RUN_REPLAN_REQUESTED: '正在调整方案', RUN_PLAN_SELECTED: '已选择方案',
  RUN_FINALIZED: '本轮处理结束', RUN_FAILED: '任务未能完成', RUN_CANCELLED: '任务已停止',
  CONNECTION_ERROR: '连接暂时中断', IMAGE_EXTRACTED: '图片识别结果已保存',
  REQUIREMENTS_READY: '需求已整理', RESEARCH_COMPLETED: '资料查找完成', PLAN_DRAFTED: '候选方案已生成',
  APPROVAL_REQUESTED: '等待你的确认', APPROVAL_RESOLVED: '确认结果已收到', MEMORY_RETRIEVED: '已读取保存的偏好',
  BROWSER_VISION_COMPLETED: '截图理解已完成'
}
const eventPhaseLabels: Record<string, string> = { CREATED: '已接收', PENDING: '排队中', RUNNING: '处理中', REQUIREMENTS_READY: '需求已整理', RESEARCHING: '查找资料', PLAN_DRAFTED: '方案已生成', REVIEWING: '核对信息', WAITING_APPROVAL: '等待确认', WAITING_BROWSER: '等待浏览器', EXECUTING: '执行中', REPLANNING: '调整方案', SUCCEEDED: '本轮已结束', PARTIAL_FAILED: '仍有待处理事项', INFEASIBLE: '当前要求无法同时满足', FAILED: '未能完成', CANCELLED: '已停止' }

export function projectEvents(events: HarnessEvent[]): AgentStep[] {
  return events.map(e => {
    const kind = e.event_type.toUpperCase()
    const outcome = (str(e.payload.outcome) || str(e.payload.status) || (kind === 'RUN_FINALIZED' ? str(e.payload.phase) || str(e.phase) : '')).toUpperCase()
    const status: AgentStep['status'] = /FAILED|ERROR|BLOCKED|TIMEOUT|EXHAUSTED|INFEASIBLE/.test(kind) || ['FAILED', 'ERROR', 'BLOCKED', 'CANCELLED', 'PARTIAL_FAILED', 'INFEASIBLE'].includes(outcome) ? 'error'
      : outcome === 'UNKNOWN' || /WAITING|REQUESTED|QUEUED|PENDING|PAUSED|INTERRUPTED/.test(kind) || ['browser', 'approval'].includes(str(e.payload.type)) ? 'waiting'
      : ['SUCCEEDED', 'OBSERVED', 'EXECUTED'].includes(outcome) || /(?:^|_)(?:SUCCEEDED|COMPLETE|COMPLETED|READY|RESOLVED|RETRIEVED|REFLECTED|RECEIVED|EXTRACTED|OBSERVED)(?:_|$)/.test(kind) ? 'done'
      : /^WAITING_/.test(str(e.phase)) ? 'waiting'
      : /STARTED|RUNNING|EXECUTING/.test(kind) ? 'running' : 'idle'
    return { id: `${e.run_id}:${e.seq}`, label: str(e.payload.label) || eventLabels[kind] || '任务进展已更新', status, detail: str(e.payload.detail) || str(e.payload.message) || str(e.payload.reason) || eventPhaseLabels[str(e.phase)] }
  })
}
