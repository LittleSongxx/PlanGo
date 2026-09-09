// preload：一能力一方法，contextBridge 暴露给渲染层。
import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc'
import type { HarnessFeedbackInput, RequirementEdit, HarnessDeliveryRequest, OfferSourceRef, OfferSelection } from '../shared/types'
import type { LocationInfo, SelectedPoi } from '../shared/location'
import type { BrowserIntent, BrowserLayout, BrowserViewState, BrowserActivity } from '../shared/browserView'

const api = {
  desktopReady: () => ipcRenderer.invoke('desktop:ready'),
  reminders: {
    list: () => ipcRenderer.invoke(IPC.reminderRequest, 'list', {}),
    create: (text: string, at: string) => ipcRenderer.invoke(IPC.reminderRequest, 'create', { text, at }),
    remove: (id: string) => ipcRenderer.invoke(IPC.reminderRequest, 'remove', { id })
  },
  harness: {
    merchantCandidates: (runId: string, sourceRef: OfferSourceRef) => ipcRenderer.invoke(IPC.harnessRequest, 'merchantCandidates', { runId, sourceRef }),
    selectOffer: (runId: string, selection: OfferSelection) => ipcRenderer.invoke(IPC.harnessRequest, 'selectOffer', { runId, selection }),
    deliver: (request: HarnessDeliveryRequest) => ipcRenderer.invoke(IPC.harnessRequest, 'deliver', request),
    checkDelivery: (requestId: string) => ipcRenderer.invoke(IPC.harnessRequest, 'checkDelivery', { requestId }),
    editRequirements: (runId: string, edit: RequirementEdit) => ipcRenderer.invoke(IPC.harnessRequest, 'editRequirements', { runId, edit }),
    resumePreparation: (runId: string, planId: string, planVersion: number, approvalId: string) => ipcRenderer.invoke(IPC.harnessRequest, 'resumePreparation', { runId, planId, planVersion, approvalId }),
    decideDraft: (runId: string, interruptId: string, planId: string, planVersion: number, decision: 'save' | 'prepare') => ipcRenderer.invoke(IPC.harnessRequest, 'decideDraft', { runId, interruptId, planId, planVersion, decision }),
    feedback: (runId: string, value: HarnessFeedbackInput) => ipcRenderer.invoke(IPC.harnessRequest, 'feedback', { runId, value }),
    createRun: (text: string, image?: string, selectedPoi?: SelectedPoi) => ipcRenderer.invoke(IPC.harnessRequest, 'createRun', { text, image, selectedPoi }),
    getRun: (runId: string) => ipcRenderer.invoke(IPC.harnessRequest, 'getRun', { runId }),
    sendMessage: (runId: string, text: string, image?: string) => ipcRenderer.invoke(IPC.harnessRequest, 'sendMessage', { runId, text, image }),
    replan: (runId: string, reason: string) => ipcRenderer.invoke(IPC.harnessRequest, 'replan', { runId, reason }),
    selectPlan: (runId: string, planId: string, planVersion: number) => ipcRenderer.invoke(IPC.harnessRequest, 'selectPlan', { runId, planId, planVersion }),
    resolveAction: (runId: string, actionId: string, status: string, note: string, reference?: string) => ipcRenderer.invoke(IPC.harnessRequest, 'resolveAction', { runId, actionId, status, note, reference }),
    cancel: (runId: string) => ipcRenderer.invoke(IPC.harnessRequest, 'cancel', { runId }),
    resume: (runId: string, interruptId: string, decision: string, text?: string) => ipcRenderer.invoke(IPC.harnessRequest, 'resume', { runId, interruptId, decision, text }),
    listRuns: () => ipcRenderer.invoke(IPC.harnessRequest, 'listRuns', {}),
    status: (checkModel = false) => ipcRenderer.invoke(IPC.harnessRequest, 'status', { checkModel })
  },
  onHarnessEvent: (cb: (event: unknown) => void) => sub(IPC.harnessEvent, cb),
  // 对话主线
  chat: (message: string, history: unknown[]) => ipcRenderer.invoke(IPC.agentChat, { message, history }),
  confirm: (token: string, ok: boolean) => ipcRenderer.invoke(IPC.agentConfirm, { token, ok }),

  // 事件订阅（main → renderer）
  onStep: (cb: (s: unknown) => void) => sub(IPC.agentStep, cb),
  onCard: (cb: (c: unknown) => void) => sub(IPC.agentStreamCard, cb),
  onProactive: (cb: (p: unknown) => void) => sub(IPC.proactivePush, cb),
  onImIncoming: (cb: (m: unknown) => void) => sub(IPC.imIncoming, cb),

  browser: {
    request: (intent: BrowserIntent) => ipcRenderer.invoke(IPC.browserRequest, intent),
    layout: (value: BrowserLayout) => ipcRenderer.invoke(IPC.browserLayout, value),
    onState: (cb: (value: BrowserViewState) => void) => sub(IPC.browserState, cb),
    onActivity: (cb: (value: BrowserActivity) => void) => sub(IPC.browserActivity, cb)
  },

  // 配置 / LLM
  getConfig: () => ipcRenderer.invoke(IPC.getConfig),
  setConfig: (patch: unknown) => ipcRenderer.invoke(IPC.setConfig, patch),
  setSource: (source: string) => ipcRenderer.invoke('config:setSource', source),
  pingLlm: () => ipcRenderer.invoke(IPC.pingLlm),

  // 技能
  listSkills: () => ipcRenderer.invoke(IPC.listSkills),
  toggleSkill: (id: string, enabled: boolean) => ipcRenderer.invoke(IPC.toggleSkill, id, enabled),

  // IM
  imStatus: () => ipcRenderer.invoke(IPC.imStatus),
  imLoginQr: () => ipcRenderer.invoke(IPC.imLoginQr),
  imSimulate: (text: string, from?: string) => ipcRenderer.invoke('im:simulate', { text, from }),

  // 主动关心
  proactiveList: () => ipcRenderer.invoke(IPC.proactiveList),
  proactiveTrigger: () => ipcRenderer.invoke('proactive:trigger'),

  // 记忆
  getMemory: () => ipcRenderer.invoke(IPC.memoryGet),
  memoryGreeting: () => ipcRenderer.invoke(IPC.memoryGreeting),
  memoryDelete: (payload: { kind: 'pref' | 'fav' | 'episode'; value: string }) => ipcRenderer.invoke(IPC.memoryDelete, payload),
  memorySave: (text: string, polarity: 'like' | 'dislike') => ipcRenderer.invoke(IPC.memorySave, { text, polarity }),
  memoryClear: () => ipcRenderer.invoke(IPC.memoryClear),

  // 分享协作
  shareCreate: (payload: { plan?: unknown; city?: string }) => ipcRenderer.invoke(IPC.shareCreate, payload),
  shareFeedback: (id: string) => ipcRenderer.invoke(IPC.shareFeedback, id),

  // 攻略导入
  guideSetImage: (dataUrl: string) => ipcRenderer.invoke(IPC.guideSetImage, dataUrl),

  // 附近发现 / 优惠发现
  discoverFetch: (request?: { city?: string; refresh?: boolean }) => ipcRenderer.invoke(IPC.discoverFetch, request),
  dealsFetch: (city?: string) => ipcRenderer.invoke(IPC.dealsFetch, city),

  // 定位
  getLocation: () => ipcRenderer.invoke(IPC.locationGet),
  detectLocation: () => ipcRenderer.invoke('location:detect'),
  setCity: (city: string) => ipcRenderer.invoke(IPC.locationSet, city),
  onLocation: (cb: (l: unknown) => void) => sub(IPC.locationUpdate, cb),
  reportLocation: (p: LocationInfo & { userInitiated?: boolean }) => ipcRenderer.invoke('location:report', p),
  geo: {
    geocode: (request: { address: string; city?: string }) => ipcRenderer.invoke('geo:geocode', request),
    reverse: (request: { longitude: number; latitude: number }) => ipcRenderer.invoke('geo:reverse', request)
  },

  // 高德 JS SDK + 外链
  getAmapJsConfig: () => ipcRenderer.invoke('amap:jsConfig'),
  openExternal: (url: string) => ipcRenderer.invoke('shell:openExternal', url)
}

function sub<T>(channel: string, cb: (p: T) => void): () => void {
  const listener = (_e: unknown, payload: T) => cb(payload)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

contextBridge.exposeInMainWorld('plango', api)

export type PlangoApi = typeof api
