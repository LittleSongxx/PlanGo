// IPC 注册：renderer 调 main（handle）+ 浏览器动作回执（on）。
import { ipcMain, shell } from 'electron'
import { IPC } from '@shared/ipc'
import { runAgent, confirmAction, cancelAction } from './agent'
import { resolveBrowserAction } from './browser-bridge'
import { getConfig, getConfigMasked, setConfig, type AppConfig } from './config'
import { pingLlm } from './llm'
import { listSkills, toggleSkill } from './skills/loader'
import { getWeChatBridge } from './im/bridge'
import { proactive } from './proactive'
import { getProfile, recallGreeting, deletePreference, deleteFavorite, clearMemory } from './brain/memory'
import { citiesWithData } from './data/mockdb'
import { detectLocation, getLocation, setManualCity } from './location'
import { createShare, getShareFeedback } from './share/server'
import { computeDiscover, computeDeals } from './discover'
import { session } from './agent/session'
import QRCode from 'qrcode'
import type { ChatMessage, Plan } from '@shared/types'

export function registerIpc(): void {
  ipcMain.handle(IPC.agentChat, async (_e, payload: { message: string; history?: ChatMessage[] }) => {
    return runAgent(payload.message, payload.history || [])
  })

  ipcMain.handle(IPC.agentConfirm, async (_e, payload: { token: string; ok: boolean }) => {
    return payload.ok ? confirmAction(payload.token) : cancelAction(payload.token)
  })

  // 浏览器动作回执（renderer → main）
  ipcMain.on(IPC.browserExecResult, (_e, payload: { id: number; result: unknown }) => {
    resolveBrowserAction(payload.id, payload.result as never)
  })

  // 配置
  ipcMain.handle(IPC.getConfig, async () => ({ config: getConfigMasked(), cities: citiesWithData().slice(0, 15) }))
  ipcMain.handle(IPC.setConfig, async (_e, patch: Partial<AppConfig>) => {
    setConfig(patch)
    return getConfigMasked()
  })
  ipcMain.handle(IPC.pingLlm, async () => pingLlm())

  // 技能
  ipcMain.handle(IPC.listSkills, async () => listSkills())
  ipcMain.handle(IPC.toggleSkill, async (_e, payload: { id: string; enabled: boolean }) => {
    toggleSkill(payload.id, payload.enabled)
    return listSkills()
  })

  // IM Bridge
  ipcMain.handle(IPC.imStatus, async () => getWeChatBridge().status())
  ipcMain.handle(IPC.imLoginQr, async () => getWeChatBridge().loginQr())
  ipcMain.handle('im:simulate', async (_e, payload: { text: string; from?: string }) => {
    getWeChatBridge().simulateIncoming(payload.text, payload.from)
    return { ok: true }
  })

  // 主动关心
  ipcMain.handle(IPC.proactiveList, async () => proactive.list())
  ipcMain.handle('proactive:trigger', async () => proactive.triggerNow())

  // 记忆
  ipcMain.handle(IPC.memoryGet, async () => getProfile())
  ipcMain.handle(IPC.memoryGreeting, async () => ({ text: recallGreeting() }))
  ipcMain.handle(IPC.memoryDelete, async (_e, p: { kind: 'pref' | 'fav'; value: string }) => {
    if (p?.kind === 'pref') deletePreference(p.value)
    else if (p?.kind === 'fav') deleteFavorite(p.value)
    return getProfile()
  })
  ipcMain.handle(IPC.memoryClear, async () => {
    clearMemory()
    return getProfile()
  })

  // 数据源快捷切换
  ipcMain.handle('config:setSource', async (_e, source: AppConfig['dataSource']) => {
    setConfig({ dataSource: source })
    return getConfig().dataSource
  })

  // 定位
  ipcMain.handle(IPC.locationGet, async () => getLocation())
  ipcMain.handle('location:detect', async () => detectLocation(true))
  ipcMain.handle(IPC.locationSet, async (_e, city: string) => setManualCity(city))
  // 渲染层用高德 JS SDK 定位后，把城市/坐标写回配置（供规划用作圆心/起点）
  ipcMain.handle('location:report', async (_e, payload: { city?: string; coords?: string }) => {
    const patch: Record<string, string> = {}
    if (payload?.city) patch.city = payload.city
    if (payload?.coords) patch.coords = payload.coords
    if (Object.keys(patch).length) setConfig(patch)
    return getConfig()
  })

  // 高德 JS SDK 配置（客户端 key，渲染层加载地图/定位用）
  ipcMain.handle('amap:jsConfig', async () => {
    const a = getConfig().amap
    return { jsKey: a.jsKey, jsSecurity: a.jsSecurity, webKey: a.key }
  })

  // 打开外部链接（导航/打车 deeplink）
  ipcMain.handle('shell:openExternal', async (_e, url: string) => {
    if (/^https?:\/\/|^tel:|^amapuri:/.test(url)) await shell.openExternal(url)
    return true
  })

  // 分享协作：创建分享（用渲染层传来的方案快照，兜底用会话最近方案）→ 返回局域网 URL + 二维码
  ipcMain.handle(IPC.shareCreate, async (_e, payload: { plan?: Plan; city?: string }) => {
    const plan = payload?.plan || session.lastPlan
    if (!plan) return { ok: false, error: '还没有可分享的方案' }
    const city = payload?.city || getConfig().city
    const { id, url } = createShare(plan, city)
    let qr = ''
    try {
      qr = await QRCode.toDataURL(url, { width: 260, margin: 1, color: { dark: '#1a1a1a', light: '#ffffff' } })
    } catch {
      /* ignore */
    }
    return { ok: true, id, url, qr }
  })

  // 分享协作：拉取反馈（投票 + 评语 + 可并入指令）
  ipcMain.handle(IPC.shareFeedback, async (_e, id: string) => getShareFeedback(id))

  // 攻略导入：暂存用户上传的截图（dataURL），供 import_guide 视觉抽取
  ipcMain.handle(IPC.guideSetImage, async (_e, dataUrl: string) => {
    session.pendingGuideImage = typeof dataUrl === 'string' && dataUrl.startsWith('data:image') ? dataUrl : undefined
    return { ok: !!session.pendingGuideImage }
  })

  // 附近发现 / 优惠发现（独立窗口直连，不经聊天、不堆成果区）
  ipcMain.handle(IPC.discoverFetch, async (_e, city?: string) => computeDiscover(city))
  ipcMain.handle(IPC.dealsFetch, async (_e, city?: string) => computeDeals(city))
}
