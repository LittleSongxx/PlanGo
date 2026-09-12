import assert from 'node:assert/strict'
import { projectEvents } from '../src/renderer/src/lib/harnessProjection'
import { allowsGeolocation, allowsHostClipboard, allowsSessionPermission } from '../src/main/permissions'
import { locationLabel } from '../src/shared/location'
import { isImeComposing } from '../src/shared/ime'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const failures: string[] = []
function check(label: string, fn: () => void): void { try { fn() } catch { failures.push(label) } }
const event = (event_type: string, payload = {}, phase = 'RESEARCHING') => projectEvents([{ run_id: 'fixture', seq: 1, event_type, phase, payload }])[0].status
check('lowercase connection error is an error', () => assert.equal(event('connection_error'), 'error'))
check('approval waiting is not successful', () => assert.equal(event('WAITING_APPROVAL'), 'waiting'))
check('browser waiting is not successful', () => assert.equal(event('browser_waiting'), 'waiting'))
check('started work remains running', () => assert.equal(event('advocate_fanout_started'), 'running'))
check('unknown browser result remains unresolved', () => assert.equal(event('BROWSER_OBSERVATION', { outcome: 'unknown' }), 'waiting'))
check('blocked browser result is an error', () => assert.equal(event('BROWSER_OBSERVATION', { outcome: 'blocked' }), 'error'))
check('successful observation retains its technical success', () => assert.equal(event('BROWSER_OBSERVATION', { outcome: 'observed' }), 'done'))
check('unlabeled in-progress work is running, not an empty marker', () => assert.equal(event('new_event'), 'running'))
check('successful finalized run remains successful', () => assert.equal(event('run_finalized', { phase: 'SUCCEEDED' }), 'done'))
check('interrupted graph in a waiting phase is waiting', () => assert.equal(event('RUN_GRAPH_INTERRUPTED', {}, 'WAITING_APPROVAL'), 'waiting'))
check('browser interrupt is waiting without a waiting phase', () => assert.equal(event('RUN_GRAPH_INTERRUPTED', { type: 'browser' }, 'REQUIREMENTS_READY'), 'waiting'))
check('successful observation is done even when its run is waiting', () => assert.equal(event('BROWSER_OBSERVATION', { outcome: 'observed' }, 'WAITING_APPROVAL'), 'done'))
check('requirements phase alone cannot declare an event successful', () => assert.equal(event('new_event', {}, 'REQUIREMENTS_READY'), 'running'))
check('connection errors preserve the actual reason', () => assert.equal(projectEvents([{ run_id: 'fixture', seq: 2, event_type: 'connection_error', phase: 'RESEARCHING', payload: { message: 'fixture connection refused' } }])[0].detail, 'fixture connection refused'))
const progress = projectEvents([
  { run_id: 'fixture', seq: 1, event_type: 'RUN_CREATED', phase: 'CREATED', payload: {} },
  { run_id: 'fixture', seq: 2, event_type: 'progress', phase: 'CREATED', payload: {} },
  { run_id: 'fixture', seq: 3, event_type: 'MEMORY_RETRIEVED', phase: 'CREATED', payload: {} },
  { run_id: 'fixture', seq: 4, event_type: 'progress', phase: 'RESEARCHING', payload: {} },
  { run_id: 'fixture', seq: 5, event_type: 'REQUIREMENTS_READY', phase: 'REQUIREMENTS_READY', payload: {} },
  { run_id: 'fixture', seq: 6, event_type: 'SUPERVISOR_DECISION', phase: 'REQUIREMENTS_READY', payload: {} },
  { run_id: 'fixture', seq: 7, event_type: 'CLARIFICATION_REQUESTED', phase: 'REQUIREMENTS_READY', payload: {} },
  { run_id: 'fixture', seq: 8, event_type: 'GRAPH_INTERRUPTED', phase: 'REQUIREMENTS_READY', payload: {} },
  { run_id: 'fixture', seq: 9, event_type: 'REQUIREMENTS_EDITED', phase: 'REQUIREMENTS_READY', payload: { reason: '修改行程需求：人数：1' } },
  { run_id: 'fixture', seq: 10, event_type: 'progress', phase: 'REPLANNING', payload: {} },
  { run_id: 'fixture', seq: 11, event_type: 'DISCOVERY_COMPLETE', phase: 'RESEARCHING', payload: {} },
  { run_id: 'fixture', seq: 12, event_type: 'SUPERVISOR_DECISION', phase: 'RESEARCHING', payload: {} },
  { run_id: 'fixture', seq: 13, event_type: 'PLAN_SYNTHESIZED', phase: 'PLAN_DRAFTED', payload: {} },
  { run_id: 'fixture', seq: 14, event_type: 'PLAN_VERIFIED', phase: 'REVIEWING', payload: {} },
  { run_id: 'fixture', seq: 15, event_type: 'GRAPH_INTERRUPTED', phase: 'REQUIREMENTS_READY', payload: {} },
  { run_id: 'fixture', seq: 16, event_type: 'progress', phase: 'REQUIREMENTS_READY', payload: {} }
])
check('raw event ticks collapse into phase progress', () => assert.equal(progress.length, 7))
check('past waits are completed once later work exists', () => assert(progress.slice(0, -1).every(step => step.status === 'done')))
check('current wait keeps a waiting marker', () => assert.equal(progress.at(-1)?.status, 'waiting'))
check('current wait names the user action', () => assert.equal(progress.at(-1)?.label, '等待你的下一步'))
check('plan synthesis is a completed phase, not an empty circle', () => assert.equal(progress.find(step => step.label === '方案已生成')?.status, 'done'))
check('requirement edits keep the user-visible change', () => assert.equal(progress.find(step => step.label === '行程需求已更新')?.detail, '修改行程需求：人数：1'))
check('discovery is not overwritten by routing ticks', () => assert.equal(progress.find(step => step.label === '查找资料' && step.detail === '地点资料已查到')?.status, 'done'))

