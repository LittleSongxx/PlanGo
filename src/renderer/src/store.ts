import { create } from 'zustand'
import type { AgentStep, ChatMessage, OutcomeCard, Plan, HarnessSnapshot, HarnessEvent, RequirementEdit } from '@shared/types'
import { projectHarness, projectEvents, runBusy, canResolveAction, row } from './lib/harnessProjection'
import { originFallback as computeOrigin } from './lib/cityCenter'
import { migrateLocalStorage } from './lib/storageMigration'
import type { BrowserIntent, BrowserViewState, BrowserTabState } from '@shared/browserView'
import type { LocationGranularity, LocationInfo, SelectedPoi } from '@shared/location'

migrateLocalStorage(localStorage)

export type Tab = BrowserTabState

interface ProactiveMsg {
  id: string
  ts: number
  text: string
  kind: string
}

export type WorkView = 'browser' | 'outcome'

export interface SavedSession {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  city: string
  messages: ChatMessage[]
  cards: OutcomeCard[]
  runId?: string
}

const SESS_KEY = 'plango_sessions'
const HIDDEN_KEY = 'plango_hidden_sessions'
function hiddenSessionIds(): Set<string> {
  try {
    const ids = JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]')
    return new Set(Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : [])
  } catch { return new Set() }
}
function visibleSessions(list: SavedSession[]): SavedSession[] {
  const hidden = hiddenSessionIds()
  return list.filter(session => !hidden.has(session.id) && (!session.runId || !hidden.has(session.runId)))
}
export function loadSessions(): SavedSession[] {
  try {
    const sessions = JSON.parse(localStorage.getItem(SESS_KEY) || '[]')
    return Array.isArray(sessions) ? visibleSessions(sessions) : []
  } catch {
    return []
  }
}
function saveSessions(list: SavedSession[]): void {
  try {
    localStorage.setItem(SESS_KEY, JSON.stringify(visibleSessions(list).slice(0, 30)))
  } catch {
    /* quota */
  }
}

export interface RouteTarget {
  origin?: string // "lng,lat" user origin
  originName?: string
  initialMode?: 'driving' | 'walking' | 'transit'
  originGranularity?: LocationGranularity
  dest: string // "lng,lat"
  destName: string
  city: string
}

interface State {
  tabs: Tab[]
  activeTabId: string | null
  messages: ChatMessage[]
  steps: AgentStep[]
  cards: OutcomeCard[]
  busy: boolean
  requestBusy: boolean
  run: HarnessSnapshot | null
  events: HarnessEvent[]
  backendError: string
  backendReady: boolean
  refreshRun: () => Promise<void>
  hydrateHarness: () => Promise<void>
  applyHarness: (run: HarnessSnapshot) => void
  receiveHarnessEvent: (event: HarnessEvent) => void
  cancelRun: () => Promise<void>
  resolveAction: (runId: string, actionId: string, status: 'SUCCEEDED' | 'FAILED', note: string, reference?: string) => Promise<void>
  selectPlan: (plan: Plan) => Promise<void>
  editRequirements: (edit: RequirementEdit) => Promise<void>
  decideDraft: (runId: string, interruptId: string, planId: string, planVersion: number, decision: 'save' | 'prepare') => Promise<void>
  resumeBrowser: () => Promise<void>
  resumePreparation: (runId: string, planId: string, planVersion: number, approvalId: string) => Promise<void>
  proactive: ProactiveMsg[]
  settingsOpen: boolean
  view: WorkView
  chatWidth: number
  city: string
  citySource: string
  district: string
  locationGranularity: LocationGranularity
  locationObservedAt: string
  locAccuracy: number // 定位精度（米），0=未知
  coords: string // "lng,lat" GCJ02，用户当前位置（地图起点）
  routeTarget: RouteTarget | null // 页面内路线面板目标
  sharePlan: Plan | null // 分享弹窗目标方案
  sessions: SavedSession[]
  activeSessionId: string
  historyOpen: boolean
  sidePanelOpen: boolean
  discoverOpen: false | 'discover' | 'deals'
  aiBrowsing: { active: boolean; site: string; action: string } // 顶部浮条：PlanGo正在浏览

  browserSeq: number
  browserError: string
  applyBrowserState: (state: BrowserViewState) => void
  browserIntent: (intent: BrowserIntent) => Promise<void>
  addTab: (url: string) => void
  closeTab: (id: string) => void
  setActiveTab: (id: string) => void

