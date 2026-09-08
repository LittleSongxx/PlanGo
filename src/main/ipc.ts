// Desktop UI invokes bounded APIs; remote web content has no Harness credentials.
import { ipcMain, shell, session as electronSession, webContents, type IpcMainInvokeEvent } from 'electron'
import { z } from 'zod'
import QRCode from 'qrcode'
import { IPC } from '@shared/ipc'
import { resolveBrowserAction, cancelBrowserRun } from './browser-bridge'
import { getMainWindow, ownsBrowserContents } from './index'
import { getHarness, harnessStatus, restartHarness } from './harness'
import { getConfig, getConfigMasked, getHarnessEnvironment, setConfig } from './config'
import { pingLlm } from './llm'
import { listSkills, toggleSkill } from './skills/loader'
import { detectLocation, getLocation, setManualCity } from './location'
import { createShare, getShareFeedback } from './share/server'
import { computeLiveDiscover } from './discover'
import { projectHarness } from '../renderer/src/lib/harnessProjection'
import type { AgentReply, DealRow, HarnessSnapshot, Plan, POISummary, UserProfile } from '@shared/types'

const id = z.string().min(1).max(512)
const text = z.string().trim().min(1).max(4000)
const image = z.string().max(12_000_000).regex(/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/).optional()
let guideImage: string | undefined

function trusted(event: IpcMainInvokeEvent): void {
  const win = getMainWindow()
  if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) throw new Error('Untrusted IPC sender')
}

function handle(channel: string, listener: (...args: any[]) => unknown): void {
  ipcMain.handle(channel, (event, ...args) => { trusted(event); return listener(...args) })
}

async function memoryProfile(): Promise<UserProfile> {
  const data = await (await getHarness()).request<Record<string, any>>('/api/v1/memory/profile?user_id=desktop')
  return {
    user_id: 'desktop', summary: (data.summaries || []).map((s: any) => typeof s === 'string' ? s : String(s.text || '')).filter(Boolean).join('；'),
    preferences: (data.preferences || []).map((p: any) => ({ text: String(p.text || p.value?.text || p.value || ''), polarity: ['negative', 'dislike'].includes(p.polarity) ? 'negative' : 'positive', strength: typeof p.strength === 'number' ? p.strength : p.confidence ?? 0.6, evidence_count: p.evidence_count ?? 1, source: 'conversation' })),
    favorite_shops: (data.favorites || []).map((p: any) => typeof p === 'string' ? p : String(p.name || p.value?.name || p.value || '')),
    avoid_shops: [], home_city: getConfig().city, footprints: data.footprints || []
  }
}

function projectedReply(run: HarnessSnapshot): AgentReply {
  const projection = projectHarness(run)
  return { content: '任务已由 Harness 受理。', steps: [], cards: projection.cards, activities: [] }
}

