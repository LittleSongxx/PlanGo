import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtempSync, rmSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { HarnessClient } from '../src/main/harnessClient'

// Controlled HTTP failures: no model, merchant data or browser execution.
const root = mkdtempSync(join(tmpdir(), 'plango-delivery-'))
let mode = 'before', posts = 0, serial = 0, lookupDown = false
const accepted = new Map<string, { run_id: string; request_id: string; request_fingerprint: string; accepted: true }>()
const bodies: unknown[] = []
const server = createServer(async (req, res) => {
  let raw = ''; for await (const chunk of req) raw += chunk
  const path = req.url || ''
  res.setHeader('Content-Type', 'application/json')
  const send = (value: unknown) => res.end(JSON.stringify(value))
  if (path === '/api/v1/health/ready') return send({ ready: true, input_delivery_version: mode === 'legacy' ? undefined : 1 })
  if (req.method === 'POST') {
    posts++
    const body = JSON.parse(raw); bodies.push(body)
    if (mode === 'conflict') { res.statusCode = 409; return send({ detail: 'request_id_conflicts_with_saved_content' }) }
    if (mode === 'lost-conflict') { req.socket.destroy(); return }
    if (mode === 'before') { req.socket.destroy(); return }
    const key = body.request_id || `legacy-${++serial}`
    if (!accepted.has(key)) accepted.set(key, { run_id: path.includes('/messages') ? 'existing-run' : `run-${accepted.size + 1}`, request_id: key, request_fingerprint: body.request_fingerprint, accepted: true })
    if (mode === 'response') { req.socket.destroy(); return }
    return send(accepted.get(key))
  }
  if (path.startsWith('/api/v1/requests/')) {
    if (lookupDown) { res.statusCode = 503; return send({ detail: 'lookup unavailable' }) }
    const row = accepted.get(path.split('/').at(-1)!)
    if (row) return send(row)
    res.statusCode = 404; return send({ detail: 'not found' })
  }
  if (path.startsWith('/api/v1/runs/')) {
    if (mode === 'snapshot') { res.statusCode = 503; return send({ detail: 'snapshot unavailable' }) }
    return send({ run_id: path.split('/').at(-1), input_text: 'controlled fixture', phase: 'SUCCEEDED', event_seq: 0, version: 1, state: {} })
  }
  res.statusCode = 404; send({ detail: 'not found' })
})
await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
const options = { baseURL: `http://127.0.0.1:${(server.address() as { port: number }).port}`, token: 'fixture-only',
  browserSessionId: 'fixture-session', dataDir: root, emit() {}, execute: async () => { throw new Error('No browser execution') } }
