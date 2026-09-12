import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:http'
import { createShare, getShareFeedback, pickLanAddress, startShareServer, stopShareServer } from '../src/main/share/server'
import type { Plan } from '../src/shared/types'

const dir = mkdtempSync(join(tmpdir(), 'plango-share-'))
const occupied = createServer()
await new Promise<void>(resolve => occupied.listen(0, '0.0.0.0', resolve))
try {
  const blockedPort = (occupied.address() as { port: number }).port
  const port = await startShareServer(blockedPort, dir)
  assert.ok(port > blockedPort, 'occupied port must retry without publishing an invalid server')
  const plan: Plan = { plan_id: 'fixture', run_id: 'run-fixture', version: 1, party_size: 4, style: 'balanced', title: '分享回归', nodes: [], total_cost: null, radar: {}, share_message: '' }
  const share = createShare(plan, '上海')
  plan.title = 'mutated UI'
  let response = await fetch(`http://127.0.0.1:${port}/api/s/${share.id}`)
  assert.equal((await response.json() as any).plan.title, '分享回归', 'share must be an immutable snapshot')
  response = await fetch(`http://127.0.0.1:${port}/api/s/${share.id}/vote`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voter: '朋友', vote: 'up' }) })
  assert.equal(response.ok, true)
  stopShareServer()
  const nextPort = await startShareServer(0, dir)
  assert.equal(getShareFeedback(share.id).tally.up, 1, 'votes survive process lifecycle reload')
  response = await fetch(`http://127.0.0.1:${nextPort}/api/s/${share.id}`)
  const data = await response.json() as any
  assert.equal(data.plan.total_cost, null)
  assert.equal(data.plan.party_size, 4)
  assert.equal(pickLanAddress(['198.18.0.1', '192.168.1.55']), '192.168.1.55')
  assert.equal(pickLanAddress(['198.18.0.1']), '198.18.0.1')
  console.log('Share persistence regression passed')
} finally {
  stopShareServer()
  await new Promise<void>(resolve => occupied.close(() => resolve()))
  rmSync(dir, { recursive: true, force: true })
}
