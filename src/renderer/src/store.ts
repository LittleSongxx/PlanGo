import { userMessage } from '../../shared/userMessages'
import { create } from 'zustand'
import type { AgentStep, ChatMessage, OutcomeCard, Plan, HarnessSnapshot, HarnessEvent, RequirementEdit, HarnessDeliveryRequest, HarnessDeliveryResult, OfferSelection, RoutePathSegment } from '@shared/types'
import { projectHarness, projectEvents, runBusy, canResolveAction, canSelectOffer, row } from './lib/harnessProjection'
import { originFallback as computeOrigin } from './lib/cityCenter'
import { CHAT_MAX, CHAT_MIN } from './lib/layout'
import { migrateLocalStorage } from './lib/storageMigration'
import { loadDraftImage, saveDraftImage } from './lib/draftImages'
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
const COMPOSER_KEY = 'plango_composer'
export interface ComposerDraft { text: string; image?: string; imageRef?: string; selectedPoi?: SelectedPoi; revision: number }
export interface PendingDelivery {
  request: HarnessDeliveryRequest
  sessionId: string
  status: HarnessDeliveryResult['status']
  acceptedRunId?: string
  imageRef?: string
  error?: string
  draftRevision?: number
}
const EMPTY_DRAFT: ComposerDraft = { text: '', revision: 0 }
function loadComposer(): { activeSessionId?: string; drafts: Record<string, ComposerDraft>; pendingDelivery: PendingDelivery | null; error?: string } {
  try {
    const value = JSON.parse(localStorage.getItem(COMPOSER_KEY) || '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid composer')
    if (value.activeSessionId !== undefined && typeof value.activeSessionId !== 'string') throw new Error('Invalid session')
    if (value.drafts !== undefined && (!value.drafts || typeof value.drafts !== 'object' || Array.isArray(value.drafts))) throw new Error('Invalid drafts')
    for (const draft of Object.values(value.drafts || {}) as ComposerDraft[]) {
      if (!draft || typeof draft.text !== 'string' || !Number.isSafeInteger(draft.revision) || (draft.image !== undefined && typeof draft.image !== 'string') || (draft.imageRef !== undefined && typeof draft.imageRef !== 'string')) throw new Error('Invalid draft')
    }
    if (value.pendingDelivery && (!value.pendingDelivery.request || typeof value.pendingDelivery.request.requestId !== 'string' || !value.pendingDelivery.request.requestId || typeof value.pendingDelivery.request.text !== 'string' || typeof value.pendingDelivery.sessionId !== 'string' || !['not_sent', 'unconfirmed', 'accepted', 'delivered'].includes(value.pendingDelivery.status))) throw new Error('Invalid pending delivery')
    return { activeSessionId: value.activeSessionId, drafts: value.drafts || {}, pendingDelivery: value.pendingDelivery || null }
  } catch { return { drafts: {}, pendingDelivery: null, error: '本地草稿记录损坏或无法读取，已停止新发送并保留原记录。请先恢复本地存储中的 plango_composer，避免重复发送。' } }
}
const initialComposer = loadComposer()
const initialSessionId = initialComposer.activeSessionId || 'sess_' + crypto.randomUUID()

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
  paths?: RoutePathSegment[]
  planned?: { distanceKm?: number; durationMin?: number; extra?: string; summary?: string }
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
  selectOffer: (runId: string, selection: OfferSelection) => Promise<void>
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
  drafts: Record<string, ComposerDraft>
  pendingDelivery: PendingDelivery | null
  deliveryBusy: boolean
  storageError: string
  setDraft: (draft: Partial<Pick<ComposerDraft, 'text' | 'image' | 'imageRef' | 'selectedPoi'>>, sessionId?: string) => void
  persistComposer: () => Promise<boolean>
  hydrateComposer: () => Promise<boolean>
  composerReady: boolean
  discardPendingDelivery: () => Promise<void>
  recoverDelivery: (retry?: boolean) => Promise<void>
  openPendingDelivery: () => void
  send: (text: string, image?: string, selectedPoi?: SelectedPoi) => Promise<void>
  confirm: (token: string, ok: boolean) => Promise<void>

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
let composerWrite = Promise.resolve(true)
let composerHydration: Promise<boolean> | null = null

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
  chatWidth: 560,
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
  activeSessionId: initialSessionId,
  drafts: initialComposer.drafts,
  pendingDelivery: initialComposer.pendingDelivery,
  deliveryBusy: false,
  storageError: initialComposer.error || '',
  composerReady: !initialComposer.error && !Object.values(initialComposer.drafts).some(draft => draft.imageRef && !draft.image) && !(initialComposer.pendingDelivery?.imageRef && !initialComposer.pendingDelivery.request.image),
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
    catch (error) { set({ browserError: userMessage(error, 'browser') }) }
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
      if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(e), backendReady: false })
    } finally { if (refreshingRun === run.run_id) refreshingRun = null }
  },

  hydrateHarness: async () => {
    await get().hydrateComposer()
    const activeSessionId = get().activeSessionId
    try {
      const status = await window.plango.harness.status()
      set({ backendReady: status.ready, backendError: status.error ? userMessage(status.error) : '' })
      if (!status.ready) return
      if (get().pendingDelivery) await get().recoverDelivery()
      if (get().activeSessionId !== activeSessionId) return
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
      const savedId = !initialComposer.activeSessionId && activeSessionId === initialSessionId ? localStorage.getItem('plango_active_run') : null
      const saved = sessions.find(s => s.id === activeSessionId) || sessions.find(s => s.runId === savedId)
      if (saved) get().restoreSession(saved.id)
    } catch (e) { set({ backendReady: false, backendError: userMessage(e) }) }
  },

  receiveHarnessEvent: (event) => {
    if (get().run?.run_id !== event.run_id) return
    if (event.event_type === 'snapshot') { get().applyHarness(event.payload.snapshot as HarnessSnapshot); return }
    if (event.event_type === 'connection_error') set({ backendReady: false, backendError: userMessage(event.payload.message || '任务连接中断') })
    const events = [...new Map([...get().events, event].map(e => [e.seq, e])).values()].sort((a, b) => a.seq - b.seq)
    set({ events, steps: projectEvents(events) })
  },

  setDraft: (patch, sessionId = get().activeSessionId) => {
    const current = get().drafts[sessionId] || EMPTY_DRAFT
    set(s => ({ drafts: { ...s.drafts, [sessionId]: { ...current, ...patch, ...('image' in patch ? { imageRef: patch.imageRef } : {}), revision: current.revision + 1 } } }))
    get().persistComposer()
  },

  persistComposer: () => {
    const { activeSessionId, drafts, pendingDelivery } = get()
    // Serialize writes so a slow image save cannot overwrite a newer edited draft.
    const operation = composerWrite.then(async () => {
      if (initialComposer.error) return false // Preserve the original corrupt blob; never silently reset a pending request.
      try {
        const savedDrafts = Object.fromEntries(await Promise.all(Object.entries(drafts).map(async ([id, draft]) => [id, {
          ...draft, image: undefined, imageRef: draft.imageRef || (draft.image ? await saveDraftImage(draft.image) : undefined)
        }])))
        const savedPending = pendingDelivery ? { ...pendingDelivery, imageRef: pendingDelivery.imageRef || (pendingDelivery.request.image ? await saveDraftImage(pendingDelivery.request.image) : undefined),
          request: { ...pendingDelivery.request, image: undefined } } : null
        localStorage.setItem(COMPOSER_KEY, JSON.stringify({ activeSessionId, drafts: savedDrafts, pendingDelivery: savedPending }))
        set(s => ({ storageError: '', drafts: Object.fromEntries(Object.entries(s.drafts).map(([id, draft]) => [id, draft.revision === drafts[id]?.revision ? { ...draft, imageRef: savedDrafts[id].imageRef } : draft])),
          pendingDelivery: s.pendingDelivery && s.pendingDelivery.request.requestId === savedPending?.request.requestId ? { ...s.pendingDelivery, imageRef: savedPending.imageRef } : s.pendingDelivery }))
        return true
      } catch {
        set({ storageError: '草稿尚未保存到磁盘（本地存储不可用或空间不足）。请保留当前窗口，复制文字或恢复存储空间后重试。' })
        return false
      }
    })
    composerWrite = operation
    return operation
  },

  hydrateComposer: async () => {
    if (get().composerReady) return true
    if (initialComposer.error) return false
    if (composerHydration) return composerHydration
    composerHydration = (async () => {
      try {
        const { drafts, pendingDelivery } = get()
        const loadedDrafts = Object.fromEntries(await Promise.all(Object.entries(drafts).map(async ([id, draft]) => [id, {
          ...draft, image: draft.image || (draft.imageRef ? await loadDraftImage(draft.imageRef) : undefined)
        }])))
        const loadedPending = pendingDelivery?.imageRef && !pendingDelivery.request.image ? { ...pendingDelivery, request: { ...pendingDelivery.request, image: await loadDraftImage(pendingDelivery.imageRef) } } : pendingDelivery
        set({ drafts: loadedDrafts, pendingDelivery: loadedPending, composerReady: true, storageError: '' })
        return true
      } catch (error) { set({ storageError: `原图片尚未恢复，已保留附件引用并停止新发送。${userMessage(error, 'storage')}` }); return false }
      finally { composerHydration = null }
    })()
    return composerHydration
  },

  discardPendingDelivery: async () => {
    const pending = get().pendingDelivery
    if (!pending || pending.status !== 'not_sent' || get().deliveryBusy || !get().composerReady) return
    const current = get().drafts[pending.sessionId]
    if (!current?.text && !current?.image) get().setDraft({ text: pending.request.text, image: pending.request.image, imageRef: pending.imageRef, selectedPoi: pending.request.selectedPoi }, pending.sessionId)
    else if (pending.request.selectedPoi) get().setDraft({ selectedPoi: pending.request.selectedPoi }, pending.sessionId)
    set({ pendingDelivery: null, backendError: '', deliveryBusy: true })
    try { if (!await get().persistComposer()) set({ pendingDelivery: pending }) }
    finally { set({ deliveryBusy: false }) }
  },

  send: async (text, image, selectedPoi) => {
    if (!text.trim() || get().busy || get().deliveryBusy || !get().composerReady) return
    if (get().pendingDelivery) { set({ backendError: '还有一条消息待确认，请先在发送状态中取回或继续原请求。' }); return }
    if (selectedPoi) get().newSession()
    else selectedPoi = get().drafts[get().activeSessionId]?.selectedPoi
    const { run, activeSessionId, sessions, drafts } = get()
    const draft = drafts[activeSessionId] || EMPTY_DRAFT
    const pendingDelivery: PendingDelivery = {
      request: { requestId: crypto.randomUUID(), text, image, selectedPoi, runId: run?.run_id || sessions.find(s => s.id === activeSessionId)?.runId },
      sessionId: activeSessionId, status: 'not_sent',
      draftRevision: draft.text.trim() === text.trim() && draft.image === image ? draft.revision : undefined
    }
    set({ pendingDelivery, backendError: '' })
    // Persist the stable identity before crossing IPC; a failed save never starts a request.
    if (!await get().persistComposer()) return
    get().persistSession()
    await get().recoverDelivery(true)
  },

  recoverDelivery: async (retry = false) => {
    const pending = get().pendingDelivery
    if (!pending || get().deliveryBusy) return
    if (!await get().hydrateComposer()) return
    const restoredPending = get().pendingDelivery
    if (!restoredPending || restoredPending.request.requestId !== pending.request.requestId || get().deliveryBusy) return
    if (!get().backendReady) { set({ backendError: '运行服务尚未连接，输入与原请求已保留，请先重新连接。' }); return }
    const shouldSend = retry && (pending.status === 'not_sent' || pending.status === 'unconfirmed')
    // Checking after reconnect/restart is read-only. An uncertain request never gets a new identity.
    set({ deliveryBusy: true, pendingDelivery: shouldSend ? { ...restoredPending, status: 'unconfirmed', error: undefined } : restoredPending })
    if (!await get().persistComposer()) { set({ deliveryBusy: false, pendingDelivery: pending }); return }
    if (get().activeSessionId === pending.sessionId) set({ busy: true, requestBusy: true })
    try {
      const result = shouldSend ? await window.plango.harness.deliver(restoredPending.request) : await window.plango.harness.checkDelivery(pending.request.requestId)
      if (result.requestId !== pending.request.requestId) throw new Error('发送回执身份不匹配，请重新核对原请求。')
      const expectedRunId = pending.request.runId || pending.acceptedRunId
      if (expectedRunId && result.runId && result.runId !== expectedRunId) throw new Error('回执目标与原任务不一致，已保留原请求，请重新核对。')
      if (pending.acceptedRunId && ['not_sent', 'unconfirmed'].includes(result.status)) throw new Error('原请求已有接受记录，本次查询尚未确认，请继续取回原任务。')
      const updated = { ...restoredPending, status: result.status, acceptedRunId: result.runId || pending.acceptedRunId, error: result.error }
      if (result.status === 'delivered' && (!result.snapshot || result.snapshot.run_id !== updated.acceptedRunId)) throw new Error('已接收消息，但任务快照仍需核对。')
      set({ pendingDelivery: updated })
      if (updated.acceptedRunId) {
        const sessions = get().sessions.map(session => session.id === pending.sessionId ? { ...session, runId: updated.acceptedRunId } : session)
        set({ sessions }); saveSessions(sessions)
      }
      if (result.status === 'delivered' && result.snapshot) {
        const { drafts } = get(), currentDraft = drafts[pending.sessionId]
        if (pending.draftRevision !== undefined && currentDraft?.revision === pending.draftRevision) {
          set({ drafts: { ...drafts, [pending.sessionId]: { text: '', revision: currentDraft.revision + 1 } } })
        }
        if (get().activeSessionId === pending.sessionId) get().applyHarness(result.snapshot)
        else {
          const projected = projectHarness(result.snapshot)
          const sessions = get().sessions.map(session => session.id === pending.sessionId ? { ...session, runId: result.snapshot!.run_id, messages: projected.messages, updatedAt: Date.now() } : session)
          set({ sessions }); saveSessions(sessions)
        }
        set({ pendingDelivery: null })
        // If disk is full, retain the identity in memory too. A later check safely finishes the same delivery.
        if (!await get().persistComposer()) set({ pendingDelivery: updated })
      } else await get().persistComposer()
    } catch (error) {
      const latest = get().pendingDelivery || pending
      set({ pendingDelivery: { ...latest, status: latest.status === 'accepted' || latest.acceptedRunId ? 'accepted' : 'unconfirmed', error: String(error) } })
      await get().persistComposer()
    } finally {
      set({ deliveryBusy: false })
      if (get().activeSessionId === pending.sessionId) {
        set({ requestBusy: false, busy: runBusy(get().run) })
        get().persistSession()
      }
    }
  },

  openPendingDelivery: () => {
    const pending = get().pendingDelivery
    if (!pending || pending.sessionId === get().activeSessionId) return
    if (!get().sessions.some(session => session.id === pending.sessionId)) {
      set(s => ({ sessions: [...s.sessions, { id: pending.sessionId, title: pending.request.text.slice(0, 26), createdAt: Date.now(), updatedAt: Date.now(), city: '', messages: [], cards: [], runId: pending.acceptedRunId || pending.request.runId }] }))
    }
    get().restoreSession(pending.sessionId)
  },

  confirm: async (token, ok) => {
    const { run, activeSessionId } = get()
    if (!run || get().busy || token !== (run.interrupt_id || run.state.interrupt_id)) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.resume(run.run_id, token, ok ? 'approve' : 'reject')
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) {
      if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(e) })
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
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  selectPlan: async (plan) => {
    const { run, activeSessionId } = get()
    if (!run || plan.run_id !== run.run_id || !plan.version || get().busy || !get().backendReady) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.selectPlan(run.run_id, plan.plan_id, plan.version)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  selectOffer: async (runId, selection) => {
    const { run, activeSessionId } = get()
    if (!run || run.run_id !== runId || !canSelectOffer(run, selection) || get().busy || get().pendingDelivery || !get().backendReady) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.selectOffer(runId, selection)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (error) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(error) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  editRequirements: async (edit) => {
    const { run, activeSessionId } = get()
    if (!run || get().busy || get().pendingDelivery || !get().backendReady) return
    set({ requestBusy: true, busy: true, backendError: '' })
    try {
      const snapshot = await window.plango.harness.editRequirements(run.run_id, edit)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (error) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(error) }) }
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
    } catch (error) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(error) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  cancelRun: async () => {
    const { run, activeSessionId } = get()
    if (!run) return
    try {
      const snapshot = await window.plango.harness.cancel(run.run_id)
      if (get().activeSessionId === activeSessionId) get().applyHarness(snapshot)
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(e) }) }
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
    } catch (error) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(error) }) }
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
    } catch (e) { if (get().activeSessionId === activeSessionId) set({ backendError: userMessage(e) }) }
    finally { if (get().activeSessionId === activeSessionId) set({ requestBusy: false, busy: runBusy(get().run) }) }
  },

  addProactive: (p) => set((s) => ({ proactive: [p, ...s.proactive].slice(0, 20) })),
  setSettings: (open) => set({ settingsOpen: open }),
  setView: (v) => set({ view: v }),
  setChatWidth: (w) => set({ chatWidth: Math.max(CHAT_MIN, Math.min(CHAT_MAX, w)) }),
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
    const previous = get().run?.run_id
    if (previous) void window.plango.harness.releaseRun(previous)
    set((s) => ({ sessions: upsertSession(s), activeSessionId: 'sess_' + crypto.randomUUID(),
      messages: [{ role: 'assistant', content: '开始新的安排吧。告诉我地点、人数和预算。' }], cards: [], steps: [], events: [], run: null,
      busy: false, requestBusy: false, backendError: '', historyOpen: false }))
    localStorage.removeItem('plango_active_run')
    get().persistComposer()
  },
  restoreSession: (id) => {
    const list = upsertSession(get())
    const target = list.find(x => x.id === id)
    if (!target) return
    const previous = get().run?.run_id
    if (previous && previous !== target.runId) void window.plango.harness.releaseRun(previous)
    set({ sessions: list, activeSessionId: id, messages: target.messages, cards: [], steps: [], events: [], run: null,
      busy: !!target.runId, requestBusy: !!target.runId, backendError: target.runId || get().drafts[id] || get().pendingDelivery?.sessionId === id ? '' : '这是旧版会话的只读记录。发送消息会建立新的后端任务。', historyOpen: false })
    get().persistComposer()
    if (!target.runId) return
    localStorage.setItem('plango_active_run', target.runId)
    void window.plango.harness.getRun(target.runId).then(async snapshot => {
      if (get().activeSessionId !== id) return
      set({ requestBusy: false })
      get().applyHarness(snapshot)
    }).catch(e => {
      if (get().activeSessionId === id) set({ busy: false, requestBusy: false, backendError: userMessage(e), backendReady: false })
    })
  },
  deleteSession: (id) => {
    const state = get()
    if (state.pendingDelivery?.sessionId === id) { set({ backendError: '此会话还有消息待确认，请先取回原请求再隐藏。' }); return }
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
  const pending = s.pendingDelivery?.sessionId === s.activeSessionId ? s.pendingDelivery : null
  const draft = s.drafts[s.activeSessionId]
  if (!firstUser && s.cards.length === 0 && !pending && !draft?.text && !draft?.image) return sessions
  const title = (typeof firstUser?.content === 'string' ? firstUser.content : '') || pending?.request.text || draft?.text || '图片草稿'
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
