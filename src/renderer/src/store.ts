import { create } from 'zustand'
import type { AgentStep, ChatMessage, OutcomeCard, Plan, HarnessSnapshot, HarnessEvent } from '@shared/types'
import { projectHarness, projectEvents, runBusy, canResolveAction } from './lib/harnessProjection'
import { originFallback as computeOrigin } from './lib/cityCenter'

export interface Tab {
  id: string
  url: string
  title: string
}

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

const SESS_KEY = 'xy_sessions'
const HIDDEN_KEY = 'xy_hidden_sessions'
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
  origin?: string // "lng,lat" 我的位置
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
  resumeBrowser: () => Promise<void>
  proactive: ProactiveMsg[]
  settingsOpen: boolean
  view: WorkView
  chatWidth: number
  city: string
  citySource: string
  district: string
  locAccuracy: number // 定位精度（米），0=未知
  coords: string // "lng,lat" GCJ02，用户当前位置（地图起点）
  routeTarget: RouteTarget | null // 页面内路线面板目标
  sharePlan: Plan | null // 分享弹窗目标方案
  sessions: SavedSession[]
  activeSessionId: string
  historyOpen: boolean
  sidePanelOpen: boolean
  discoverOpen: false | 'discover' | 'deals'
  aiBrowsing: { active: boolean; site: string; action: string } // 顶部浮条：小悠正在浏览

  addTab: (url: string) => string
  closeTab: (id: string) => void
  setActiveTab: (id: string) => void
  updateTab: (id: string, patch: Partial<Tab>) => void

  pushMessage: (m: ChatMessage) => void
  send: (text: string, image?: string) => Promise<void>
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
  setLocationInfo: (info: { city?: string; district?: string; coords?: string; source?: string; accuracy?: number }) => void
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

let tabSeq = 0
let refreshingRun: string | null = null