export function registerIpc(): void {
  handle('browser:eval', (contentsId: number, code: string) => {
    z.number().int().positive().parse(contentsId)
    z.string().max(250_000).parse(code)
    const target = webContents.fromId(contentsId)
    if (!target || !ownsBrowserContents(contentsId) || target.getType() !== 'webview' || target.session !== electronSession.fromPartition('persist:xiaonian')) throw new Error('Untrusted browser target')
    return target.executeJavaScriptInIsolatedWorld(1001, [{ code }])
  })
  handle(IPC.reminderRequest, async (operation: string, raw: unknown) => {
    const client = await getHarness()
    if (operation === 'list') return client.request('/api/v1/reminders')
    if (operation === 'create') return client.request('/api/v1/reminders', 'POST', z.object({ text, at: z.string().datetime({ offset: true }) }).parse(raw))
    if (operation === 'remove') return client.request(`/api/v1/reminders/${encodeURIComponent(z.object({ id }).parse(raw).id)}`, 'DELETE')
    throw new Error('Unknown reminder operation')
  })
  handle(IPC.harnessRequest, async (operation: string, raw: unknown) => {
    if (operation === 'status') return harnessStatus()
    const client = await getHarness()
    switch (operation) {
      case 'createRun': {
        const p = z.object({ text, image }).parse(raw)
        const attached = p.image || guideImage
        guideImage = undefined
        return client.createRun(p.text, attached)
      }
      case 'getRun': return client.getRun(z.object({ runId: id }).parse(raw).runId)
      case 'listRuns': return client.listRuns()
      case 'sendMessage': {
        const p = z.object({ runId: id, text, image }).parse(raw)
        const attached = p.image || guideImage
        guideImage = undefined
        return client.mutate(p.runId, 'messages', { text: p.text, ...(attached ? { image: attached } : {}) })
      }
      case 'replan': {
        const p = z.object({ runId: id, reason: text }).parse(raw)
        return client.mutate(p.runId, 'replan', { reason: p.reason })
      }
      case 'selectPlan': {
        const p = z.object({ runId: id, planId: id, planVersion: z.number().int().min(1) }).parse(raw)
        return client.selectPlan(p.runId, p.planId, p.planVersion)
      }
      case 'resolveAction': {
        const p = z.object({ runId: id, actionId: id, status: z.enum(['SUCCEEDED', 'FAILED']), note: z.string().trim().min(1).max(500), reference: z.string().max(200).optional() }).parse(raw)
        return client.resolveAction(p.runId, p.actionId, p.status, p.note, p.reference)
      }
      case 'cancel': {
        const p = z.object({ runId: id }).parse(raw)
        cancelBrowserRun(p.runId)
        return client.mutate(p.runId, 'cancel', {})
      }
      case 'resume': {
        const p = z.object({ runId: id, interruptId: id, decision: z.enum(['approve', 'reject', 'edit', 'resume']), text: z.string().max(4000).optional() }).parse(raw)
        return client.resume(p.runId, p.interruptId, p.decision, p.text)
      }
      case 'events': {
        const p = z.object({ runId: id, after: z.number().int().min(0) }).parse(raw)
        return client.events(p.runId, p.after)
      }
      default: throw new Error('Unknown Harness operation')
    }
  })
  handle(IPC.agentChat, async (raw: unknown) => {
    const p = z.object({ message: text }).parse(raw)
    return projectedReply(await (await getHarness()).createRun(p.message))
  })
  handle(IPC.agentConfirm, () => { throw new Error('旧确认已失效，请从当前 Harness 任务重新确认。') })
  ipcMain.on(IPC.browserExecResult, (event, payload) => {
    if (event.sender !== getMainWindow()?.webContents || event.senderFrame !== getMainWindow()?.webContents.mainFrame) return
    if (!Number.isSafeInteger(payload?.id)) return
    resolveBrowserAction(payload.id, payload.result)
  })

  handle(IPC.getConfig, () => ({ config: { ...getConfigMasked(), harness: { baseURL: getHarnessEnvironment().YOYU_BACKEND_URL || 'http://127.0.0.1:8011', autoStart: getHarnessEnvironment().YOYU_BACKEND_AUTOSTART !== 'false' } }, cities: [] }))
  handle(IPC.setConfig, async (raw: unknown) => {
    const patch = z.object({
      llm: z.object({ apiKey: z.string().max(2048), baseURL: z.string().url(), model: z.string().min(1).max(200) }).partial().optional(),
      amap: z.object({ key: z.string().max(2048), jsKey: z.string().max(2048), jsSecurity: z.string().max(2048) }).partial().optional(),
      city: z.string().min(1).max(80).optional(), coords: z.string().max(80).optional()
    }).parse(raw)
    for (const section of [patch.llm, patch.amap]) {
      if (!section) continue
      for (const key of Object.keys(section)) if (String((section as any)[key]).includes('••••')) delete (section as any)[key]
    }
    // Partial nested settings are merged by config; masked placeholders are never persisted.
    setConfig(patch as Parameters<typeof setConfig>[0])
    if (patch.llm || patch.amap) await restartHarness()
    return getConfigMasked()
  })
  handle(IPC.pingLlm, () => pingLlm())
  handle(IPC.listSkills, () => listSkills())
  handle(IPC.toggleSkill, (skillId: string, enabled: boolean) => { toggleSkill(id.parse(skillId), z.boolean().parse(enabled)); return listSkills() })

  handle(IPC.imStatus, () => ({ connected: false, note: '微信渠道尚未连接。可以使用真实方案二维码分享。' }))
  handle(IPC.imLoginQr, () => ({ dataUrl: '', note: '尚未配置可用的微信渠道；没有可登录的二维码。' }))
  handle(IPC.proactiveList, async () => (await getHarness()).request('/api/v1/reminders'))
  handle('proactive:trigger', () => { throw new Error('提醒由真实计划或订阅事件触发。') })
  handle(IPC.memoryGet, () => memoryProfile())
  handle(IPC.memoryGreeting, async () => {
    const profile = await memoryProfile()
    return { text: profile.preferences.length ? `记得你的偏好：${profile.preferences.slice(0, 2).map(p => p.text).join('；')}。` : '' }
  })
  handle(IPC.memoryDelete, async (raw: unknown) => {
    const p = z.object({ kind: z.enum(['pref', 'fav']), value: text }).parse(raw)
    const path = p.kind === 'pref' ? 'preferences?text=' : 'favorites?name='
    await (await getHarness()).request('/api/v1/memory/' + path + encodeURIComponent(p.value), 'DELETE')
    return memoryProfile()
  })
  handle(IPC.memoryClear, async () => { await (await getHarness()).request('/api/v1/memory/profile?user_id=desktop', 'DELETE'); return memoryProfile() })

  handle(IPC.locationGet, () => getLocation())
  handle('location:detect', () => detectLocation(true))
  handle(IPC.locationSet, (city: string) => setManualCity(z.string().min(1).max(80).parse(city)))
  handle('location:report', (raw: unknown) => {
    const p = z.object({ city: z.string().min(1).max(80).optional(), coords: z.string().regex(/^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$/).optional() }).parse(raw)
    setConfig(p)
    return { city: getConfig().city, coords: getConfig().coords }
  })
  handle('amap:jsConfig', () => { const a = getConfig().amap; return { jsKey: a.jsKey, jsSecurity: a.jsSecurity, webKey: a.key } })
  handle('shell:openExternal', async (raw: unknown) => {
    const url = z.string().max(4000).parse(raw)
    const parsed = new URL(url)
    if (!['http:', 'https:', 'tel:', 'amapuri:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error('不支持的外部链接')
    await shell.openExternal(url)
    return true
  })
  handle(IPC.shareCreate, async (payload: { plan?: Plan; city?: string }) => {
    const requested = payload?.plan
    if (!requested?.run_id || !requested.version) return { ok: false, error: '请先从当前 Harness 任务选择一份方案。' }
    const run = await (await getHarness()).getRun(id.parse(requested.run_id))
    const cards = projectHarness(run).cards
    const plans = cards.flatMap(card => card.kind === 'plan' ? [card.plan] : card.kind === 'plans' ? card.variants.map(v => v.plan) : [])
    const plan = plans.find(p => p.plan_id === requested.plan_id && p.version === requested.version)
    if (!plan) return { ok: false, error: '方案版本已变化，请刷新后再分享。' }
    const { id: shareId, url } = createShare(plan, payload.city || getConfig().city)
    return { ok: true, id: shareId, url, qr: await QRCode.toDataURL(url, { width: 260, margin: 1 }) }
  })
  handle(IPC.shareFeedback, (shareId: string) => getShareFeedback(id.parse(shareId)))
  handle(IPC.guideSetImage, (raw: string) => { guideImage = image.parse(raw); return { ok: !!guideImage } })
  handle(IPC.discoverFetch, (city?: string) => computeLiveDiscover(city ? z.string().max(80).parse(city) : undefined))
  handle(IPC.dealsFetch, async (city?: string) => {
    const runs = await (await getHarness()).listRuns()
    const items: { poi: POISummary; deal: DealRow }[] = []
    for (const run of runs.slice(0, 20)) {
      for (const artifact of Array.isArray(run.state.browser_artifacts) ? run.state.browser_artifacts : []) {
        if (!artifact || typeof artifact !== 'object') continue
        const a = artifact as Record<string, any>
        const observed = Date.parse(a.observed_at || '')
        if (!Number.isFinite(observed) || Date.now() - observed > 10 * 60_000) continue
        for (const offer of Array.isArray(a.data?.offers) ? a.data.offers : []) {
          // Only compare actual same-offer prices. Missing list price is not a discount.
          if (typeof offer.price !== 'number' || typeof offer.original_price !== 'number' || offer.price < 0 || offer.original_price < offer.price) continue
          const title = String(a.title || offer.name || '网页套餐')
          const poi: POISummary = { poi_id: String(a.artifact_id), name: title, category: '团购', raw_score: null, trust: 'unknown', trust_reason: '', address: '', tags: [], enable_book: false, enable_reservation: false, business_hours: '', products: [], source: 'browser', recommended: [], is_distraction: false }
          const deal: DealRow = { shop: title, original: offer.original_price, final: offer.price, saved: offer.original_price - offer.price, used: [], reason: [offer.name, ...(offer.conditions || []), a.url].filter(Boolean).join(' · '), source: 'browser' }
          if (!items.some(item => item.poi.poi_id === poi.poi_id && item.deal.reason === deal.reason)) items.push({ poi, deal })
        }
      }
    }
    return { city: city || getConfig().city, items: items.slice(0, 20), source: 'browser' }
  })
}