  pushMessage: (m: ChatMessage) => void
  send: (text: string, image?: string, selectedPoi?: SelectedPoi) => Promise<void>
  confirm: (token: string, ok: boolean) => Promise<void>

  applyStep: (s: AgentStep & { patch?: boolean }) => void
  addCard: (c: OutcomeCard) => void
  clearCards: () => void
  addProactive: (p: ProactiveMsg) => void
  setSettings: (open: boolean) => void
  setView: (v: WorkView) => void
  setChatWidth: (w: number) => void
  setCity: (city: string, source?: string) => void
  setCoords: (coords: string) => void
  originFallback: () => string // 出发点：真实坐标优先，否则当前城市中心（永远有值）
  setLocationInfo: (info: Partial<LocationInfo>) => void
  navigateInApp: (url: string) => void
  openRoute: (t: RouteTarget) => void
  closeRoute: () => void
  openShare: (p: Plan) => void
  closeShare: () => void
  setHistoryOpen: (open: boolean) => void
  setSidePanelOpen: (open: boolean) => void
  setDiscoverOpen: (v: false | 'discover' | 'deals') => void
  setAiBrowsing: (v: { active: boolean; site?: string; action?: string }) => void
  newSession: () => void
  restoreSession: (id: string) => void
  deleteSession: (id: string) => void
  persistSession: () => void
}

let refreshingRun: string | null = null

