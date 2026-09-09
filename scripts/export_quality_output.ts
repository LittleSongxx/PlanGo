/**
 * Offline scoring input, using the production projection and React card renderer.
 * CLI: node_modules/.bin/tsx --tsconfig tsconfig.web.json scripts/export_quality_output.ts INPUT.json OUTPUT.json
 *      node_modules/.bin/tsx --tsconfig tsconfig.web.json scripts/export_quality_output.ts --self-test
 * Input: HarnessSnapshot, or { snapshot: HarnessSnapshot, events?: HarnessEvent[],
 *          checkpoint?: { id?: string, captured_at?: string, as_of?: string } }.
 * Output schema: plango.quality-output.v1 (see exportQualityOutput return value).
 * Each file represents ONE registered delivery checkpoint. Events recover replies,
 * not previous card states: the runner must capture every required checkpoint.
 * No browser, effects, network, real desktop storage, .env, scoring or model calls.
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { Children, Fragment, createElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { projectHarness, readProgress, row, rows } from '../src/renderer/src/lib/harnessProjection'
import { isBookingPreviewUrl } from '../src/shared/bookingPreview'
import type { HarnessEvent, HarnessSnapshot, OutcomeCard } from '../src/shared/types'

const str = (value: unknown): string => typeof value === 'string' ? value : ''
const privateKey = /(?:key|token|secret|password|authorization|cookie|credential)s?$/i
const omittedKeys = /^(?:token|interruptId|interrupt_id|approval_id|image|images|image_data|image_payload|request|request_payload|model_calls|prompt|messages|headers|browser_binding)$/i
const hash = (value: string): string => createHash('sha256').update(value).digest('hex')

function sanitizer(input: unknown): (value: unknown) => unknown {
  const secrets = new Set<string>()
  const collect = (value: unknown): void => {
    if (!value || typeof value !== 'object') return
    for (const [key, item] of Object.entries(value)) {
      if ((privateKey.test(key) || key === 'token') && typeof item === 'string' && item.length >= 4) secrets.add(item)
      else collect(item)
    }
  }
  collect(input)
  const cleanString = (value: string): string => {
    let clean = value.replace(/data:image\/[^\s)"'<>]+/gi, '[image payload omitted]')
    for (const secret of secrets) clean = clean.split(secret).join('[redacted]')
    clean = clean.replace(/\b(?:sk|sess)[-_][A-Za-z0-9_-]{16,}\b/g, '[redacted]')
    // The guarded preview's exact three parameters support its year/party/time
    // claims. Any extra query, credentials or different URL fails that guard.
    return clean.replace(/https?:\/\/[^\s<>"'）)]+/gi, value => {
      try { const url = new URL(value); const safePreview = isBookingPreviewUrl(value); url.username = ''; url.password = ''; if (!safePreview) url.search = ''; url.hash = ''; return url.href }
      catch { return '[invalid source URL]' }
    })
  }
  const clean = (value: unknown): unknown => {
    if (typeof value === 'string') return cleanString(value)
    if (Array.isArray(value)) return value.map(clean)
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !privateKey.test(key) && !omittedKeys.test(key)).map(([key, item]) => [key, clean(item)]))
    return value
  }
  return clean
}

// The input here is escaped React-generated HTML, never untrusted raw HTML.
// Keep collapsed details and table cells; discard SVG glyphs and input controls.
function textOf(html: string): string {
  return html.replace(/<(svg|script|style|button|textarea|select)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
    .replace(/<input\b[^>]*\/?>/gi, '').replace(/<\/?(?:div|p|h[1-6]|li|ul|ol|tr|th|td|br|pre|blockquote|summary|section)\b[^>]*>/gi, '\n')
    .replace(/<[^>]*>/g, '').replace(/&(amp|lt|gt|quot|apos|#x[0-9a-f]+|#\d+);/gi, (_, entity: string) => {
      if (entity[0] === '#') return String.fromCodePoint(parseInt(entity.slice(entity[1].toLowerCase() === 'x' ? 2 : 1), entity[1].toLowerCase() === 'x' ? 16 : 10))
      return ({ amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" } as Record<string, string>)[entity.toLowerCase()]
    }).replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

async function cardRenderer(run: HarnessSnapshot): Promise<(card: OutcomeCard) => string> {
  // Importing the renderer initializes its store. Give it ONLY empty in-memory
  // storage, never a user's browser profile or saved draft.
  const memory = new Map<string, string>()
  const storage = { getItem: (key: string) => memory.get(key) ?? null, setItem: (key: string, value: string) => { memory.set(key, value) }, removeItem: (key: string) => { memory.delete(key) } }
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'window', { value: {}, configurable: true })
  let renderer: { OutcomeCanvas: () => ReactElement }, store: typeof import('../src/renderer/src/store')
  try {
    store = await import('../src/renderer/src/store')
    // A URL import keeps TSX out of the node-only typecheck compilation; tsx
    // uses the documented web tsconfig to render the existing JSX at runtime.
    renderer = await import(new URL('../src/renderer/src/components/OutcomeCanvas.tsx', import.meta.url).href)
  } finally {
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else Reflect.deleteProperty(globalThis, 'localStorage')
    if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
  const { useStore } = store
  const { OutcomeCanvas } = renderer
  const initial = useStore.getInitialState()
  const original = { ...initial }
  const locate = (node: ReactNode): ReactElement[] => {
    const found: ReactElement[] = []
    Children.forEach(node, child => {
      if (!isValidElement<{ card?: OutcomeCard; children?: ReactNode }>(child)) return
      if (child.props.card) found.push(child)
      else found.push(...locate(child.props.children))
    })
    return found
  }
  function CardsOnly(): ReactElement {
    // Reuse the private CardView elements produced by OutcomeCanvas. Forms,
    // navigation, progress, feedback and user-entered requirements are excluded.
    const elements = locate(OutcomeCanvas())
    if (elements.length !== 1) throw new Error('OutcomeCanvas card boundary changed; update the offline exporter')
    return createElement(Fragment, null, elements)
  }
  return card => {
    // Zustand SSR closes over its initial object. Populate and restore only
    // this isolated process's server snapshot, without invoking store actions.
    Object.assign(initial, { run, cards: [card], busy: false, backendReady: true, pendingDelivery: null })
    try { return textOf(renderToStaticMarkup(createElement(CardsOnly))) }
    finally { Object.assign(initial, original) }
  }
}

function attributedFields(card: OutcomeCard): string[] {
  if (card.kind === 'browser_page') return ['/text', ...(card.rawTitle ? ['/rawTitle'] : []), ...(card.places || []).flatMap((place, i) => place.quote ? [`/places/${i}/quote`] : [])]
  if (card.kind === 'dishes') return card.dishes.flatMap((dish, i) => dish.reason ? [`/dishes/${i}/reason`] : [])
  if (card.kind === 'groupbuy') return card.comparison ? card.comparison.entries.map((_, i) => `/comparison/entries/${i}/quote`) : card.packages.flatMap((item, i) => item.quote ? [`/packages/${i}/quote`] : [])
  if (card.kind === 'price_comparison') return card.data.entries.map((_, i) => `/data/entries/${i}/quote`)
  if (card.kind === 'evidence') return card.items.map((_, i) => `/items/${i}/claim`)
  return []
}

export async function exportQualityOutput(input: unknown, inputSha256 = hash(JSON.stringify(input))): Promise<Record<string, unknown>> {
  const envelope = row(input), raw = row(envelope.snapshot || envelope.run || input)
  if (!str(raw.run_id) || !str(raw.phase) || !raw.state || typeof raw.state !== 'object' || Array.isArray(raw.state) || !Number.isSafeInteger(raw.event_seq) || Number(raw.event_seq) < 0) throw new Error('Expected a HarnessSnapshot with run_id, phase, event_seq and state')
  const run = structuredClone(raw) as unknown as HarnessSnapshot
  if (envelope.events !== undefined) run.events = envelope.events as HarnessEvent[]
  if (run.events !== undefined && (!Array.isArray(run.events) || run.events.some(e => !e || !Number.isSafeInteger(e.seq) || !e.payload || typeof e.payload !== 'object'))) throw new Error('Malformed durable events')
  const checkpoint = row(envelope.checkpoint)
  const asOf = str(checkpoint.as_of) || str(checkpoint.captured_at)
  if (asOf && !Number.isFinite(Date.parse(asOf))) throw new Error('checkpoint.as_of/captured_at must be an ISO date')
  const clean = sanitizer(input)
  const events = [...new Map((run.events || []).filter(event => event.run_id === run.run_id && event.seq <= run.event_seq).map(event => [event.seq, event])).values()].sort((a, b) => a.seq - b.seq)
  const eventComplete = events[0]?.event_type === 'RUN_CREATED' && events.every((event, i) => event.seq === i + 1) && events.at(-1)?.seq === run.event_seq
  const oldBoundaries = events.filter((event, i) => (event.event_type === 'RUN_FINALIZED' || event.event_type === 'GRAPH_INTERRUPTED'
    && rows(event.payload.interrupts).some(question => ['clarification', 'draft_review', 'approval'].includes(str(question.type)) && !!(str(question.question) || str(question.message))))
    && events[i + 1]?.event_type !== 'ASSISTANT_MESSAGE'
    && !(event.created_at && events.some(next => next.event_type === 'ASSISTANT_MESSAGE' && next.created_at === event.created_at)))
  const NativeDate = Date
  const renderTime = asOf || new NativeDate().toISOString()
  // This is an offline rendering clock only. No workflow, lease or timer runs.
  if (asOf) globalThis.Date = class extends NativeDate {
    constructor(value?: string | number | Date) { super(value === undefined ? asOf : value instanceof NativeDate ? value.getTime() : value) }
    static now(): number { return NativeDate.parse(asOf) }
  } as DateConstructor
  try {
    const projection = projectHarness(run)
    const render = await cardRenderer(run)
    const cards = projection.cards.map((card, index) => {
      const views = [{ name: 'initial', rendered_text: render(card) }]
      // A tabbed candidate is delivered even before its tab is selected.
      if (card.kind === 'plans') for (const [i, variant] of card.variants.entries()) views.push({ name: `candidate:${i}`, rendered_text: render({ kind: 'plan', plan: variant.plan }) })
      return { id: `card:${index}`, kind: card.kind, projection_pointer: `/cards/${index}`, structure: clean(card),
        views: clean(views), source_attributed_fields: attributedFields(card), collapsed_details_included: true }
    })
    const sourceRefs = [
      ...projection.evidence.map((evidence, index) => ({ kind: 'evidence', pointer: `/state/evidence/${index}`, evidence_id: evidence.evidence_id, source: evidence.source, source_ref: evidence.source_ref, observed_at: evidence.observed_at, expires_at: evidence.expires_at })),
      ...rows(run.state.browser_artifacts).map((artifact, index) => ({ kind: 'artifact', pointer: `/state/browser_artifacts/${index}`, artifact_id: str(artifact.artifact_id), command_id: str(artifact.command_id), source: str(artifact.source), source_ref: str(artifact.url), observed_at: str(artifact.observed_at), expires_at: str(artifact.expires_at) })),
      ...(run.offer_comparison ? [{ kind: 'offer_comparison', pointer: '/offer_comparison/source', artifact_id: str(run.offer_comparison.source.artifact_id), command_id: str(run.offer_comparison.source.command_id), source_ref: str(run.offer_comparison.source.url), observed_at: str(run.offer_comparison.source.observed_at), expires_at: str(run.offer_comparison.source.expires_at) }] : []),
      ...(row(row(run.state.execution_outcome).data).source_url ? [{ kind: 'outcome_source', pointer: '/state/execution_outcome/data/source_url', source_ref: str(row(row(run.state.execution_outcome).data).source_url) }] : [])
    ]
    return {
      schema: 'plango.quality-output.v1', input_sha256: inputSha256,
      checkpoint: { id: clean(str(checkpoint.id) || `${run.run_id}:${run.event_seq}`), run_id: clean(run.run_id), version: typeof run.version === 'number' ? run.version : null,
        turn_id: typeof run.state.turn_id === 'number' ? run.state.turn_id : null, event_seq: run.event_seq, phase: clean(run.phase), outcome: clean(run.outcome ?? null),
        scope: clean(str(row(row(run.state.execution_outcome).data).scope) || run.draft_review?.scope || null),
        render_reference_time: renderTime, render_time_supplied: !!asOf },
      coverage: { history_coverage_partial: !eventComplete || oldBoundaries.length > 0, event_sequence_complete: !!eventComplete,
        supplied_event_count: events.length, legacy_boundaries_without_exact_reply: oldBoundaries.map(event => event.seq),
        cards_scope: 'current_checkpoint_only', historical_card_states_included: false,
        effects_and_native_browser_included: false, source_excerpt_details_included: true,
        limitations: ['Capture every preregistered delivery checkpoint; events cannot reconstruct previous cards.',
          'Server rendering includes collapsed excerpts and candidate plans; native map rendering, image pixels and effects need separate screenshots.',
          ...(!eventComplete ? ['Missing or incomplete events: exported messages are only the production saved-history fallback, not all historical replies.'] : []),
          ...(!asOf ? ['No checkpoint rendering time supplied; time-sensitive UI was reconstructed at export time.'] : [])] },
      messages: projection.messages.filter(message => message.role === 'assistant').map((message, index) => ({ id: clean(message.id || `assistant:${index}`), role: 'assistant', content: clean(message.content) })),
      cards, context_output: clean({ reading_scope: readProgress(run), offer_comparison_error: run.offer_comparison_error || null }),
      source_pointer_base: envelope.snapshot ? '/snapshot' : envelope.run ? '/run' : '', source_refs: clean(sourceRefs),
      scoring_boundary: { primary: 'messages.content + cards.views.rendered_text + context_output public scope/status; structure retains delivered values and provenance, not an extra set of claims',
        source_attributed: 'Quoted/source-excerpt text delivered in PlanGo cards is included, not excluded merely because it attributes a source.',
        excluded: ['user inputs and requirement form values', 'hidden model prompts/reasoning', 'raw native merchant page text not delivered in cards', 'progress animation and action controls'],
        deduplication: 'Do not count repetitions across messages/cards/views/checkpoints as new facts unless the claim or business conditions change; retain contradictions and earlier errors.',
        scores_computed: false }
    }
  } finally { globalThis.Date = NativeDate }
}

async function selfTest(): Promise<void> {
  const safePreview = 'https://www.szuo.com/en/niccolo-chongqing-tealounge/reserve/landing?pax=2&start_date=2026-09-11&start_time=15%3A00'
  const clean = sanitizer({ binding_token: 'PREVIEW_TOKEN_SENTINEL' })
  assert.equal(clean(safePreview), safePreview, 'Validated party/date/time source parameters must survive export')
  for (const unsafe of [safePreview + '&token=PREVIEW_TOKEN_SENTINEL', safePreview + '&arbitrary=EXTRA_QUERY_SENTINEL', safePreview.replace('www.szuo.com', 'example.org'), safePreview.replace('https://', 'https://user:password@')]) {
    const output = String(clean(unsafe))
    assert(!output.includes('?') && !output.includes('PREVIEW_TOKEN_SENTINEL') && !output.includes('EXTRA_QUERY_SENTINEL') && !output.includes('user:password'))
  }
  const run: HarnessSnapshot = { run_id: 'offline-check', input_text: 'USER_ONLY', phase: 'SUCCEEDED', outcome: 'SUCCEEDED', version: 1, event_seq: 3,
    events: [
      { run_id: 'offline-check', seq: 1, event_type: 'RUN_CREATED', payload: { input_text: 'USER_ONLY' } },
      { run_id: 'offline-check', seq: 2, event_type: 'ASSISTANT_MESSAGE', payload: { content: '已找到菜单，请核对规则。' } },
      { run_id: 'offline-check', seq: 3, event_type: 'ASSISTANT_MESSAGE', payload: { content: '当前只完成预览。' } }
    ], state: { reason: '当前只完成预览。', turn_id: 1, model_calls: [{ prompt: 'HIDDEN_REASONING', api_key: 'PRIVATE_KEY_SENTINEL' }],
      browser_binding: { binding_token: 'BINDING_TOKEN_SENTINEL' }, browser_observation: { text: 'NATIVE_PAGE_ONLY' },
      browser_artifacts: [{ artifact_id: 'page:one', type: 'browser_page', source: 'browser', url: 'https://name:password@example.org/menu?token=BINDING_TOKEN_SENTINEL', title: '雾岚餐厅',
        data: { menu: Array.from({ length: 10 }, (_, i) => ({ name: `菜单${i + 1}`, price: i + 10, quote: `原文${i + 1}` })), offers: [{ name: '双人套餐', price: 98, people: 2, conditions: ['节假日规则待确认'], quote: '双人98元' }], text: 'DELIVERED_EXCERPT <真实原文>' } }] } }
  const before = JSON.stringify(run)
  const output = await exportQualityOutput({ snapshot: run, checkpoint: { id: 'read', as_of: '2026-09-09T12:00:00+08:00' } })
  const json = JSON.stringify(output)
  for (const hidden of ['USER_ONLY', 'HIDDEN_REASONING', 'PRIVATE_KEY_SENTINEL', 'BINDING_TOKEN_SENTINEL', 'NATIVE_PAGE_ONLY', 'name:password', '?token=']) assert(!json.includes(hidden), hidden)
  for (const delivered of ['菜单10', '双人套餐', '98', '节假日规则待确认', 'DELIVERED_EXCERPT <真实原文>', '已找到菜单', '当前只完成预览']) assert(json.includes(delivered), delivered)
  assert.equal(row(output.coverage).history_coverage_partial, false)
  const transportEvents: HarnessEvent[] = [run.events![0],
    { run_id: run.run_id, seq: 2, event_type: 'GRAPH_INTERRUPTED', payload: { interrupts: [{ type: 'browser', message: '等待桌面浏览器结果。' }] } },
    { run_id: run.run_id, seq: 3, event_type: 'BROWSER_OBSERVED', payload: {} },
    { run_id: run.run_id, seq: 4, event_type: 'ASSISTANT_MESSAGE', payload: { content: '页面摘录已交付。' } }]
  assert.equal(row((await exportQualityOutput({ ...run, events: transportEvents, event_seq: 4 })).coverage).history_coverage_partial, false, 'Browser transport waits are not missing public reply boundaries')
  const legacyQuestion = structuredClone(transportEvents)
  legacyQuestion[1].payload = { interrupts: [{ type: 'clarification', question: '请确认同行人数。' }] }
  assert.equal(row((await exportQualityOutput({ ...run, events: legacyQuestion, event_seq: 4 })).coverage).history_coverage_partial, true, 'Genuine public reply boundaries still report missing exact replies')
  assert.equal(JSON.stringify(run), before, 'Exporter must not mutate input state')
  assert.equal(row((await exportQualityOutput({ ...run, events: undefined })).coverage).history_coverage_partial, true)
  const plan = { plan_id: 'plan-1', version: 1, label: '候选A', total_cost: 98, party_size: 2,
    stops: [{ place_id: 'shop', name: '雾岚餐厅', category: '餐厅', start_minute: 1080, end_minute: 1140, unit_price: 49, estimated_cost: 98, transport_cost: 0, supply_source: 'browser', estimated_wait_min: 0 }] }
  const planned = await exportQualityOutput({ ...run, state: { candidate_plans: [plan, { ...plan, plan_id: 'plan-2', label: '候选B' }], selected_plan: plan,
    trip_spec: { party_size: 2, budget: 150 }, evidence: [{ evidence_id: 'price', source: 'browser', source_ref: 'https://example.org/menu', claim: '人均49元' }],
    execution_goal: { kind: 'itinerary_preparation', approval_id: 'approval-1', plan_id: 'plan-1', plan_version: 1, stops: [{ place_id: 'shop', name: '雾岚餐厅', address: '青竹路1号' }] },
    execution_outcome: { kind: 'itinerary_preparation', status: 'satisfied', summary: '参数已准备，待核对', data: { scope: 'ready_to_review', business_completed: false,
      entries: [{ place_id: 'shop', party_size: 2, visit_date: '2026-09-11', visit_time: '18:00', timezone: 'Asia/Shanghai' }] } },
    browser_artifacts: [{ artifact_id: 'prices', type: 'price_comparison', source: 'browser', title: '单点比较', data: { basis: 'per_person', party_size: 2, total_budget: 150,
      entries: [{ name: '单点', unit_price: 49, total: 98, within_budget: true, source_url: 'https://example.org/menu', quote: '人均49元', evidence_id: 'price' }] } }] } })
  const plannedCards = rows(planned.cards)
  for (const kind of ['plans', 'price_comparison', 'preparation', 'evidence']) assert(plannedCards.some(card => card.kind === kind), kind)
  assert.equal((plannedCards.find(card => card.kind === 'plans')!.views as unknown[]).length, 3, 'Every candidate has an actual rendered view')
  for (const delivered of ['候选B', '去程交通', '参数已准备，待核对', '尚未提交预约', '人均价格', '¥49 / 人']) assert(JSON.stringify(planned).includes(delivered), delivered)
  const offer = { offer_index: 0, offer_hash: 'a'.repeat(64), name: '双人98元', grounded: true, price_basis: 'per_package', kind: 'package', status: 'unknown',
    price: 98, face_value: null, original_price: null, people: 2, known_cost: 98, total_cost: null, within_budget: null, reasons: ['2人套餐'], missing_rules: ['预约规则'], quote: '双人98元', source_ref: { command_id: 'cmd', offer_index: 0, offer_hash: 'a'.repeat(64) } }
  const compared = await exportQualityOutput({ ...run, offer_comparison: { source_ref: { command_id: 'cmd', artifact_id: 'page:cmd' }, merchant: { name: '雾岚餐厅', address: '青竹路1号' },
    source: { command_id: 'cmd', artifact_id: 'page:cmd', url: 'https://example.org/menu', observed_at: '2026-09-09T12:00:00+08:00', expires_at: '2026-09-09T12:10:00+08:00', valid: true },
    constraints: { party_size: 3, visit_date: '2026-09-11', budget: 150, per_person_budget: null, timezone: 'Asia/Shanghai' }, entries: [offer], limitations: [], summary: '' } })
  assert(JSON.stringify(compared).includes('规则缺失 · 待核对'))
  const preview = await exportQualityOutput({ ...run, state: { execution_outcome: { kind: 'page_read', status: 'satisfied', data: { scope: 'booking_parameters', business_completed: false, availability_checked: false, requested: { party_size: 2, date: '2026-09-11', time: '15:00' }, visible_labels: { party_label: '2 Guests', date_label: 'Fri Sep 11', time_label: '3:00 pm' }, source_url: 'https://example.org/preview' } } } })
  assert(JSON.stringify(preview).includes('未查询空位，未提交预约'))
  console.log('Offline quality output: actual cards, collapsed menu/excerpts, replies, scope and secret exclusions passed')
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    if (process.argv[2] === '--self-test') await selfTest()
    else {
      if (process.argv.length !== 4) throw new Error('Usage: tsx --tsconfig tsconfig.web.json scripts/export_quality_output.ts INPUT.json OUTPUT.json (or --self-test)')
      const bytes = await readFile(process.argv[2], 'utf8')
      const output = await exportQualityOutput(JSON.parse(bytes), hash(bytes))
      await writeFile(process.argv[3], JSON.stringify(output, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
      console.log(JSON.stringify({ schema: output.schema, messages: (output.messages as unknown[]).length, cards: (output.cards as unknown[]).length, history_coverage_partial: row(output.coverage).history_coverage_partial }))
    }
  } catch (error) {
    // Do not print input content, absolute private paths or rendering stack props.
    console.error(error instanceof Error && error.message.startsWith('Usage:') ? error.message : `Quality export failed (${row(error).code || (error instanceof Error ? error.name : 'invalid input')}); no existing output was overwritten.`)
    if (process.argv[2] === '--self-test') console.error(error)
    process.exitCode = 1
  }
}
