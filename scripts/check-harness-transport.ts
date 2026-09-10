import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { HarnessClient } from '../src/main/harnessClient'
import type { BrowserCommand, BrowserObservation } from '../src/shared/browser'

const root = mkdtempSync(join(tmpdir(), 'plango-transport-'))
const received: BrowserObservation[] = []
const events: unknown[] = []
let commands: BrowserCommand[] = []
let failures = 1
let missingAcknowledgements = 1
let executions = 0
let releases = 0
let activations = 0
let receiptAcknowledgements = 0
let state = { run_id: 'run-test', input_text: 'fixture', phase: 'RESEARCHING', event_seq: 1, version: 1, state: {} }
let selected: unknown
const token = 'transport-fixture-only'
let streamConnections = 0, streamReleases = 0
let pausedConnections = 0
const streamCursors: string[] = []
let streamState = { run_id: 'run-stream', input_text: 'fixture', phase: 'RESEARCHING', event_seq: 0, version: 1, state: {} }
const streamEvents: any[] = []
const server = createServer(async (req, res) => {
  assert.equal(req.headers.authorization, `Bearer ${token}`)
  const url = new URL(req.url || '/', 'http://localhost')
  let body = ''
  for await (const part of req) body += part
  res.setHeader('Content-Type', 'application/json')
  const send = (value: unknown) => res.end(JSON.stringify(value))
  if (url.pathname === '/api/v1/runs/run-batch') return send({ run_id: 'run-batch', input_text: 'fixture', phase: 'SUCCEEDED', event_seq: 3, version: 1, state: {} })
  if (url.pathname === '/api/v1/runs/run-batch/events') return send({ events: (Number(url.searchParams.get('after')) === 0 ? [1, 3] : [2, 3]).map(seq => ({ run_id: 'run-batch', seq, event_type: 'fixture', payload: {} })) })
  if (url.pathname === '/api/v1/runs/run-paused') return send({ run_id: 'run-paused', input_text: 'fixture', phase: 'REQUIREMENTS_READY', event_seq: 0, version: 1, interrupt_id: 'clarification:fixture', state: {} })
  if (url.pathname === '/api/v1/runs/run-paused/events') {
    if (!req.headers.accept?.includes('text/event-stream')) return send({ events: [] })
    if (++pausedConnections === 1) { res.statusCode = 503; return send({ detail: 'connection interrupted' }) }
    res.setHeader('Content-Type', 'text/event-stream')
    res.write(': heartbeat\n\n')
    return
  }
  if (url.pathname === '/api/v1/runs/run-stream') return send(streamState)
  if (url.pathname === '/api/v1/runs/run-stream/events') {
    if (!req.headers.accept?.includes('text/event-stream')) return send({ events: [] })
    streamConnections++
    streamCursors.push(String(req.headers['last-event-id']))
    res.setHeader('Content-Type', 'text/event-stream')
    streamState = { ...streamState, phase: 'SUCCEEDED', event_seq: 3, version: 2 }
    if (streamConnections === 1) {
      const frame = Buffer.from('id: 1\r\ndata: ' + JSON.stringify({ run_id: 'run-stream', seq: 1, event_type: 'fixture', payload: { message: '断线前事件' } }) + '\r\n\r\n')
      const split = frame.indexOf(Buffer.from('断')) + 1
      res.write(frame.subarray(0, split))
      setTimeout(() => res.end(frame.subarray(split)), 20)
    } else if (streamConnections === 2) {
      res.end(`id: 3\ndata: ${JSON.stringify({ run_id: 'run-stream', seq: 3, event_type: 'fixture', payload: {} })}\n\n`)
    } else {
      assert.equal(streamReleases, 0, 'Terminal snapshot cannot stop before its event_seq is received')
      for (const seq of [1, 2, 3]) res.write(`id: ${seq}\ndata: ${JSON.stringify({ run_id: 'run-stream', seq, event_type: 'fixture', payload: { message: '恢复后的事件' } })}\n\n`)
      res.end()
    }
    return
  }
  if (url.pathname === '/api/v1/health/ready') return send({ ready: true })
  if (url.pathname === '/api/v1/browser/commands') return send({ commands, cursor: 0 })
  if (url.pathname.endsWith('/result')) {
    if (failures-- > 0) { assert.equal(receiptAcknowledgements, 0, 'A failed delivery cannot release the executor result'); res.statusCode = 503; return send({ detail: 'fixture transient failure' }) }
    if (missingAcknowledgements-- > 0) { assert.equal(receiptAcknowledgements, 0, 'HTTP success without acceptance cannot release the executor result'); return send({ accepted: false }) }
    received.push(JSON.parse(body)); return send({ accepted: true })
  }
  if (url.pathname.endsWith('/events')) return send({ events: Array.from({ length: state.event_seq }, (_, i) => ({ run_id: state.run_id, seq: i + 1, event_type: 'fixture', payload: {} })).filter(event => event.seq > Number(url.searchParams.get('after') || 0)) })
  if (url.pathname.endsWith('/plans/select')) { selected = JSON.parse(body); state = { ...state, phase: 'REPLANNING', version: 2, event_seq: 2 }; return send(state) }
  if (url.pathname.endsWith('/merchant-candidates')) { const request = JSON.parse(body); selected = request; return send({ source_ref: request.source_ref, merchant: { name: '受控门店', address: '受控地址' }, candidates: [], observed_at: new Date().toISOString(), source: 'amap' }) }
  if (url.pathname.endsWith('/offer-selection')) { selected = JSON.parse(body); state = { ...state, phase: 'REPLANNING', version: 3, event_seq: 3 }; return send({ accepted: true }) }
  if (url.pathname === '/api/v1/runs/run-test') return send(state)
  if (url.pathname === '/api/v1/runs') return send({ runs: [state] })
  res.statusCode = 404; send({ detail: 'not found' })
})
await new Promise<void>(r => server.listen(0, '127.0.0.1', r))
const port = (server.address() as { port: number }).port
const base = { baseURL: `http://127.0.0.1:${port}`, token, browserSessionId: 'desktop-test', dataDir: root,
  emit: (event: unknown) => { events.push(event) }, onTerminal: () => { releases++ }, onActivate: () => { activations++ },
  onReceiptDelivered: (command: BrowserCommand) => {
    const journal = new Map<string, any>(JSON.parse(readFileSync(join(root, 'browser-receipts.json'), 'utf8')))
    assert.equal(journal.get(command.command_id)?.delivered, true, 'Release follows the durable acknowledgement, not only the HTTP response')
    receiptAcknowledgements++
  } }
