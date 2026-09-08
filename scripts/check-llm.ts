// Run: npx tsx scripts/check-llm.ts
import assert from 'node:assert/strict'
import { getConfig } from '../src/main/config'
import { pingLlm } from '../src/main/llm'

const config = getConfig().llm
const original = { ...config }
const fetch = globalThis.fetch
try {
  Object.assign(config, { apiKey: 'fixture-only', baseURL: 'https://fixture.invalid/v1/', model: 'fixture-model' })
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'https://fixture.invalid/v1/chat/completions')
    assert.equal((options?.headers as Record<string, string>).Authorization, 'Bearer fixture-only')
    assert.equal(JSON.parse(String(options?.body)).model, 'fixture-model')
    assert.equal(JSON.parse(String(options?.body)).max_tokens, 8)
    assert.ok(options?.signal instanceof AbortSignal)
    return Response.json({ choices: [{ message: { content: '<think>internal</think> ok ' } }] })
  }
  assert.deepEqual(await pingLlm(), { ok: true, message: 'ok' })
  globalThis.fetch = async () => new Response('unavailable', { status: 503 })
  assert.deepEqual(await pingLlm(), { ok: false, message: '模型返回 503：unavailable' })
  globalThis.fetch = async () => { throw new Error('offline') }
  assert.deepEqual(await pingLlm(), { ok: false, message: 'offline' })
  config.apiKey = ''
  globalThis.fetch = async () => { assert.fail('missing credentials must not send a request') }
  assert.match((await pingLlm()).message, /未配置大模型 API Key/)
  console.log('Desktop model connectivity check passed')
} finally {
  Object.assign(config, original)
  globalThis.fetch = fetch
}