export const useStore = create<State>((set, get) => ({
  tabs: [],
  activeTabId: null,
  messages: [
    {
      role: 'assistant',
      content:
        '你好，我是小悠 👋 你的 AI 本地生活管家。\n告诉我人数、预算和想去的地方，我会结合真实地图与浏览器信息规划行程。菜单、团购和执行结果会保留来源；登录或网站操作需要你接管时，我会停下来等你。'
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
  coords: '',
  routeTarget: null,
  sharePlan: null,
  sessions: loadSessions(),
  activeSessionId: 'sess_' + crypto.randomUUID(),
  historyOpen: false,
  sidePanelOpen: false,
  discoverOpen: false,
  aiBrowsing: { active: false, site: '', action: '' },

  addTab: (url) => {
    const id = 'tab_' + ++tabSeq
    set((s) => ({ tabs: [...s.tabs, { id, url, title: '加载中…' }], activeTabId: id }))
    return id
  },
  closeTab: (id) =>
    set((s) => {
      const tabs = s.tabs.filter((t) => t.id !== id)
      const activeTabId = s.activeTabId === id ? tabs[tabs.length - 1]?.id ?? null : s.activeTabId
      return { tabs, activeTabId }
    }),
  setActiveTab: (id) => set({ activeTabId: id }),
  updateTab: (id, patch) => set((s) => ({ tabs: s.tabs.map((t) => (t.id === id ? { ...t, ...patch } : t)) })),

  pushMessage: (m) => set((s) => ({ messages: [...s.messages, m] })),

  applyHarness: (run) => {
    const hidden = hiddenSessionIds()
    if (hidden.has(run.run_id) || hidden.has(get().activeSessionId)) return
    const current = get().run
    if (current && current.run_id !== run.run_id) return
    if (current && ((run.version ?? 0) < (current.version ?? 0) || (run.version === current.version && run.event_seq < current.event_seq))) return
    const projected = projectHarness(run)
    const newlyFinished = !!run.outcome && (!current?.outcome || run.state.turn_id !== current.state.turn_id)
    set((s) => ({ run, messages: projected.messages.length ? projected.messages : s.messages, cards: projected.cards,
      backendReady: true, backendError: '', busy: s.requestBusy || runBusy(run),
      aiBrowsing: run.outcome || run.state.browser_wait ? { active: false, site: '', action: '' } : s.aiBrowsing,
      view: projected.cards.length && (!s.cards.length || newlyFinished) ? 'outcome' : s.view }))
    get().persistSession()
  },

  refreshRun: async () => {
    const { run, activeSessionId } = get()
    if (!run || refreshingRun === run.run_id) return
    refreshingRun = run.run_id
    try {
      const [snapshot, result] = await Promise.all([window.xiaonian.harness.getRun(run.run_id), window.xiaonian.harness.events(run.run_id, get().events.at(-1)?.seq || 0)])
      if (get().activeSessionId !== activeSessionId || get().run?.run_id !== run.run_id) return
      const events = [...new Map([...get().events, ...result.events].map(e => [e.seq, { ...e, run_id: run.run_id }])).values()].sort((a, b) => a.seq - b.seq)
      set({ events, steps: projectEvents(events) })
      get().applyHarness(snapshot)
    } catch (e) {
      if (get().activeSessionId === activeSessionId) set({ backendError: String(e), backendReady: false })
    } finally { if (refreshingRun === run.run_id) refreshingRun = null }
  },

  hydrateHarness: async () => {
    const activeSessionId = get().activeSessionId
    try {
      const status = await window.xiaonian.harness.status()
      set({ backendReady: status.ready, backendError: status.error || '' })
      if (!status.ready) return
      if (get().run) { await get().refreshRun(); return }
      const runs = await window.xiaonian.harness.listRuns()
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
      const savedId = localStorage.getItem('xy_active_run')
      const saved = sessions.find(s => s.runId === savedId)
      if (saved) get().restoreSession(saved.id)
    } catch (e) { set({ backendReady: false, backendError: String(e) }) }
  },

  receiveHarnessEvent: (event) => {
    if (get().run?.run_id !== event.run_id) return
    const events = [...new Map([...get().events, event].map(e => [e.seq, e])).values()].sort((a, b) => a.seq - b.seq)
    set({ events, steps: projectEvents(events) })
    void get().refreshRun()
  },

  send: async (text, image) => {
    if (!text.trim() || get().busy) return
    if (!get().backendReady) { set({ backendError: '运行服务尚未连接，请先重新连接。' }); return }
    const { run, activeSessionId } = get()
    set((s) => ({ busy: true, requestBusy: true, backendError: '', messages: [...s.messages, { role: 'user', content: text }] }))
    try {
      const snapshot = run ? await window.xiaonian.harness.sendMessage(run.run_id, text, image) : await window.xiaonian.harness.createRun(text, image)
      if (get().activeSessionId !== activeSessionId) return
      get().applyHarness(snapshot)
      await get().refreshRun()
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
      const snapshot = await window.xiaonian.harness.resume(run.run_id, token, ok ? 'approve' : 'reject')
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) {
      if (get().activeSessionId === activeSessionId) set({ backendError: String(e) })
    } finally {
      if (get().activeSessionId === activeSessionId) { set({ requestBusy: false, busy: runBusy(get().run) }); await get().refreshRun() }
    }
  },

  resolveAction: async (runId, actionId, status, note, reference) => {
    const { run, activeSessionId } = get()
    if (!canResolveAction(run, runId, actionId) || get().busy || !get().backendReady || !note.trim()) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.xiaonian.harness.resolveAction(runId, actionId, status, note.trim(), reference?.trim() || undefined)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: String(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  selectPlan: async (plan) => {
    const { run, activeSessionId } = get()
    if (!run || plan.run_id !== run.run_id || !plan.version || get().busy || !get().backendReady) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.xiaonian.harness.selectPlan(run.run_id, plan.plan_id, plan.version)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: String(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  cancelRun: async () => {
    const { run, activeSessionId } = get()
    if (!run) return
    try {
      const snapshot = await window.xiaonian.harness.cancel(run.run_id)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: String(e) }) }
  },

  resumeBrowser: async () => {
    const { run, activeSessionId } = get()
    const interruptId = run?.interrupt_id || run?.state.interrupt_id
    if (!run || typeof interruptId !== 'string' || get().requestBusy) return
    set({ requestBusy: true, busy: true })
    try {
      const snapshot = await window.xiaonian.harness.resume(run.run_id, interruptId, 'resume')
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
        // 小悠一产出成果卡片，主工作区自动切到"成果区"大视图（confirm 卡除外，避免打断浏览）
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
      district: info.district ?? s.district,
      coords: info.coords || s.coords,
      citySource: info.source ?? s.citySource,
      locAccuracy: info.accuracy ?? s.locAccuracy
    })),
  // 页面内导航：在内置浏览器新开一个标签打开高德网页版路线（不跳系统外部浏览器）
  navigateInApp: (url) => {
    get().addTab(url)
    set({ view: 'browser' })
  },
  openRoute: (t) => set({ routeTarget: t, view: 'outcome' }),
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
    localStorage.removeItem('xy_active_run')
  },
  restoreSession: (id) => {
    const list = upsertSession(get())
    const target = list.find(x => x.id === id)
    if (!target) return
    set({ sessions: list, activeSessionId: id, messages: target.messages, cards: [], steps: [], events: [], run: null,
      busy: !!target.runId, requestBusy: !!target.runId, backendError: target.runId ? '' : '这是旧版会话的只读记录。发送消息会建立新的后端任务。', historyOpen: false })
    if (!target.runId) return
    localStorage.setItem('xy_active_run', target.runId)
    void window.xiaonian.harness.getRun(target.runId).then(async snapshot => {
      if (get().activeSessionId !== id) return
      set({ requestBusy: false })
      get().applyHarness(snapshot)
      await get().refreshRun()
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
  if (s.run) localStorage.setItem('xy_active_run', s.run.run_id)
  return list
}