const waitFor = async (predicate: () => boolean) => {
  const until = Date.now() + 6500
  while (!predicate()) { assert.ok(Date.now() < until, 'fixture deadline exceeded'); await new Promise(r => setTimeout(r, 20)) }
}
const command: BrowserCommand = { command_id: 'command-one', run_id: 'run-test', browser_session_id: 'desktop-test', tab_id: 'tab-test', operation: 'click', arguments: { idx: 1 }, expected_snapshot_id: 'snapshot-test', approved_action_id: 'approved-test' }
let client: HarnessClient | undefined
try {
  client = new HarnessClient({ ...base, execute: async c => { executions++; return { command_id: c.command_id, ok: true, outcome: 'executed' } } })
  const writeJournal = (client as any).writeJournal.bind(client)
  let diskFailure = false
  ;(client as any).writeJournal = (path: string, rows: [string, any][]) => {
    if (!diskFailure && rows.some(([, receipt]) => receipt.delivered)) { diskFailure = true; throw new Error('fixture acknowledgement disk failure') }
    return writeJournal(path, rows)
  }
  assert.equal((await client.status()).ready, true)
  commands = [command]
  client.startBrowserPolling()
  await waitFor(() => received.length > 0)
  await waitFor(() => receiptAcknowledgements > 0)
  assert.equal(diskFailure, true, 'A failed acknowledgement save is retried before releasing the result')
  assert.equal(executions, 1, 'failed POST must retry receipt, never click')
  commands = [{ ...command, arguments: { idx: 99 } }]
  await waitFor(() => events.length > 0)
  assert.equal(executions, 1, 'reused command ID with different payload must not execute')
  state = { ...state, phase: 'SUCCEEDED' }
  await client.getRun('run-test'); await client.getRun('run-test')
  assert.equal(releases, 1, 'terminal snapshot should release only once')
  await client.selectPlan('run-test', 'candidate-test', 3)
  assert.deepEqual(selected, { plan_id: 'candidate-test', plan_version: 3 })
  assert.equal(activations, 1)
  const sourceRef = { command_id: 'source-command', artifact_id: 'page:source-command' }
  const candidates = await client.merchantCandidates('run-test', sourceRef)
  assert.deepEqual(candidates.source_ref, sourceRef)
  assert.equal(activations, 1, 'Read-only merchant lookup cannot activate browser actions')
  const choice = { expected_version: 2, source_ref: sourceRef, offer_index: 1, offer_hash: 'a'.repeat(64), poi_id: 'canonical-poi', identity_confirmed: true }
  await client.selectOffer('run-test', choice)
  assert.deepEqual(selected, choice, 'Offer selection keeps exact source, index, content and plan version binding')
  await client.close()

  // Real HTTP SSE reconnect keeps Last-Event-ID and drains a terminal snapshot's tail.
  client = new HarnessClient({ ...base, emit: event => streamEvents.push(event), onTerminal: () => streamReleases++, execute: async () => { throw new Error('read-only stream test') } })
  await client.getRun('run-stream')
  await waitFor(() => streamReleases === 1)
  assert.deepEqual(streamCursors.slice(0, 3), ['0', '1', '1'], 'A gap must not advance the durable event cursor')
  assert.deepEqual(streamEvents.filter(event => event.seq > 0).map(event => event.seq), [1, 2, 3], 'Replayed events are not duplicated')
  assert.equal(streamEvents.find(event => event.seq === 1).payload.message, '断线前事件', 'UTF-8 characters survive chunk splitting')
  const restored = await client.getRun('run-stream')
  assert.deepEqual(restored.events?.map(event => event.seq), [1, 2, 3], 'History restoration receives the canonical main-process event log')
  await client.close()

  // A resumed paused task often has no new event/version. It must still clear the UI's offline state.
  const recovery: any[] = []
  client = new HarnessClient({ ...base, emit: event => recovery.push(event), execute: async () => { throw new Error('read-only reconnect') } })
  await client.getRun('run-paused')
  await waitFor(() => recovery.filter(event => event.event_type === 'snapshot').length >= 2)
  const failedAt = recovery.findIndex(event => event.event_type === 'connection_error')
  assert.ok(failedAt > 0 && recovery.slice(failedAt + 1).some(event => event.event_type === 'snapshot'), 'Unchanged recovery snapshot clears the connection error')
  const recoveredSnapshots = recovery.filter(event => event.event_type === 'snapshot')
  assert.deepEqual(recoveredSnapshots[0].payload.snapshot, recoveredSnapshots.at(-1).payload.snapshot)
  await client.close()

  client = new HarnessClient({ ...base, execute: async () => { throw new Error('read-only event recovery') } })
  await assert.rejects(client.getRun('run-batch'), /event gap/)
  const batch = await client.getRun('run-batch')
  assert.deepEqual(batch.events?.map(event => event.seq), [1, 2, 3], 'Recovery must retain the prefix accepted before the gap in a batch')
  await client.close()

  // A process can stop after recording dispatch but before any acknowledgement.
  const crashed = { ...command, command_id: 'command-crashed' }
  writeFileSync(join(root, 'browser-receipts.json'), JSON.stringify([[crashed.command_id, { command: crashed }]]))
  commands = [crashed]
  received.length = 0
  client = new HarnessClient({ ...base, execute: async () => { throw new Error('must never repeat interrupted write') } })
  client.startBrowserPolling()
  await waitFor(() => received.length > 0)
  assert.equal(received[0].outcome, 'unknown')
  assert.equal(received[0].error_kind, 'desktop_restarted_during_command')
  await client.close()

  // Restart drains the old writer before another client opens its receipt journal.
  const pending = { ...command, command_id: 'command-drain' }
  commands = [pending]
  let finish!: (value: BrowserObservation) => void
  let entered = false
  let closed = false
  client = new HarnessClient({ ...base, execute: async () => { entered = true; return new Promise(r => { finish = r }) } })
  client.startBrowserPolling()
  await waitFor(() => entered)
  const closing = client.close().then(() => { closed = true })
  await new Promise(r => setTimeout(r, 30))
  assert.equal(closed, false)
  finish({ command_id: pending.command_id, ok: true, outcome: 'executed' })
  await closing
  const journal = new Map<string, any>(JSON.parse(readFileSync(join(root, 'browser-receipts.json'), 'utf8')))
  assert.equal(journal.get(pending.command_id).result.outcome, 'executed')
  console.log('Harness transport regression passed: durable receipt retry, identity conflict, SSE resume with terminal tail, selection and restart drain')
} finally {
  await client?.close()
  server.closeAllConnections()
  await new Promise<void>(r => server.close(() => r()))
  rmSync(root, { recursive: true, force: true })
}
