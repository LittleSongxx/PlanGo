import type { AgentStep, ChatMessage, HarnessEvidence, HarnessEvent, HarnessSnapshot, OutcomeCard, Plan, POISummary, SourceTag } from '../../../shared/types'

type Row = Record<string, unknown>
export const row = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(row) : []
const str = (value: unknown): string => typeof value === 'string' ? value : ''
const num = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : []
const source = (value: unknown): SourceTag => ['real', 'browser', 'amap', 'user', 'unknown', 'dataset', 'simulated', 'cache', 'fallback'].includes(str(value)) ? value as SourceTag : 'unknown'
const time = (value: unknown): string => num(value) === undefined ? '时间待确认' : `${String(Math.floor(Number(value) / 60)).padStart(2, '0')}:${String(Number(value) % 60).padStart(2, '0')}`

export function phaseLabel(run: HarnessSnapshot | null): string {
  if (!run) return '准备就绪'
  if (run.command_pending) return '处理中'
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
  const places = rows(state.place_candidates)
  const checks = rows(candidate.checks)
  const verifier = row(state.verifier)
  const unknownNotes = rows(verifier.unknown_evidence).map(c => str(c.detail) || str(c.name)).concat(strings(verifier.unknown_evidence)).filter(Boolean)
  const stops = rows(candidate.stops)
  const priceUnknown = stops.some(stop => {
    const place = places.find(p => p.place_id === stop.place_id) || {}
    return stop.price_known === false || place.price_known === false || (num(stop.unit_price) === undefined && num(place.average_price) === undefined)
  })
  const candidateSource = (stop: Row): SourceTag => source(stop.supply_source || places.find(p => p.place_id === stop.place_id)?.source)
  return {
    plan_id: str(candidate.plan_id), run_id: run.run_id, version: num(candidate.version), party_size: num(candidate.party_size) ?? num(row(state.trip_spec).party_size),
    title: str(candidate.label) || '当前方案', style: 'balanced', total_cost: priceUnknown ? null : num(candidate.total_cost) ?? null,
    radar: {}, share_message: str(candidate.rationale),
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
        price_per_person: place.price_known === false || stop.price_known === false ? undefined : num(stop.unit_price) ?? num(place.average_price),
        tags: strings(stop.tags), enable_book: false, enable_reservation: false, business_hours: '', products: [],
        source: candidateSource(stop), recommended: [], is_distraction: false
      }
      return { node_id: `${str(stop.place_id)}:${i}`, time_start: time(stop.start_minute), time_end: time(stop.end_minute), category: kind, title: str(stop.name), poi,
        reason: str(stop.reason) || str(candidate.rationale), locked: stop.locked === true, transit_from_prev_min: num(stop.travel_min) ?? 0, wait_min: num(stop.estimated_wait_min) ?? null,
        verify_state: row(state.verifier).plan_id === candidate.plan_id && row(state.verifier).executable === true ? 'verified' as const : 'suggested' as const }
    })
  }
}

export function projectHarness(run: HarnessSnapshot): { cards: OutcomeCard[]; messages: ChatMessage[]; evidence: HarnessEvidence[] } {
  const state = row(run.state)
  const evidence = evidenceOf(state)
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

  for (const artifact of rows(state.browser_artifacts)) {
    const data = row(artifact.data)
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
    if (menu.length) cards.push({ kind: 'dishes', mode: 'menu', source: source(artifact.source), shopName: str(artifact.title), dishes: menu.filter(x => str(x.name)).map(x => ({ name: str(x.name), price: num(x.price), reason: [str(x.unit), str(x.quote)].filter(Boolean).join(' · ') })) })
    if (offers.length) cards.push({ kind: 'groupbuy', shopName: str(artifact.title), source: 'browser', packages: offers.filter(x => str(x.name)).map(x => ({ name: str(x.name), price: num(x.price) ?? null, originalPrice: num(x.original_price) ?? null, includes: strings(x.conditions), fitPeople: num(x.people) ? `适用 ${x.people} 人` : '适用人数待确认' })) })
    if (str(data.text) || data.tables || (!menu.length && !offers.length)) cards.push({ kind: 'browser_page', title: str(artifact.title) || '页面观测', url: str(artifact.url), text: str(data.text) || JSON.stringify(data.tables || data, null, 2), observedAt: str(artifact.observed_at) })
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
  const results = rows(state.action_results)
  if (results.length) cards.push({ kind: 'receipt', shareMessage: '', items: results.map(a => {
    const result = row(a.result)
    const businessConfirmed = a.status === 'SUCCEEDED' && ((result.scope === 'business_receipt' && row(result.receipt).identity_verified === true) || (result.scope === 'user_confirmation' && result.source === 'user' && result.user_confirmed === true))
    return { business_confirmed: businessConfirmed, run_id: run.run_id, action_id: str(a.action_id), resolution_required: a.status === 'UNKNOWN' && a.resolution_required !== false, label: str(result.shop_name) || str(result.name) || str(a.action_id), status: a.status === 'SUCCEEDED' ? 'ok' : a.status === 'FAILED' || a.status === 'CANCELLED' ? 'fail' : 'pending',
      detail: result.scope === 'browser_interaction' && a.status === 'SUCCEEDED' ? '页面步骤完成，业务结果尚待核验。' : a.status === 'UNKNOWN' ? '提交结果未知，请核查实际订单或业务记录；不会自动重复提交。' : [str(result.note) || str(a.error) || str(result.message) || `${str(a.status)} ${JSON.stringify(result)}`, str(result.reference) ? `业务编号：${str(result.reference)}` : ''].filter(Boolean).join(' · '),
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
  const summary = (needsFreshObservation ? '当前页面目标无法核对，请重新读取页面后再确认。' : '') || str(row(state.clarification).question) || str(wait.message) || str(wait.reason) || str(state.reason) || (plans.length ? `已生成${plans.length > 1 ? `${plans.length} 份候选` : ''}方案，请查看成果区。${run.phase === 'WAITING_APPROVAL' ? '实际操作需在确认卡中批准。' : ''}` : run.outcome ? phaseLabel(run) : '')
  if (summary && messages.at(-1)?.content !== summary) messages.push({ role: 'assistant', content: summary })
  return { cards, messages, evidence }
}

export function projectEvents(events: HarnessEvent[]): AgentStep[] {
  return events.slice(-60).map(e => ({ id: `${e.run_id}:${e.seq}`, label: str(e.payload.label) || e.event_type.replaceAll('_', ' '),
    status: /FAILED|ERROR|TIMEOUT/.test(e.event_type) ? 'error' : 'done', detail: str(e.payload.detail) || str(e.payload.reason) || e.phase }))
}
