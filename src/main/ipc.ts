// Desktop UI invokes bounded APIs; remote web content has no Harness credentials.
import { ipcMain, shell, type IpcMainInvokeEvent } from 'electron'
import { z } from 'zod'
import QRCode from 'qrcode'
import { IPC } from '@shared/ipc'
import { errorDiagnostic } from '@shared/userMessages'
import { cancelBrowserTab, releaseBrowserRun } from './browser-bridge'
import { getMainWindow, isTrustedRendererUrl } from './index'
import { handleBrowserIntent, setBrowserLayout } from './browserView'
import { getHarness, harnessStatus, restartHarness } from './harness'
import { getConfig, getConfigMasked, getHarnessEnvironment, setConfig, safeServiceOrigin } from './config'
import { pingLlm } from './llm'
import { listSkills, toggleSkill } from './skills/loader'
import { detectLocation, getLocation, setManualCity, setReportedLocation } from './location'
import { locationSources, locationGranularities } from '../shared/location'
import { geocode, reverse } from './data/amap'
import { saveCarryOutIcs, saveCarryOutImage } from './carryOutRender'
import { createShare, getShareFeedback } from './share/server'
import { computeLiveDiscover } from './discover'
import { desktopInputHint } from './inputHint'
import { projectHarness } from '../renderer/src/lib/harnessProjection'
import type { DealRow, Plan, POISummary, UserProfile } from '@shared/types'

const id = z.string().min(1).max(512)
const offerSource = z.object({ command_id: id, artifact_id: id }).strict()
const text = z.string().trim().min(1).max(4000)
const image = z.string().max(12_000_000).regex(/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/).optional()
const selectedPoi = z.object({ poi_id: id, name: z.string().min(1).max(200), address: z.string().max(500), longitude: z.number().finite().min(-180).max(180),
  latitude: z.number().finite().min(-90).max(90), source: z.literal('amap'), observed_at: z.string().datetime({ offset: true }).optional() }).strict().optional()
function trusted(event: IpcMainInvokeEvent): void {
  const win = getMainWindow()
  if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame || !isTrustedRendererUrl(event.senderFrame.url) || !isTrustedRendererUrl(win.webContents.getURL())) throw new Error('Untrusted IPC sender')
}

function handle(channel: string, listener: (...args: any[]) => unknown): void {
  ipcMain.handle(channel, async (event, ...args) => {
    trusted(event)
    try { return await listener(...args) }
    catch (error) { console.warn('[plango] IPC request failed', channel, errorDiagnostic(error)); throw error }
  })
}

async function memoryProfile(): Promise<UserProfile> {
  const data = await (await getHarness()).request<Record<string, any>>('/api/v1/memory/profile?user_id=desktop')
  return {
    user_id: 'desktop', summary: (data.summaries || []).map((s: any) => typeof s === 'string' ? s : String(s.text || '')).filter(Boolean).join('；'),
    episodes: (data.summaries || []).filter((item: any) => item && typeof item.id === 'string').map((item: any) => ({ id: item.id, text: String(item.text || ''), scope: item.scope, createdAt: item.createdAt })),
    preferences: (data.preferences || []).map((p: any) => ({ text: String(p.text || p.value?.text || p.value || ''), polarity: ['negative', 'dislike'].includes(p.polarity) ? 'negative' : 'positive', strength: typeof p.strength === 'number' ? p.strength : p.confidence ?? 0.6, evidence_count: p.evidence_count ?? 1, source: 'conversation', explicit: p.explicit === true, provenance: String(p.source || '') })),
    favorite_shops: (data.favorites || []).map((p: any) => typeof p === 'string' ? p : String(p.name || p.value?.name || p.value || '')),
    favorite_provenance: Object.fromEntries((data.favorites || []).filter((p: any) => p && typeof p === 'object').map((p: any) => [String(p.name || p.value?.name || p.value || ''), { explicit: p.explicit === true, source: String(p.source || '') }])),
    home_city: getConfig().city
  }
}

