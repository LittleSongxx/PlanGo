// preload：一能力一方法，contextBridge 暴露给渲染层。
import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc'

const api = {
  desktopReady: () => ipcRenderer.invoke('desktop:ready'),
  reminders: {
    list: () => ipcRenderer.invoke(IPC.reminderRequest, 'list', {}),
    create: (text: string, at: string) => ipcRenderer.invoke(IPC.reminderRequest, 'create', { text, at }),
    remove: (id: string) => ipcRenderer.invoke(IPC.reminderRequest, 'remove', { id })
  },
  harness: {
    createRun: (text: string, image?: string) => ipcRenderer.invoke(IPC.harnessRequest, 'createRun', { text, image }),
    getRun: (runId: string) => ipcRenderer.invoke(IPC.harnessRequest, 'getRun', { runId }),
    sendMessage: (runId: string, text: string, image?: string) => ipcRenderer.invoke(IPC.harnessRequest, 'sendMessage', { runId, text, image }),
    replan: (runId: string, reason: string) => ipcRenderer.invoke(IPC.harnessRequest, 'replan', { runId, reason }),
    selectPlan: (runId: string, planId: string, planVersion: number) => ipcRenderer.invoke(IPC.harnessRequest, 'selectPlan', { runId, planId, planVersion }),
    resolveAction: (runId: string, actionId: string, status: string, note: string, reference?: string) => ipcRenderer.invoke(IPC.harnessRequest, 'resolveAction', { runId, actionId, status, note, reference }),
    cancel: (runId: string) => ipcRenderer.invoke(IPC.harnessRequest, 'cancel', { runId }),
    resume: (runId: string, interruptId: string, decision: string, text?: string) => ipcRenderer.invoke(IPC.harnessRequest, 'resume', { runId, interruptId, decision, text }),
    events: (runId: string, after: number) => ipcRenderer.invoke(IPC.harnessRequest, 'events', { runId, after }),
    listRuns: () => ipcRenderer.invoke(IPC.harnessRequest, 'listRuns', {}),
    status: () => ipcRenderer.invoke(IPC.harnessRequest, 'status', {})
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

  // 浏览器桥
  onBrowserExec: (cb: (p: { id: number; action: string; args: Record<string, unknown> }) => void) =>
    ipcRenderer.on(IPC.browserExec, (_e, p) => cb(p)),
  browserExecResult: (id: number, result: unknown) => ipcRenderer.send(IPC.browserExecResult, { id, result }),
  browserEval: (contentsId: number, code: string) => ipcRenderer.invoke('browser:eval', contentsId, code),

  // 配置 / LLM
  getConfig: () => ipcRenderer.invoke(IPC.getConfig),
  setConfig: (patch: unknown) => ipcRenderer.invoke(IPC.setConfig, patch),
  setSource: (source: string) => ipcRenderer.invoke('config:setSource', source),
  pingLlm: () => ipcRenderer.invoke(IPC.pingLlm),

  // 技能
  listSkills: () => ipcRenderer.invoke(IPC.listSkills),
  toggleSkill: (id: string, enabled: boolean) => ipcRenderer.invoke(IPC.toggleSkill, { id, enabled }),

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
  memoryDelete: (payload: { kind: 'pref' | 'fav'; value: string }) => ipcRenderer.invoke(IPC.memoryDelete, payload),
  memoryClear: () => ipcRenderer.invoke(IPC.memoryClear),

  // 分享协作
  shareCreate: (payload: { plan?: unknown; city?: string }) => ipcRenderer.invoke(IPC.shareCreate, payload),
  shareFeedback: (id: string) => ipcRenderer.invoke(IPC.shareFeedback, id),

  // 攻略导入
  guideSetImage: (dataUrl: string) => ipcRenderer.invoke(IPC.guideSetImage, dataUrl),

  // 附近发现 / 优惠发现
  discoverFetch: (city?: string) => ipcRenderer.invoke(IPC.discoverFetch, city),
  dealsFetch: (city?: string) => ipcRenderer.invoke(IPC.dealsFetch, city),

  // 定位
  getLocation: () => ipcRenderer.invoke(IPC.locationGet),
  detectLocation: () => ipcRenderer.invoke('location:detect'),
  setCity: (city: string) => ipcRenderer.invoke(IPC.locationSet, city),
  onLocation: (cb: (l: unknown) => void) => sub(IPC.locationUpdate, cb),
  reportLocation: (p: { city?: string; coords?: string }) => ipcRenderer.invoke('location:report', p),

  // 高德 JS SDK + 外链
  getAmapJsConfig: () => ipcRenderer.invoke('amap:jsConfig'),
  openExternal: (url: string) => ipcRenderer.invoke('shell:openExternal', url)
}

function sub(channel: string, cb: (p: unknown) => void): () => void {
  const listener = (_e: unknown, payload: unknown) => cb(payload)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

contextBridge.exposeInMainWorld('plango', api)

export type PlangoApi = typeof api
