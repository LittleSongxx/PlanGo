import assert from 'node:assert/strict'
import type { HarnessDeliveryRequest, HarnessDeliveryResult, HarnessSnapshot } from '../src/shared/types'

// Controlled IPC failures only; this never starts a backend or opens a real profile.
const cache = new Map<string, string>()
const images = new Map<string, Blob>()
Object.defineProperty(globalThis, 'indexedDB', { value: { open: () => {
  const database = { createObjectStore: () => {}, transaction: () => {
    const transaction: any = { objectStore: () => ({
      put: (blob: Blob, id: string) => { images.set(id, blob); queueMicrotask(() => transaction.oncomplete?.()) },
      get: (id: string) => { const request: any = { result: images.get(id) }; queueMicrotask(() => request.onsuccess?.()); return request }
    }) }; return transaction
  } }
  const request: any = { result: database }
  queueMicrotask(() => { request.onupgradeneeded?.(); request.onsuccess?.() })
  return request
} } })
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
let full = false
Object.defineProperty(globalThis, 'localStorage', { value: {
  getItem: (key: string) => cache.get(key) ?? null,
  setItem: (key: string, value: string) => { if (full) throw new Error('controlled quota'); cache.set(key, value) },
  removeItem: (key: string) => cache.delete(key)
} })
const requests: HarnessDeliveryRequest[] = [], checks: string[] = []
const snapshot = (runId: string, text = '受控请求'): HarnessSnapshot => ({ run_id: runId, input_text: text, phase: 'SUCCEEDED', outcome: 'SUCCEEDED', event_seq: 1, version: 1, state: {} })
let post: (request: HarnessDeliveryRequest) => Promise<HarnessDeliveryResult> = async request => ({ requestId: request.requestId, status: 'delivered', runId: request.runId || 'controlled-run', snapshot: snapshot(request.runId || 'controlled-run', request.text) })
let lookup: (requestId: string) => Promise<HarnessDeliveryResult> = async requestId => ({ requestId, status: 'not_sent' })
let fetchRun = async (runId: string) => snapshot(runId)
const api = {
  deliver: async (request: HarnessDeliveryRequest) => { requests.push(structuredClone(request)); return post(request) },
  checkDelivery: async (requestId: string) => { checks.push(requestId); return lookup(requestId) },
  status: async () => ({ ready: true }), listRuns: async () => [], getRun: async (runId: string) => fetchRun(runId)
}
Object.defineProperty(globalThis, 'window', { value: { plango: { harness: api } } })
const { useStore } = await import('../src/renderer/src/store')
const state = () => useStore.getState()
function reset(id: string): void {
  cache.clear(); requests.length = 0; checks.length = 0
  useStore.setState({ activeSessionId: id, drafts: {}, pendingDelivery: null, sessions: [], messages: [], cards: [], run: null, busy: false, requestBusy: false, deliveryBusy: false, backendReady: true, backendError: '', storageError: '' })
}

reset('offline')
state().setDraft({ text: '断线时的草稿' })
useStore.setState({ backendReady: false })
await state().send('断线时的草稿')
const offlineId = state().pendingDelivery!.request.requestId
assert.equal(requests.length, 0)
assert.equal(state().pendingDelivery?.status, 'not_sent')
assert.equal(state().drafts.offline.text, '断线时的草稿')
assert.equal(state().messages.length, 0, 'Unaccepted requests must not appear as delivered user bubbles')
assert.equal(JSON.parse(cache.get('plango_composer')!).pendingDelivery.request.requestId, offlineId)
await state().hydrateHarness()
assert.deepEqual(checks, [offlineId])
assert.equal(requests.length, 0, 'Reconnect never automatically sends an offline draft')

let finish!: (result: HarnessDeliveryResult) => void
post = () => new Promise(resolve => { finish = resolve })
const sending = state().recoverDelivery(true)
await tick()
await state().recoverDelivery(true)
await state().send('断线时的草稿')
assert.equal(requests.length, 1, 'Double clicks share the one unresolved identity')
state().setDraft({ text: '发送过程中继续编辑的新草稿' })
finish({ requestId: offlineId, status: 'unconfirmed', error: 'controlled POST response lost' })
await sending
assert.equal(state().pendingDelivery?.status, 'unconfirmed')
lookup = async requestId => ({ requestId, status: 'accepted', runId: 'accepted-run', error: 'controlled snapshot GET failure' })
await state().recoverDelivery()
assert.equal(state().pendingDelivery?.status, 'accepted')
assert.equal(state().pendingDelivery?.request.runId, undefined, 'A create request must never become a message request after acceptance')
assert.equal(state().pendingDelivery?.acceptedRunId, 'accepted-run')
assert.equal(state().sessions.find(s => s.id === 'offline')?.runId, 'accepted-run')
lookup = async requestId => ({ requestId, status: 'delivered', runId: 'accepted-run', snapshot: snapshot('accepted-run') })
await state().recoverDelivery(true)
assert.equal(requests.length, 1, 'An accepted message only retrieves the original run')
assert.equal(state().run?.run_id, 'accepted-run')
assert.equal(state().pendingDelivery, null)
assert.equal(state().drafts.offline.text, '发送过程中继续编辑的新草稿', 'Delivery clears only the exact submitted draft revision')

