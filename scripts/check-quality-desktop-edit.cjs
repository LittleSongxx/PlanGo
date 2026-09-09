// Pure TEST protocol objects only. No desktop, HTTP server, model or real case runs.
const assert = require('node:assert/strict')
const { createHash } = require('node:crypto')
const { driverConfig, messageSequence, permittedWrite } = require('./quality_desktop_cases.cjs')
const messages = ['测试修改甲', '测试修改乙']
const declared = { case_id: 'TEST-EDIT', environment: { driver: 'edit' }, agent_input: { user_turns: messages.map(message => ({ message })) } }
const config = driverConfig({ case_id: declared.case_id, run_id: 'test-run', driver: 'edit', messages }, declared)
assert.deepEqual(config.messages, messages)
for (const bad of [[], [...messages, 'extra'], [...messages].reverse(), ['unregistered']]) {
  assert.throws(() => driverConfig({ ...config, messages: bad }, declared))
}
assert.throws(() => driverConfig(config))
const body = (id, text) => {
  const value = { request_id: id, text, location_context: { city: '重庆' } }
  return { ...value, request_fingerprint: createHash('sha256').update(JSON.stringify(value)).digest('hex') }
}
const accepted = (entry, extras = {}) => ({ accepted: true, run_id: 'test-run', request_id: entry.request_id, request_fingerprint: entry.request_fingerprint, ...extras })
const sequence = messageSequence(config), first = body('test-first', messages[0]), second = body('test-second', messages[1])
const path = new URL('http://127.0.0.1:45678/api/v1/runs/test-run/messages')
assert.equal(sequence.permit(first), null, 'Nothing is sent before the UI driver arms a declared step')
sequence.arm(0)
assert.equal(sequence.permit(second), null, 'Cannot skip the message order')
for (const suffix of ['/api/v1/runs', '/api/v1/runs/other/messages', '/api/v1/runs/test-run/resume', '/api/v1/runs/test-run/draft-decision']) {
  assert.equal(permittedWrite(config, 'POST', new URL(suffix, path), first, sequence), null)
}
assert.equal(permittedWrite(config, 'PUT', path, first, sequence), null)
assert.equal(permittedWrite(config, 'POST', new URL(path.href + '?submit=true'), first, sequence), null)
assert.equal(permittedWrite(config, 'POST', path, { ...first, request_fingerprint: '0'.repeat(64) }, sequence), null)
const entry = permittedWrite(config, 'POST', path, first, sequence).identity
assert.equal(entry.index, 0)
assert.equal(sequence.permit(body('another-first', messages[0])), null, 'A fresh identity cannot duplicate the current script step')
assert.throws(() => sequence.arm(1), 'Next step requires a real matching acceptance')
assert.equal(sequence.accept(entry, accepted(entry, { run_id: 'other' }), 202), false)
assert.equal(sequence.accept(entry, accepted(entry), 200), false)
assert.equal(sequence.accept(entry, accepted(entry), 202), true)
sequence.arm(1)
assert.equal(sequence.permit(first).index, 0, 'Replay retains its original step after the next step is armed')
assert.equal(sequence.permit({ ...first, text: messages[1] }), null)
assert.equal(sequence.permit(second).index, 1)
assert.equal(sequence.permit(body('unregistered-third', '测试修改丙')), null)

const repeated = messageSequence({ ...config, messages: ['same', 'same'] })
repeated.arm(0)
const repeatedFirst = repeated.permit(body('repeat-1', 'same'))
assert(repeated.accept(repeatedFirst, accepted(repeatedFirst), 202))
assert.equal(repeated.permit(body('repeat-2', 'same')), null)
repeated.arm(1)
assert.equal(repeated.permit(body('repeat-2', 'same')).index, 1, 'Same text in an explicitly new scripted turn stays independent')
assert.equal(repeated.permit(body('repeat-1', 'same')).index, 0)

const legacy = driverConfig({ case_id: 'DEV-10', run_id: 'test-run' })
const legacySequence = messageSequence(legacy)
assert(legacySequence.permit(body('old-request', legacy.message)), 'Legacy fault driver still allows only its one declared message')
assert.equal(driverConfig({ case_id: 'DEV09' }).driver, 'save_restart')
console.log('Desktop edit configuration, ordered identities, replay and exact proxy write isolation passed; no case executed')