let client = new HarnessClient(options)
try {
  const first = { requestId: 'before-send', text: '同样文字' }
  assert.equal((await client.deliver(first)).status, 'unconfirmed')
  assert.equal(accepted.size, 0)
  mode = 'ok'
  const [a, b] = await Promise.all([client.deliver(first), client.deliver(first)])
  assert.equal(a.status, 'delivered'); assert.equal(a.runId, b.runId)
  assert.equal(accepted.size, 1); assert.equal(posts, 2, 'Concurrent clicks reuse one in-flight POST')
  const count = posts
  await client.deliver(first); assert.equal(posts, count, 'Completed retry is read-only')
  await assert.rejects(client.deliver({ ...first, text: 'conflict' }), /身份/)
  assert.equal(posts, count, 'Conflicting payload never reaches the network')

  mode = 'response'; lookupDown = true
  const attachment = { requestId: 'image-choice', text: '同样文字', image: 'data:image/png;base64,AAAA',
    selectedPoi: { poi_id: 'fixture-poi', name: '受控门店', address: '受控地址', longitude: 106.57, latitude: 29.56, source: 'amap' as const } }
  assert.equal((await client.deliver(attachment)).status, 'unconfirmed')
  assert.equal(accepted.size, 2, 'A separate turn with identical text has a separate identity')
  const saved = new Map<string, any>(JSON.parse(readFileSync(join(root, 'input-deliveries.json'), 'utf8')))
  assert.equal(saved.get(attachment.requestId).body.image, attachment.image)
  assert.deepEqual(saved.get(attachment.requestId).body.selected_poi, attachment.selectedPoi)
  await client.close(); lookupDown = false; mode = 'ok'
  client = new HarnessClient({ ...options, location: () => ({ city: '成都' } as any) })
  const beforeRecovery = posts
  assert.equal((await client.checkDelivery(attachment.requestId)).status, 'delivered')
  assert.equal(posts, beforeRecovery, 'Restart retrieves the accepted run without a POST')

  mode = 'snapshot'
  const followup = { requestId: 'message-get-failure', runId: 'existing-run', text: '修改人数为3' }
  const pending = await client.deliver(followup)
  assert.equal(pending.status, 'accepted'); assert.equal(pending.runId, 'existing-run')
  const beforeSnapshot = posts
  await client.close(); client = new HarnessClient(options); mode = 'ok'
  assert.equal((await client.deliver(followup)).status, 'delivered')
  assert.equal(posts, beforeSnapshot, 'Accepted message only retrieves the original run')
  assert.equal((await client.checkDelivery('never-sent')).status, 'not_sent')
  mode = 'legacy'
  const legacyPosts = posts
  assert.equal((await client.deliver({ requestId: 'old-api', text: '保留草稿' })).status, 'not_sent')
  assert.equal(posts, legacyPosts, 'Legacy services must not receive a POST with an ignored identity')
  mode = 'conflict'
  const conflict = { requestId: 'server-conflict', text: '服务未接受的新内容' }
  accepted.set(conflict.requestId, { request_id: conflict.requestId, request_fingerprint: 'old-content', accepted: true, run_id: 'unrelated-content' })
  assert.equal((await client.deliver(conflict)).status, 'not_sent')
  assert.equal((await client.checkDelivery(conflict.requestId)).status, 'not_sent', 'An ID conflict cannot confirm different content as delivered')
  mode = 'lost-conflict'
  const lostConflict = { requestId: 'lost-conflict-id', text: '409丢失也不能清除的新草稿' }
  accepted.set(lostConflict.requestId, { request_id: lostConflict.requestId, request_fingerprint: 'old-content', accepted: true, run_id: 'unrelated-content' })
  assert.equal((await client.deliver(lostConflict)).status, 'unconfirmed')
  assert.equal((await client.checkDelivery(lostConflict.requestId)).status, 'unconfirmed', 'Lost conflict response still cannot confirm another payload')

  // An unaccepted retry keeps the first location and skill snapshot after restart.
  mode = 'before'
  client = await (async () => { await client.close(); return new HarnessClient({ ...options, location: () => ({ city: '重庆' } as any), enabledSkills: () => ['original'] }) })()
  const frozen = { requestId: 'fixed-context', text: '固定上下文' }
  await client.deliver(frozen)
  const original = bodies.at(-1)
  await client.close(); mode = 'ok'
  client = new HarnessClient({ ...options, location: () => ({ city: '成都' } as any), enabledSkills: () => ['changed'] })
  assert.equal((await client.deliver(frozen)).status, 'delivered')
  assert.deepEqual(bodies.at(-1), original)
  await client.close()
  client = new HarnessClient({ ...options, baseURL: options.baseURL + '/different-service' })
  assert.equal((await client.checkDelivery(frozen.requestId)).status, 'unconfirmed', 'Cannot recover into another service')
  console.log(JSON.stringify({ controlled: true, passed: true, cases: ['before acceptance', 'response lost', 'snapshot failed', 'duplicate click', 'same text new identity', 'image and selected POI', 'process restart', 'frozen payload', 'service mismatch'], accepted_inputs: accepted.size, posts }))
} finally {
  await client.close(); server.closeAllConnections()
  await new Promise<void>(resolve => server.close(() => resolve()))
  rmSync(root, { recursive: true, force: true })
}