const values = new Map<string, string>()
Object.defineProperty(globalThis, 'localStorage', { value: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) } })
Object.defineProperty(globalThis, 'window', { value: { plango: { geo: { geocode: async () => ({ source: 'amap', location: { longitude: 106.57, latitude: 29.56, city: '重庆市', district: '渝中区', granularity: 'address' }, observed_at: '2026-09-09T00:00:00Z', expires_at: '2030-01-01T00:00:00Z' }) } } } })
const { geocodeAddress } = await import('../src/renderer/src/lib/amap')
const address = await geocodeAddress('重庆解放碑', '重庆')
check('manual address is not a GPS observation', () => assert.equal(address?.source, 'address'))
check('manual address has no invented meter accuracy', () => assert.equal(address?.accuracy, undefined))
const { useStore } = await import('../src/renderer/src/store')
useStore.setState({ city: '上海', coords: '121.47,31.23', district: '黄浦区', locAccuracy: 15, citySource: 'gps' })
useStore.getState().setLocationInfo({ city: '重庆', source: 'manual' })
check('city change drops stale coordinates', () => assert.equal(useStore.getState().coords, ''))
check('city change drops stale district and accuracy', () => assert.deepEqual([useStore.getState().district, useStore.getState().locAccuracy], ['', 0]))
check('manual address label never claims GPS', () => assert(!locationLabel('address', 30).includes('GPS') && !locationLabel('address', 30).includes('30')))
const host = { permission: 'geolocation', isMainFrame: true, requestingUrl: 'file:///fixture/index.html', pageUrl: 'file:///fixture/index.html', hostUrl: 'file:///fixture/index.html', isHost: true, isBrowser: false }
check('owned desktop can request host geolocation', () => assert(allowsGeolocation(host)))
check('host permission excludes subframes', () => assert(!allowsGeolocation({ ...host, isMainFrame: false })))
check('host permission excludes navigated external documents', () => assert(!allowsGeolocation({ ...host, requestingUrl: 'https://meituan.com/', pageUrl: 'https://meituan.com/' })))
check('host permission excludes unowned windows', () => assert(!allowsGeolocation({ ...host, isHost: false })))
check('host geolocation does not grant other permissions', () => assert(!allowsGeolocation({ ...host, permission: 'media' })))
check('owned desktop can write sanitized clipboard', () => assert(allowsHostClipboard({ ...host, permission: 'clipboard-sanitized-write' })))
check('host clipboard write does not grant clipboard read', () => assert(!allowsHostClipboard({ ...host, permission: 'clipboard-read' })))
check('host clipboard excludes subframes', () => assert(!allowsHostClipboard({ ...host, permission: 'clipboard-sanitized-write', isMainFrame: false })))
check('guest pages cannot write clipboard', () => assert(!allowsHostClipboard({ ...host, permission: 'clipboard-sanitized-write', isHost: false })))
check('session permission unions host clipboard and geolocation', () => {
  assert(allowsSessionPermission(host))
  assert(allowsSessionPermission({ ...host, permission: 'clipboard-sanitized-write' }))
  assert(!allowsSessionPermission({ ...host, permission: 'media' }))
})
const merchant = { ...host, isHost: false, isBrowser: true, requestingUrl: 'https://www.meituan.com/a', pageUrl: 'https://www.meituan.com/a' }
check('known merchant partition retains its permission', () => assert(allowsGeolocation(merchant)))
check('merchant permission excludes unrelated embedded origin', () => assert(!allowsGeolocation({ ...merchant, requestingUrl: 'https://evil.test' })))
check('merchant permission excludes suffix tricks', () => assert(!allowsGeolocation({ ...merchant, pageUrl: 'https://www.meituan.com.evil.test/', requestingUrl: 'https://www.meituan.com.evil.test/' })))
check('IME composition must not be treated as a submit key', () => {
  assert.equal(isImeComposing({ isComposing: true, keyCode: 13 }), true)
  assert.equal(isImeComposing({ keyCode: 229 }), true)
  assert.equal(isImeComposing({ nativeEvent: { isComposing: true } }), true)
  assert.equal(isImeComposing({ keyCode: 13 }), false)
})
const cwd = process.cwd(), temporary = mkdtempSync(join(tmpdir(), 'plango-location-check-'))
try {
  process.chdir(temporary)
  const { getLocation, setReportedLocation, setManualCity } = await import('../src/main/location')
  setReportedLocation({ city: '重庆', coords: '106.57,29.56', district: '渝中区', source: 'address' }, true)
  check('main process preserves address provenance', () => assert.equal(getLocation().source, 'address'))
  check('persisted provenance survives restart serialization', () => assert.equal(JSON.parse(readFileSync('.plango-config.json', 'utf8')).location.source, 'address'))
  setReportedLocation({ city: '上海', coords: '121.4,31.2', source: 'amap-ip' })
  check('late automatic IP cannot replace a chosen address', () => assert.equal(getLocation().city, '重庆'))
  setManualCity('成都')
  check('manual city clears main-process coordinates', () => assert.equal(getLocation().coords, undefined))
} finally { process.chdir(cwd); rmSync(temporary, { recursive: true, force: true }) }
console.log(JSON.stringify({ scope: 'controlled desktop correctness fixtures', failures }))
assert.deepEqual(failures, [])
