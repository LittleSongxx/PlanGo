import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { HarnessClient } from '../src/main/harnessClient'
import type { BrowserCommand, BrowserObservation } from '../src/shared/browser'

const root = mkdtempSync(join(tmpdir(), 'yoyu-transport-'))
const received: BrowserObservation[] = []
const events: unknown[] = []
let commands: BrowserCommand[] = []
let failures = 1
let executions = 0
let releases = 0
let activations = 0
let state = { run_id: 'run-test', input_text: 'fixture', phase: 'RESEARCHING', event_seq: 1, version: 1, state: {} }
let selected: unknown
const token = 'transport-fixture-only'
const server = createServer(async (req, res) => {
  assert.equal(req.headers.authorization, `Bearer ${token}`)
  const url = new URL(req.url || '/', 'http://localhost')
  let body = ''
  for await (const part of req) body += part
  res.setHeader('Content-Type', 'application/json')
  const send = (value: unknown) => res.end(JSON.stringify(value))
  if (url.pathname === '/api/v1/health/ready') return send({ ready: true })
  if (url.pathname === '/api/v1/browser/commands') return send({ commands, cursor: 0 })
  if (url.pathname.endsWith('/result')) {
    if (failures-- > 0) { res.statusCode = 503; return send({ detail: 'fixture transient failure' }) }
    received.push(JSON.parse(body)); return send({ accepted: true })
  }
  if (url.pathname.endsWith('/events')) return send({ events: [] })
  if (url.pathname.endsWith('/plans/select')) { selected = JSON.parse(body); state = { ...state, phase: 'REPLANNING', version: 2, event_seq: 2 }; return send(state) }
  if (url.pathname === '/api/v1/runs/run-test') return send(state)
  if (url.pathname === '/api/v1/runs') return send({ runs: [state] })
  res.statusCode = 404; send({ detail: 'not found' })
})
await new Promise<void>(r => server.listen(0, '127.0.0.1', r))
const port = (server.address() as { port: number }).port
const base = { baseURL: `http://127.0.0.1:${port}`, token, browserSessionId: 'desktop-test', dataDir: root,
  emit: (event: unknown) => { events.push(event) }, onTerminal: () => { releases++ }, onActivate: () => { activations++ } }
const waitFor = async (predicate: () => boolean) => {
  const until = Date.now() + 6500
  while (!predicate()) { assert.ok(Date.now() < until, 'fixture deadline exceeded'); await new Promise(r => setTimeout(r, 20)) }
}
const command: BrowserCommand = { command_id: 'command-one', run_id: 'run-test', browser_session_id: 'desktop-test', tab_id: 'tab-test', operation: 'click', arguments: { idx: 1 }, expected_snapshot_id: 'snapshot-test', approved_action_id: 'approved-test' }
let client: HarnessClient | undefined
try {
  client = new HarnessClient({ ...base, execute: async c => { executions++; return { command_id: c.command_id, ok: true, outcome: 'executed' } } })
  assert.equal((await client.status()).ready, true)
  commands = [command]
  client.startBrowserPolling()
  await waitFor(() => received.length > 0)
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
  console.log('Harness transport regression passed: durable receipt retry, identity conflict, terminal release, selection and restart drain')
} finally {
  await client?.close()
  server.closeAllConnections()
  await new Promise<void>(r => server.close(() => r()))
  rmSync(root, { recursive: true, force: true })
}