export const useStore = create<State>((set, get) => ({
  tabs: [],
  activeTabId: null,
  messages: [
    {
      role: 'assistant',
      content:
        '你好，我是 PlanGo。\n\n告诉我想去哪儿、和谁一起、什么时候出发。我会结合真实资料整理安排，保留来源和待核验事项。\n\n需要登录或执行关键操作时，会先停下来请你确认。'
    }
  ],
  steps: [],
  cards: [],
  busy: false,
  requestBusy: false,
  run: null,
  events: [],
  backendError: '',
  backendReady: false,
  proactive: [],
  settingsOpen: false,
  view: 'browser',
  chatWidth: 440,
  city: '定位中…',
  citySource: 'config',
  district: '',
  locAccuracy: 0,
  locationGranularity: 'city',
  locationObservedAt: '',
  coords: '',
  routeTarget: null,
  sharePlan: null,
  sessions: loadSessions(),
  activeSessionId: 'sess_' + crypto.randomUUID(),
  historyOpen: false,
  sidePanelOpen: false,
  discoverOpen: false,
  aiBrowsing: { active: false, site: '', action: '' },

  browserSeq: -1,
  browserError: '',
  applyBrowserState: (value) => set(state => value.seq < state.browserSeq ? {} : { tabs: value.tabs, activeTabId: value.activeTabId, browserSeq: value.seq }),
  browserIntent: async (intent) => {
    set({ browserError: '' })
    try { get().applyBrowserState(await window.plango.browser.request(intent)); set({ browserError: '' }) }
    catch (error) { set({ browserError: (error as Error).message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '') }) }
  },
  addTab: (url) => { void get().browserIntent({ kind: 'create', url }) },
  closeTab: (id) => { void get().browserIntent({ kind: 'close', id }) },
  setActiveTab: (id) => { void get().browserIntent({ kind: 'activate', id }) },

  pushMessage: (m) => set((s) => ({ messages: [...s.messages, m] })),

  applyHarness: (run) => {
    const hidden = hiddenSessionIds()
    if (hidden.has(run.run_id) || hidden.has(get().activeSessionId)) return
    const current = get().run
    if (current && current.run_id !== run.run_id) return
    if (current && ((run.version ?? 0) < (current.version ?? 0) || (run.version === current.version && run.event_seq < current.event_seq))) return
    const projected = projectHarness(run)
    const newDecision = !!run.interrupt_id && run.interrupt_id !== current?.interrupt_id && (!!run.draft_review || run.phase === 'WAITING_APPROVAL')
    const newlyFinished = !!run.outcome && (!current?.outcome || run.state.turn_id !== current.state.turn_id)
    set((s) => ({ run, messages: projected.messages.length ? projected.messages : s.messages, cards: projected.cards,
      ...(run.events ? { events: run.events, steps: projectEvents(run.events) } : {}),
      backendReady: true, backendError: '', busy: s.requestBusy || runBusy(run),
      aiBrowsing: run.outcome || run.state.browser_wait ? { active: false, site: '', action: '' } : s.aiBrowsing,
      view: projected.cards.length && (!s.cards.length || newlyFinished || newDecision) ? 'outcome' : s.view }))
    get().persistSession()
  },

  refreshRun: async () => {
    const { run, activeSessionId } = get()
    if (!run || refreshingRun === run.run_id) return
    refreshingRun = run.run_id
    try {
      const snapshot = await window.plango.harness.getRun(run.run_id)
      if (get().activeSessionId !== activeSessionId || get().run?.run_id !== run.run_id) return
      get().applyHarness(snapshot)
    } catch (e) {
      if (get().activeSessionId === activeSessionId) set({ backendError: String(e), backendReady: false })
    } finally { if (refreshingRun === run.run_id) refreshingRun = null }
  },

  hydrateHarness: async () => {
    const activeSessionId = get().activeSessionId
    try {
      const status = await window.plango.harness.status()
      set({ backendReady: status.ready, backendError: status.error || '' })
      if (!status.ready) return
      if (get().run) { await get().refreshRun(); return }
      const runs = await window.plango.harness.listRuns()
      if (get().activeSessionId !== activeSessionId || get().run) return
      const sessions = visibleSessions(get().sessions)
      const hidden = hiddenSessionIds()
      for (const run of runs) {
        if (hidden.has(run.run_id) || sessions.some(s => s.runId === run.run_id)) continue
        sessions.push({ id: run.run_id, runId: run.run_id, title: run.input_text.slice(0, 26), createdAt: Date.now(), updatedAt: Date.now(), city: '', messages: [], cards: [] })
      }
      set({ sessions })
      saveSessions(sessions)
      // The history stores references only; opening a run always fetches its
      // current plan and approval rather than trusting cached outcome cards.
      const savedId = localStorage.getItem('plango_active_run')
      const saved = sessions.find(s => s.runId === savedId)
      if (saved) get().restoreSession(saved.id)
    } catch (e) { set({ backendReady: false, backendError: String(e) }) }
  },

  receiveHarnessEvent: (event) => {
    if (get().run?.run_id !== event.run_id) return
    if (event.event_type === 'snapshot') { get().applyHarness(event.payload.snapshot as HarnessSnapshot); return }
    if (event.event_type === 'connection_error') set({ backendReady: false, backendError: String(event.payload.message || '任务连接中断') })
    const events = [...new Map([...get().events, event].map(e => [e.seq, e])).values()].sort((a, b) => a.seq - b.seq)
    set({ events, steps: projectEvents(events) })
  },

  send: async (text, image, selectedPoi) => {
    if (!text.trim() || get().busy) return
    if (!get().backendReady) { set({ backendError: '运行服务尚未连接，请先重新连接。' }); return }
    if (selectedPoi) get().newSession()
    const { run, activeSessionId } = get()
    set((s) => ({ busy: true, requestBusy: true, backendError: '', messages: [...s.messages, { role: 'user', content: text }] }))
    try {
      const snapshot = run ? await window.plango.harness.sendMessage(run.run_id, text, image) : await window.plango.harness.createRun(text, image, selectedPoi)
      if (get().activeSessionId !== activeSessionId) return
      get().applyHarness(snapshot)
    } catch (e) {
      if (get().activeSessionId === activeSessionId) set({ backendError: String(e) })
    } finally {
      if (get().activeSessionId === activeSessionId) { set({ requestBusy: false, busy: runBusy(get().run) }); get().persistSession() }
    }
  },

  confirm: async (token, ok) => {
    const { run, activeSessionId } = get()
    if (!run || get().busy || token !== (run.interrupt_id || run.state.interrupt_id)) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.resume(run.run_id, token, ok ? 'approve' : 'reject')
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) {
      if (get().activeSessionId === activeSessionId) set({ backendError: String(e) })
    } finally {
      if (get().activeSessionId === activeSessionId) { set({ requestBusy: false, busy: runBusy(get().run) }) }
    }
  },

  resolveAction: async (runId, actionId, status, note, reference) => {
    const { run, activeSessionId } = get()
    if (!canResolveAction(run, runId, actionId) || get().busy || !get().backendReady || !note.trim()) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.resolveAction(runId, actionId, status, note.trim(), reference?.trim() || undefined)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: String(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  selectPlan: async (plan) => {
    const { run, activeSessionId } = get()
    if (!run || plan.run_id !== run.run_id || !plan.version || get().busy || !get().backendReady) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.selectPlan(run.run_id, plan.plan_id, plan.version)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: String(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  editRequirements: async (edit) => {
    const { run, activeSessionId } = get()
    if (!run || get().busy || !get().backendReady) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.editRequirements(run.run_id, edit)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (error) { if (get().activeSessionId === activeSessionId) set({ backendError: String(error) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  decideDraft: async (runId, interruptId, planId, planVersion, decision) => {
    const { run, activeSessionId } = get(), draft = run?.draft_review
    if (!run || run.outcome || ['FAILED', 'CANCELLED', 'INFEASIBLE', 'PARTIAL_FAILED', 'SUCCEEDED'].includes(run.phase) || run.run_id !== runId || get().busy || !get().backendReady || !draft || run.interrupt_id !== interruptId || draft.interrupt_id !== interruptId ||
      draft.plan_id !== planId || draft.plan_version !== planVersion || row(run.state.selected_plan).plan_id !== planId || row(run.state.selected_plan).version !== planVersion ||
      (decision === 'prepare' && (!draft.can_prepare || draft.preparation_blockers.length > 0 || draft.conflicts.some(check => check.passed !== true)))) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.decideDraft(runId, interruptId, planId, planVersion, decision)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (error) { if (get().activeSessionId === activeSessionId) set({ backendError: String(error) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  cancelRun: async () => {
    const { run, activeSessionId } = get()
    if (!run) return
    try {
      const snapshot = await window.plango.harness.cancel(run.run_id)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: String(e) }) }
  },

  resumePreparation: async (runId, planId, planVersion, approvalId) => {
    const { run, activeSessionId } = get(), resume = run?.preparation_resume, goal = row(run?.state.execution_goal)
    if (!run || run.run_id !== runId || get().busy || !get().backendReady || run.command_pending || !resume?.can_resume || resume.blockers.length ||
      resume.plan_id !== planId || resume.plan_version !== planVersion || resume.approval_id !== approvalId || goal.kind !== 'itinerary_preparation' ||
      goal.plan_id !== planId || goal.plan_version !== planVersion || goal.approval_id !== approvalId || row(run.state.selected_plan).plan_id !== planId || row(run.state.selected_plan).version !== planVersion) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.resumePreparation(runId, planId, planVersion, approvalId)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (error) { if (get().activeSessionId === activeSessionId) set({ backendError: String(error) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  resumeBrowser: async () => {
    const { run, activeSessionId } = get()
    const interruptId = run?.interrupt_id || run?.state.interrupt_id
    if (!run || typeof interruptId !== 'string' || get().requestBusy) return
    set({ requestBusy: true, busy: true })
    try {
      const snapshot = await window.plango.harness.resume(run.run_id, interruptId, 'resume')
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: String(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  applyStep: (s) =>
    set((state) => {
      if (s.patch) {
        return { steps: state.steps.map((x) => (x.id === s.id ? { ...x, status: s.status, detail: s.detail ?? x.detail, source: s.source ?? x.source } : x)) }
      }
      if (state.steps.find((x) => x.id === s.id)) return {}
      return { steps: [...state.steps, s] }
    }),
  addCard: (c) =>
    set((s) => {
      // 单例卡：代表"当前结果"的卡片重复产出时替换旧的，避免"点一次加一张"堆叠；
      // 交易/历史类卡（回执/排号/确认/群体确认）保留追加。
      const SINGLETON = new Set(['plans', 'plan', 'deal', 'dishes', 'takeout', 'groupbuy', 'discover'])
      const cards = SINGLETON.has(c.kind) ? [...s.cards.filter((x) => x.kind !== c.kind), c] : [...s.cards, c]
      return {
        cards,
        // PlanGo一产出成果卡片，主工作区自动切到"成果区"大视图（confirm 卡除外，避免打断浏览）
        view: c.kind === 'confirm' ? s.view : 'outcome'
      }
    }),
  clearCards: () => set({ cards: [] }),
  addProactive: (p) => set((s) => ({ proactive: [p, ...s.proactive].slice(0, 20) })),
  setSettings: (open) => set({ settingsOpen: open }),
  setView: (v) => set({ view: v }),
  setChatWidth: (w) => set({ chatWidth: Math.max(320, Math.min(760, w)) }),
  setCity: (city, source) => set((s) => ({ city, citySource: source ?? s.citySource })),
  setCoords: (coords) => set({ coords }),
  originFallback: () => computeOrigin(get().coords, get().city),
  setLocationInfo: (info) =>
    set((s) => ({
      city: info.city || s.city,
      district: info.district ?? '',
      coords: info.coords ?? '',
      citySource: info.source ?? s.citySource,
      locAccuracy: info.accuracy ?? 0,
      locationGranularity: info.granularity ?? (info.coords ? 'unknown' : 'city'),
      locationObservedAt: info.observed_at ?? ''
    })),
  // 页面内导航：在内置浏览器新开一个标签打开高德网页版路线（不跳系统外部浏览器）
  navigateInApp: (url) => {
    get().addTab(url)
    set({ view: 'browser' })
  },
  openRoute: (t) => set({ routeTarget: { ...t, originGranularity: t.originGranularity ?? get().locationGranularity } }),
  closeRoute: () => set({ routeTarget: null }),
  openShare: (p) => set({ sharePlan: p }),
  closeShare: () => set({ sharePlan: null }),

  setHistoryOpen: (open) => set({ historyOpen: open }),
  setSidePanelOpen: (open) => set({ sidePanelOpen: open }),
  setDiscoverOpen: (v) => set({ discoverOpen: v }),
  setAiBrowsing: (v) => set((s) => ({ aiBrowsing: { active: v.active, site: v.site ?? s.aiBrowsing.site, action: v.action ?? s.aiBrowsing.action } })),
  newSession: () => {
    set((s) => ({ sessions: upsertSession(s), activeSessionId: 'sess_' + crypto.randomUUID(),
      messages: [{ role: 'assistant', content: '开始新的安排吧。告诉我地点、人数和预算。' }], cards: [], steps: [], events: [], run: null,
      busy: false, requestBusy: false, backendError: '', historyOpen: false }))
    localStorage.removeItem('plango_active_run')
  },
  restoreSession: (id) => {
    const list = upsertSession(get())
    const target = list.find(x => x.id === id)
    if (!target) return
    set({ sessions: list, activeSessionId: id, messages: target.messages, cards: [], steps: [], events: [], run: null,
      busy: !!target.runId, requestBusy: !!target.runId, backendError: target.runId ? '' : '这是旧版会话的只读记录。发送消息会建立新的后端任务。', historyOpen: false })
    if (!target.runId) return
    localStorage.setItem('plango_active_run', target.runId)
    void window.plango.harness.getRun(target.runId).then(async snapshot => {
      if (get().activeSessionId !== id) return
      set({ requestBusy: false })
      get().applyHarness(snapshot)
    }).catch(e => {
      if (get().activeSessionId === id) set({ busy: false, requestBusy: false, backendError: String(e), backendReady: false })
    })
  },
  deleteSession: (id) => {
    const state = get()
    const target = state.sessions.find(session => session.id === id)
    const hidden = hiddenSessionIds()
    hidden.add(id)
    if (target?.runId) hidden.add(target.runId)
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden])) }
    catch { set({ backendError: '无法保存历史隐藏设置，请检查本地存储后重试。' }); return }
    const sessions = visibleSessions(state.sessions)
    saveSessions(sessions)
    set({ sessions })
    // Hiding changes this desktop's list only. It never cancels or deletes a run.
    if (id === state.activeSessionId || (target?.runId && target.runId === state.run?.run_id)) {
      get().newSession()
      set({ view: 'browser', sharePlan: null, routeTarget: null })
    }
  },
  persistSession: () => set((s) => ({ sessions: upsertSession(s) }))
}))

// 把当前会话（消息+成果卡）快照进历史列表（按 activeSessionId upsert），并落 localStorage。
function upsertSession(s: State): SavedSession[] {
  const sessions = visibleSessions(s.sessions)
  const hidden = hiddenSessionIds()
  if (hidden.has(s.activeSessionId) || (s.run && hidden.has(s.run.run_id))) return sessions
  const firstUser = s.messages.find((m) => m.role === 'user')
  if (!firstUser && s.cards.length === 0) return sessions // 空会话不存
  const title = (typeof firstUser?.content === 'string' ? firstUser.content : '') || '新会话'
  const now = Date.now()
  const existing = sessions.find((x) => x.id === s.activeSessionId)
  const snap: SavedSession = {
    id: s.activeSessionId,
    title: title.length > 26 ? title.slice(0, 26) + '…' : title,
    createdAt: existing?.createdAt || now,
    updatedAt: now,
    city: s.city,
    messages: s.messages,
    cards: s.run ? [] : s.cards,
    runId: s.run?.run_id || existing?.runId
  }
  const rest = sessions.filter((x) => x.id !== s.activeSessionId)
  const list = [snap, ...rest].sort((a, b) => b.updatedAt - a.updatedAt)
  saveSessions(list)
  if (s.run) localStorage.setItem('plango_active_run', s.run.run_id)
  return list
}