async function resolveCanonicalPlan(requested?: Plan): Promise<{ ok: true; plan: Plan } | { ok: false; error: string }> {
  if (!requested?.run_id || requested.version == null) return { ok: false, error: '请先从当前任务选择一份方案。' }
  const run = await (await getHarness()).getRun(id.parse(requested.run_id))
  const cards = projectHarness(run).cards
  const plans = cards.flatMap(card => card.kind === 'plan' ? [card.plan] : card.kind === 'plans' ? card.variants.map(v => v.plan) : [])
  const plan = plans.find(item => item.plan_id === requested.plan_id && item.version === requested.version)
  return plan ? { ok: true, plan } : { ok: false, error: '方案版本已变化，请刷新后再试。' }
}

export function registerIpc(): void {
  handle('desktop:ready', () => {
    console.log('[plango] Desktop ready')
    return { inputHint: desktopInputHint() }
  })
  handle(IPC.browserRequest, async (raw: unknown) => {
    const intent = z.discriminatedUnion('kind', [
      z.object({ kind: z.literal('state') }).strict(), z.object({ kind: z.literal('create'), url: z.string().max(8192) }).strict(),
      z.object({ kind: z.literal('navigate'), id, url: z.string().max(8192) }).strict(),
      z.object({ kind: z.literal('zoom'), id, factor: z.number().finite().min(0.5).max(2.5) }).strict(),
      ...(['activate', 'close', 'back', 'forward', 'reload', 'focus'] as const).map(kind => z.object({ kind: z.literal(kind), id }).strict())
    ]).parse(raw)
    if ('id' in intent && !['activate', 'focus'].includes(intent.kind)) cancelBrowserTab(intent.id)
    return handleBrowserIntent(intent)
  })
  handle(IPC.browserLayout, (raw: unknown) => {
    const value = z.object({ x: z.number().finite().min(0).max(32000), y: z.number().finite().min(0).max(32000),
      width: z.number().finite().min(0).max(32000), height: z.number().finite().min(0).max(32000), visible: z.boolean() }).strict().parse(raw)
    setBrowserLayout(value)
  })
  handle(IPC.reminderRequest, async (operation: string, raw: unknown) => {
    const client = await getHarness()
    if (operation === 'list') return client.request('/api/v1/reminders')
    if (operation === 'create') return client.request('/api/v1/reminders', 'POST', z.object({ text, at: z.string().datetime({ offset: true }) }).parse(raw))
    if (operation === 'remove') return client.request(`/api/v1/reminders/${encodeURIComponent(z.object({ id }).parse(raw).id)}`, 'DELETE')
    throw new Error('Unknown reminder operation')
  })
  handle(IPC.harnessRequest, async (operation: string, raw: unknown) => {
    if (operation === 'status') return harnessStatus(z.object({ checkModel: z.boolean().optional() }).parse(raw || {}).checkModel)
    const client = await getHarness()
    switch (operation) {
      case 'merchantCandidates': {
        const p = z.object({ runId: id, sourceRef: offerSource }).strict().parse(raw)
        return client.merchantCandidates(p.runId, p.sourceRef)
      }
      case 'selectOffer': {
        const p = z.object({ runId: id, selection: z.object({ expected_version: z.number().int().min(1), source_ref: offerSource,
          offer_index: z.number().int().min(0).max(29), offer_hash: z.string().regex(/^[a-f0-9]{64}$/), poi_id: id, identity_confirmed: z.boolean().optional() }).strict() }).strict().parse(raw)
        return client.selectOffer(p.runId, p.selection)
      }
      case 'deliver': {
        const p = z.object({ requestId: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/), runId: id.optional(), text, image, selectedPoi }).strict().parse(raw)
        if (p.runId && p.selectedPoi) throw new Error('选店必须使用明确的任务入口')
        return client.deliver(p)
      }
      case 'checkDelivery': return client.checkDelivery(z.object({ requestId: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/) }).strict().parse(raw).requestId)
      case 'getRun': return client.getRun(z.object({ runId: id }).parse(raw).runId)
      case 'listRuns': return client.listRuns()
      case 'selectPlan': {
        const p = z.object({ runId: id, planId: id, planVersion: z.number().int().min(1) }).parse(raw)
        return client.selectPlan(p.runId, p.planId, p.planVersion)
      }
      case 'editRequirements': {
        const p = z.object({ runId: id, edit: z.object({
          expected_version: z.number().int().min(1),
          offer_source: offerSource.optional(),
          fields: z.object({
            location_name: z.string().trim().min(1).max(200).optional(), search_location_name: z.string().trim().min(1).max(200).optional(),
            max_distance_km: z.number().finite().min(0.1).max(50).nullable().optional(),
            search_radius_km: z.number().finite().min(0.1).max(50).nullable().optional(),
            route_distance_km: z.number().finite().min(0.1).max(1000).nullable().optional(),
            visit_date: z.string().date().nullable().optional(), time_window_start: z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d$/).nullable().optional(),
            party_size: z.number().int().min(1).max(12).optional(), budget: z.number().finite().min(0).max(1_000_000).nullable().optional(),
            per_person_budget: z.number().finite().min(0).max(1_000_000).nullable().optional(), travel_mode: z.enum(['driving', 'walking', 'transit']).optional()
          }).strict().optional(),
          stop_lock: z.object({ plan_id: id, plan_version: z.number().int().min(1), place_id: id, locked: z.boolean() }).strict().optional()
        }).strict() }).strict().parse(raw)
        return client.editRequirements(p.runId, p.edit)
      }
      case 'resumePreparation': {
        const p = z.object({ runId: id, planId: id, planVersion: z.number().int().min(1), approvalId: id }).strict().parse(raw)
        return client.resumePreparation(p.runId, p.planId, p.planVersion, p.approvalId)
      }
      case 'decideDraft': {
        const p = z.object({ runId: id, interruptId: id, planId: id, planVersion: z.number().int().min(1), decision: z.enum(['save', 'prepare']) }).strict().parse(raw)
        return client.decideDraft(p.runId, p.interruptId, p.planId, p.planVersion, p.decision)
      }
      case 'feedback': {
        const p = z.object({ runId: id, value: z.object({
          feedback_id: z.string().uuid(), turn_id: z.number().int().min(1), rating: z.enum(['helpful', 'unhelpful']),
          text: z.string().max(1000).optional(),
          preference: z.object({ text: z.string().trim().min(1).max(1000), polarity: z.enum(['positive', 'negative']) }).strict().optional()
        }).strict() }).strict().parse(raw)
        return client.feedback(p.runId, p.value)
      }
      case 'resolveAction': {
        const p = z.object({ runId: id, actionId: id, status: z.enum(['SUCCEEDED', 'FAILED']), note: z.string().trim().min(1).max(500), reference: z.string().max(200).optional() }).parse(raw)
        return client.resolveAction(p.runId, p.actionId, p.status, p.note, p.reference)
      }
      case 'cancel': {
        const p = z.object({ runId: id }).parse(raw)
        releaseBrowserRun(p.runId)
        return client.mutate(p.runId, 'cancel', {})
      }
      case 'releaseRun': {
        const p = z.object({ runId: id }).parse(raw)
        releaseBrowserRun(p.runId)
        return { ok: true }
      }
      case 'resume': {
        const p = z.object({ runId: id, interruptId: id, decision: z.enum(['approve', 'reject', 'edit', 'resume']), text: z.string().max(4000).optional() }).parse(raw)
        return client.resume(p.runId, p.interruptId, p.decision, p.text)
      }
      default: throw new Error('Unknown Harness operation')
    }
  })
  handle(IPC.getConfig, () => ({ config: { ...getConfigMasked(), harness: { baseURL: safeServiceOrigin(getHarnessEnvironment().PLANGO_BACKEND_URL || 'http://127.0.0.1:8011'), autoStart: getHarnessEnvironment().PLANGO_BACKEND_AUTOSTART !== 'false' } }, cities: [] }))
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

  handle(IPC.memoryGet, () => memoryProfile())
  handle(IPC.memoryGreeting, async () => {
    const profile = await memoryProfile()
    return { text: profile.preferences.length ? `记得你的偏好：${profile.preferences.slice(0, 2).map(p => p.text).join('；')}。` : '' }
  })
  handle(IPC.memoryDelete, async (raw: unknown) => {
    const p = z.object({ kind: z.enum(['pref', 'fav', 'episode']), value: text }).parse(raw)
    const path = p.kind === 'episode' ? 'episodes/' : p.kind === 'pref' ? 'preferences?text=' : 'favorites?name='
    await (await getHarness()).request('/api/v1/memory/' + path + encodeURIComponent(p.value), 'DELETE')
    return memoryProfile()
  })
  handle(IPC.memorySave, async (raw: unknown) => {
    const value = z.object({ text: z.string().trim().min(1).max(1000), polarity: z.enum(['like', 'dislike']) }).strict().parse(raw)
    await (await getHarness()).request('/api/v1/memory/preferences', 'POST', { ...value, user_id: 'desktop' })
    return memoryProfile()
  })
  handle(IPC.memoryClear, async () => { await (await getHarness()).request('/api/v1/memory/profile?user_id=desktop', 'DELETE'); return memoryProfile() })

  handle(IPC.locationGet, () => getLocation())
  handle('location:detect', () => detectLocation(true))
  handle(IPC.locationSet, (city: string) => setManualCity(z.string().min(1).max(80).parse(city)))
  handle('location:report', (raw: unknown) => {
    const p = z.object({ city: z.string().min(1).max(80), coords: z.string().regex(/^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$/).optional(),
      source: z.enum(locationSources), district: z.string().max(80).optional(), accuracy: z.number().finite().nonnegative().max(1_000_000).optional(), userInitiated: z.boolean().optional(),
      coordinate_system: z.literal('GCJ02').optional(), granularity: z.enum(locationGranularities).optional(), observed_at: z.string().datetime({ offset: true }).optional()
    }).parse(raw)
    if (p.coords) {
      const [lng, lat] = p.coords.split(',').map(Number)
      if (Math.abs(lng) > 180 || Math.abs(lat) > 90) throw new Error('坐标超出有效范围')
    }
    const { userInitiated, ...location } = p
    return setReportedLocation(location, userInitiated)
  })
  handle('geo:geocode', async (raw: unknown) => { const p = z.object({ address: z.string().trim().min(1).max(200), city: z.string().trim().min(1).max(100).optional() }).strict().parse(raw); return geocode(await getHarness(), p.address, p.city) })
  handle('geo:reverse', async (raw: unknown) => { const p = z.object({ longitude: z.number().finite().min(-180).max(180), latitude: z.number().finite().min(-90).max(90) }).strict().parse(raw); return reverse(await getHarness(), p.longitude, p.latitude) })
  handle('amap:jsConfig', () => { const a = getConfig().amap; return { jsKey: a.jsKey, jsSecurity: a.jsSecurity } })
  handle('shell:openExternal', async (raw: unknown) => {
    const url = z.string().max(4000).parse(raw)
    const parsed = new URL(url)
    if (!['http:', 'https:', 'tel:', 'amapuri:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error('不支持的外部链接')
    await shell.openExternal(url)
    return true
  })
  handle(IPC.shareCreate, async (payload: { plan?: Plan; city?: string }) => {
    const resolved = await resolveCanonicalPlan(payload?.plan)
    if (!resolved.ok) return resolved
    const { id: shareId, url } = createShare(resolved.plan, payload.city || getConfig().city)
    return { ok: true, id: shareId, url, qr: await QRCode.toDataURL(url, { width: 260, margin: 1 }) }
  })
  handle(IPC.shareFeedback, (shareId: string) => getShareFeedback(id.parse(shareId)))
  handle(IPC.carryOutSaveIcs, async (requested?: Plan) => {
    const resolved = await resolveCanonicalPlan(requested)
    if (!resolved.ok) return resolved
    return saveCarryOutIcs(resolved.plan)
  })
  handle(IPC.carryOutSaveImage, async (requested?: Plan) => {
    const resolved = await resolveCanonicalPlan(requested)
    if (!resolved.ok) return resolved
    return saveCarryOutImage(resolved.plan)
  })
  handle(IPC.discoverFetch, async (raw: unknown) => {
    const p = z.object({ city: z.string().trim().min(1).max(100).optional(), refresh: z.boolean().optional() }).strict().parse(raw || {})
    const current = getLocation()
    const location = p.city && p.city.replace(/市$/, '') !== current.city.replace(/市$/, '') ? { city: p.city, source: 'manual' as const, granularity: 'city' as const } : current
    return computeLiveDiscover(await getHarness(), location, p.refresh)
  })
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
