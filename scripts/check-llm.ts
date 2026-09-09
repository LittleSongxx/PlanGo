// Run: npx tsx scripts/check-llm.ts
import assert from 'node:assert/strict'
import { getConfig, getConfigMasked, safeServiceOrigin } from '../src/main/config'
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
  assert.deepEqual(await pingLlm(), { ok: true, message: '文本回复已返回；未测试工具调用或图片能力' })
  globalThis.fetch = async () => new Response('unavailable', { status: 503 })
  assert.deepEqual(await pingLlm(), { ok: false, message: '桌面模型返回 HTTP 503：请核对接口配置或稍后重试' })
  globalThis.fetch = async () => new Response('fixture-only https://user:password@example.com/?key=secret', { status: 401 })
  assert.deepEqual(await pingLlm(), { ok: false, message: '桌面模型返回 HTTP 401：密钥鉴权失败' })
  globalThis.fetch = async () => { throw new Error('offline https://user:password@example.com/?key=secret') }
  assert.deepEqual(await pingLlm(), { ok: false, message: '桌面模型连接失败或超时，请检查桌面网络与接口地址。' })
  config.baseURL = 'https://user:password@fixture.invalid/v1?key=secret#private'
  assert.equal(getConfigMasked().llm.baseURL, '')
  assert.equal(getConfigMasked().llm.apiKey, '••••')
  assert.equal(config.apiKey, 'fixture-only', 'Display masking must preserve the execution configuration')
  assert.equal(safeServiceOrigin(config.baseURL), 'https://fixture.invalid')
  assert.equal(safeServiceOrigin('not a URL'), '地址无效')
  config.apiKey = ''
  globalThis.fetch = async () => { assert.fail('missing credentials must not send a request') }
  assert.match((await pingLlm()).message, /未配置大模型 API Key/)
  console.log('Desktop model connectivity check passed')
} finally {
  Object.assign(config, original)
  globalThis.fetch = fetch
}
