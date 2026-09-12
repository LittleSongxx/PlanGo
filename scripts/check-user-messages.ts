// Presentation and protocol checks only: no browser, HTTP server or real model.
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { deliveryMessage, errorDiagnostic, publicStatus, userMessage, type MessageContext } from '../src/shared/userMessages'
import { projectConversation, projectEvents, projectHarness } from '../src/renderer/src/lib/harnessProjection'
import { HarnessClient } from '../src/main/harnessClient'
import type { HarnessSnapshot } from '../src/shared/types'

const internal = "1 validation error for Reference\nfield\nString should match pattern '^SECRET_PATTERN$' [type=string_pattern_mismatch, input_value='PRIVATE_INPUT'] https://errors.pydantic.dev/2/v/string_pattern_mismatch"
for (const context of ['task', 'offer', 'browser', 'settings', 'storage', 'share', 'discover', 'action', 'reminder', 'carry'] as MessageContext[]) {
  const shown = userMessage(new Error(internal), context)
  assert(!/PRIVATE_INPUT|SECRET_PATTERN|pydantic|input_value|validation|https?:/.test(shown))
  assert(/[\u3400-\u9fff]/.test(shown))
}
assert.equal(userMessage(new Error('人数需要至少1人，请修改后保存。')), '人数需要至少1人，请修改后保存。')
const explanation = '已保留所给资料，来源为 https://example.org/menu；尚未确认完整规则。'
assert.equal(publicStatus(explanation), explanation, 'Normal answers and source URLs are not error payloads')
assert.equal(publicStatus('English source excerpt and price 98 CNY'), 'English source excerpt and price 98 CNY')
assert(!publicStatus('Harness: request_id_conflicts_with_saved_content').includes('request_id'))
assert(!deliveryMessage('unconfirmed', internal).includes('未送达'))
assert(deliveryMessage('accepted', internal).includes('已接收'))
assert(!/重试|重新发送|未送达/.test(deliveryMessage('accepted', internal)))
assert(!deliveryMessage('not_sent', 'request_id_conflicts_with_saved_content').includes('继续发送'))
assert(!/重试|继续发送/.test(deliveryMessage('not_sent', 'write_commands_cannot_be_retried')))
assert(!/重试|再试|重新提交/.test(userMessage(new Error(internal), 'action')))
assert(!/PRIVATE_INPUT|SECRET_PATTERN/.test(JSON.stringify(errorDiagnostic(new Error(internal)))))
const run: HarnessSnapshot = { run_id: 'message-check', input_text: '用户原始输入', phase: 'FAILED', outcome: 'FAILED', event_seq: 0,
  state: { reason: internal, messages: [{ role: 'user', content: internal }], action_results: [{ action_id: 'a', status: 'UNKNOWN', error: internal }] } }
const original = JSON.stringify(run)
const projection = projectHarness(run)
assert.equal(projection.messages[0].content, internal, 'User input must not be rewritten')
assert(!projection.messages.at(-1)!.content.includes('validation'))
assert.match(projection.cards.find(card => card.kind === 'receipt')!.items[0].detail, /结果未知/)
assert.equal(JSON.stringify(run), original, 'Presentation does not rewrite durable state')
assert(!projectConversation({ ...run, state: { messages: [{ role: 'assistant', content: internal }] } })[0].content.includes('PRIVATE_INPUT'))
assert(!projectEvents([{ run_id: run.run_id, seq: 1, event_type: 'RUN_FAILED', payload: { reason: internal } }])[0].detail?.includes('pydantic'))

const directory = mkdtempSync(join(tmpdir(), 'plango-user-message-check-'))
const originalFetch = globalThis.fetch
const client = new HarnessClient({ baseURL: 'https://fixture.invalid', token: 'fixture', browserSessionId: 'fixture', dataDir: directory,
  execute: async () => { throw new Error('No browser in this check') }, emit: () => {} })
try {
  globalThis.fetch = async () => Response.json({ detail: 'request_id_conflicts_with_saved_content' }, { status: 409 })
  await assert.rejects(client.request('/api/v1/runs', 'POST', {}), /request_id_conflicts_with_saved_content/, 'The machine-readable 409 reaches HarnessClient unchanged')
} finally { client.stop(); globalThis.fetch = originalFetch; rmSync(directory, { recursive: true, force: true }) }
console.log('User messages: natural presentation, preserved uncertainty/source text and unchanged protocol errors passed')
