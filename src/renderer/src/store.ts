import { create } from 'zustand'
import type { AgentStep, ChatMessage, OutcomeCard, Plan } from '@shared/types'
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
}

const SESS_KEY = 'xy_sessions'
function loadSessions(): SavedSession[] {
  try {
    return JSON.parse(localStorage.getItem(SESS_KEY) || '[]')
  } catch {
    return []
  }
}
function saveSessions(list: SavedSession[]): void {
  try {
    localStorage.setItem(SESS_KEY, JSON.stringify(list.slice(0, 30)))
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
  send: (text: string) => Promise<void>
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

export const useStore = create<State>((set, get) => ({
  tabs: [],
  activeTabId: null,
  messages: [
    {
      role: 'assistant',
      content:
        '你好，我是小悠 👋 你的 AI 本地生活管家。\n我会按你当前定位的城市，去查真实高德/美团/点评的门店，一次给你 3 套（经济/均衡/特色）周末方案，还能比价、点菜、排号、发同行人确认。\n跟我说一句就行，比如：「这周六下午带娃出去玩4小时，孩子5岁，老婆减脂，预算人均120」。'
    }
  ],
  steps: [],
  cards: [],
  busy: false,
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
  activeSessionId: 'sess_' + Date.now().toString(36),
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

  send: async (text) => {
    const { messages } = get()
    set({ busy: true, steps: [], messages: [...messages, { role: 'user', content: text }] })
    try {
      const reply = await window.xiaonian.chat(text, messages.slice(-6))
      set((s) => ({ messages: [...s.messages, { role: 'assistant', content: reply.content }] }))
    } catch (e) {
      set((s) => ({ messages: [...s.messages, { role: 'assistant', content: '出错了：' + (e as Error).message }] }))
    } finally {
      set({ busy: false })
      get().persistSession()
    }
  },

  confirm: async (token, ok) => {
    set({ busy: true })
    try {
      const reply = await window.xiaonian.confirm(token, ok)
      set((s) => ({
        messages: [...s.messages, { role: 'assistant', content: reply.content }],
        // 移除该确认卡
        cards: s.cards.filter((c) => c.kind !== 'confirm' || (c as { token: string }).token !== token)
      }))
    } finally {
      set({ busy: false })
    }
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
  openRoute: (t) => set({ routeTarget: t }),
  closeRoute: () => set({ routeTarget: null }),
  openShare: (p) => set({ sharePlan: p }),
  closeShare: () => set({ sharePlan: null }),

  setHistoryOpen: (open) => set({ historyOpen: open }),
  setSidePanelOpen: (open) => set({ sidePanelOpen: open }),
  setDiscoverOpen: (v) => set({ discoverOpen: v }),
  setAiBrowsing: (v) => set((s) => ({ aiBrowsing: { active: v.active, site: v.site ?? s.aiBrowsing.site, action: v.action ?? s.aiBrowsing.action } })),
  newSession: () =>
    set((s) => {
      const list = upsertSession(s)
      return {
        sessions: list,
        activeSessionId: 'sess_' + Date.now().toString(36),
        messages: [s.messages[0]].filter(Boolean),
        cards: [],
        steps: [],
        historyOpen: false
      }
    }),
  restoreSession: (id) =>
    set((s) => {
      const list = upsertSession(s)
      const target = list.find((x) => x.id === id)
      if (!target) return { sessions: list }
      return {
        sessions: list,
        activeSessionId: target.id,
        messages: target.messages.length ? target.messages : s.messages,
        cards: target.cards,
        steps: [],
        historyOpen: false,
        view: target.cards.length ? 'outcome' : s.view
      }
    }),
  deleteSession: (id) =>
    set((s) => {
      const sessions = s.sessions.filter((x) => x.id !== id)
      saveSessions(sessions)
      return { sessions }
    }),
  persistSession: () => set((s) => ({ sessions: upsertSession(s) }))
}))

// 把当前会话（消息+成果卡）快照进历史列表（按 activeSessionId upsert），并落 localStorage。
function upsertSession(s: State): SavedSession[] {
  const firstUser = s.messages.find((m) => m.role === 'user')
  if (!firstUser && s.cards.length === 0) return s.sessions // 空会话不存
  const title = (typeof firstUser?.content === 'string' ? firstUser.content : '') || '新会话'
  const now = Date.now()
  const existing = s.sessions.find((x) => x.id === s.activeSessionId)
  const snap: SavedSession = {
    id: s.activeSessionId,
    title: title.length > 26 ? title.slice(0, 26) + '…' : title,
    createdAt: existing?.createdAt || now,
    updatedAt: now,
    city: s.city,
    messages: s.messages,
    cards: s.cards
  }
  const rest = s.sessions.filter((x) => x.id !== s.activeSessionId)
  const list = [snap, ...rest].sort((a, b) => b.updatedAt - a.updatedAt)
  saveSessions(list)
  return list
}