post = async request => ({ requestId: request.requestId, status: 'delivered', runId: request.runId || 'controlled-run', snapshot: snapshot(request.runId || 'controlled-run', request.text) })
state().setDraft({ text: '断线时的草稿' })
await state().send('断线时的草稿')
assert.notEqual(requests[1].requestId, offlineId, 'Identical text in a later explicit turn gets a fresh identity')
assert.equal(requests[1].runId, 'accepted-run')
assert.equal(state().drafts.offline.text, '')
assert.equal(state().pendingDelivery, null)

reset('old-session')
state().setDraft({ text: '旧会话请求' })
post = () => new Promise(resolve => { finish = resolve })
const older = state().send('旧会话请求')
await tick()
const originalId = state().pendingDelivery!.request.requestId
state().newSession()
const newerSession = state().activeSessionId
state().setDraft({ text: '新会话草稿' })
await state().send('新会话草稿')
assert.equal(requests.length, 1)
assert.equal(state().pendingDelivery?.sessionId, 'old-session')
finish({ requestId: originalId, status: 'delivered', runId: 'old-run', snapshot: snapshot('old-run') })
await older
assert.equal(state().run, null, 'An older delivery cannot activate a different current session')
assert.equal(state().activeSessionId, newerSession)
assert.equal(state().drafts[newerSession].text, '新会话草稿')
assert.equal(state().sessions.find(s => s.id === 'old-session')?.runId, 'old-run')
state().restoreSession('old-session')
await new Promise(resolve => setTimeout(resolve, 0))
assert.equal(state().run?.run_id, 'old-run')

reset('image-poi')
const image = 'data:image/png;base64,aGVsbG8='
const selectedPoi = { poi_id: 'controlled-poi', name: '受控同名门店 A', address: '受控路 1 号', longitude: 106.5, latitude: 29.5, source: 'amap' as const }
state().setDraft({ text: '选店前的草稿' })
post = async () => { throw new Error('controlled IPC acknowledgement loss') }
await state().send('选定这家门店', image, selectedPoi)
const selectedSession = state().activeSessionId, selectedRequest = structuredClone(state().pendingDelivery!.request)
assert.notEqual(selectedSession, 'image-poi')
assert.equal(state().drafts['image-poi'].text, '选店前的草稿')
assert.deepEqual(selectedRequest.selectedPoi, selectedPoi)
assert.equal(selectedRequest.image, image)
assert.equal(selectedRequest.runId, undefined)
assert.equal(state().pendingDelivery?.status, 'unconfirmed')
state().deleteSession(selectedSession)
assert(state().sessions.some(s => s.id === selectedSession), 'An unresolved delivery cannot be hidden away')
// Reload the renderer module from durable localStorage, with no shared in-memory pending state.
const restartModule = '../src/renderer/src/store.ts?delivery-restart'
const { useStore: reloaded } = await import(restartModule)
assert.notEqual(reloaded, useStore)
assert.equal(reloaded.getState().activeSessionId, selectedSession)
await reloaded.getState().hydrateComposer()
assert.deepEqual(JSON.parse(JSON.stringify(reloaded.getState().pendingDelivery?.request)), JSON.parse(JSON.stringify(selectedRequest)))
lookup = async requestId => ({ requestId, status: 'unconfirmed', error: 'controlled temporarily unreachable' })
await reloaded.getState().hydrateHarness()
assert.equal(requests.length, 1, 'Desktop restart checks the original receipt without resending')
assert.deepEqual(checks, [selectedRequest.requestId])
post = async request => ({ requestId: request.requestId, status: 'delivered', runId: 'selected-run', snapshot: snapshot('selected-run') })
await reloaded.getState().recoverDelivery(true)
assert.equal(requests.length, 2)
assert.deepEqual(JSON.parse(JSON.stringify(requests[1])), JSON.parse(JSON.stringify(selectedRequest)), 'Explicit retry retains the complete image/POI/original target payload')
assert.equal(reloaded.getState().run?.run_id, 'selected-run')
assert.equal(reloaded.getState().pendingDelivery, null)

reset('quota')
full = true
state().setDraft({ text: '磁盘满时保留', image })
await state().send('磁盘满时保留', image)
assert.match(state().storageError, /尚未保存/)
assert.equal(state().drafts.quota.image, image)
assert.equal(state().pendingDelivery?.status, 'not_sent')
assert.equal(requests.length, 0, 'A failed identity save cannot cross IPC')
full = false
await state().recoverDelivery(true)
assert.equal(requests.length, 1)
assert.equal(state().pendingDelivery, null)
assert.equal(state().drafts.quota.text, '')
assert.equal(state().drafts.quota.image, undefined)

reset('canonical-restore')
useStore.setState({ sessions: [{ id: 'saved-session', runId: 'existing-run', title: 'Existing', city: '', createdAt: 1, updatedAt: 1, messages: [], cards: [] }] })
fetchRun = async () => { throw new Error('controlled GET failure') }
state().restoreSession('saved-session')
await new Promise(resolve => setTimeout(resolve, 0))
assert.equal(state().run, null)
useStore.setState({ backendReady: true })
await state().send('修改原任务')
assert.equal(requests[0].runId, 'existing-run', 'A failed history GET must never create a second backend run')

reset('cancel-not-sent')
useStore.setState({ backendReady: false })
state().setDraft({ text: '未送达可以修改' })
await state().send('未送达可以修改')
const cancelledId = state().pendingDelivery!.request.requestId
await state().discardPendingDelivery()
assert.equal(state().pendingDelivery, null)
assert.equal(state().drafts['cancel-not-sent'].text, '未送达可以修改')
state().setDraft({ text: '修改后的新输入' })
await state().send('修改后的新输入')
assert.notEqual(state().pendingDelivery?.request.requestId, cancelledId)
useStore.setState({ pendingDelivery: { ...state().pendingDelivery!, status: 'unconfirmed' } })
await state().discardPendingDelivery()
assert.equal(state().pendingDelivery?.status, 'unconfirmed', 'An uncertain delivery cannot be discarded and relabeled as a new request')

reset('target-identity')
useStore.setState({ run: snapshot('original-run') })
post = async request => ({ requestId: request.requestId, status: 'delivered', runId: 'wrong-run', snapshot: snapshot('wrong-run') })
state().setDraft({ text: '原任务修改' })
await state().send('原任务修改')
assert.equal(state().pendingDelivery?.status, 'unconfirmed')
assert.equal(state().run?.run_id, 'original-run')
assert.equal(state().drafts['target-identity'].text, '原任务修改')
assert.match(state().pendingDelivery!.error!, /原任务不一致/)

reset('attachment-missing')
state().setDraft({ text: '缺失附件', image })
useStore.setState({ backendReady: false })
await state().send('缺失附件', image)
const metadata = cache.get('plango_composer')!
assert(!metadata.includes(image))
images.clear()
const missingModule = '../src/renderer/src/store.ts?missing-attachment'
const { useStore: missing } = await import(missingModule)
await missing.getState().hydrateHarness()
await missing.getState().send('不能无图发送')
await missing.getState().recoverDelivery(true)
assert.equal(requests.length, 0)
assert.equal(missing.getState().composerReady, false)
assert.match(missing.getState().storageError, /原图片尚未恢复/)
assert(cache.get('plango_composer')!.includes(JSON.parse(metadata).pendingDelivery.imageRef), 'A missing attachment retains its reference')

const corrupt = '{"pendingDelivery":broken'
cache.set('plango_composer', corrupt)
const corruptModule = '../src/renderer/src/store.ts?corrupt-composer'
const { useStore: corruptStore } = await import(corruptModule)
await corruptStore.getState().hydrateHarness()
corruptStore.getState().setDraft({ text: '不能覆盖原记录' })
await corruptStore.getState().persistComposer()
await corruptStore.getState().send('不能覆盖原记录')
assert.equal(cache.get('plango_composer'), corrupt)
assert.equal(requests.length, 0)
assert.equal(corruptStore.getState().composerReady, false)
assert.match(corruptStore.getState().storageError, /保留原记录/)

console.log(JSON.stringify({ scope: 'controlled renderer delivery recovery', checks: ['offline persisted draft', 'reconnect read-only', 'double click', 'lost acknowledgement', 'accepted GET failure', 'new draft preserved', 'same text new turn', 'cross-session completion', 'image and selected POI restart', 'same identity retry', 'visible quota failure', 'canonical restore identity', 'not-sent cancellation', 'receipt target guard', 'missing image retains reference', 'corrupt metadata blocks overwrite'], real_business_actions: 0 }))
