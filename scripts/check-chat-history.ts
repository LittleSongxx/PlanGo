import assert from 'node:assert/strict'
import { projectConversation, projectHarness } from '../src/renderer/src/lib/harnessProjection'
import type { HarnessEvent, HarnessSnapshot } from '../src/shared/types'

const event = (seq: number, event_type: string, payload: Record<string, unknown>, created_at = String(seq)): HarnessEvent => ({ run_id: 'chat', seq, event_type, payload, created_at })
const events = [
  event(1, 'RUN_CREATED', { input_text: '安排晚餐' }),
  event(2, 'GRAPH_INTERRUPTED', { interrupts: [{ type: 'clarification', question: '几个人？' }] }),
  event(3, 'USER_MESSAGE', { text: '2人' }),
  event(4, 'GRAPH_INTERRUPTED', { interrupts: [{ type: 'draft_review', question: '草案仍待核对。' }] }, 'boundary'),
  event(5, 'ASSISTANT_MESSAGE', { content: '已生成2人草案，规则待核对。', turn_id: 2 }, 'boundary'),
  event(6, 'REQUIREMENTS_EDITED', { reason: '改为3人' }),
  event(7, 'ASSISTANT_MESSAGE', { content: '双人餐未明确覆盖3人。', turn_id: 3 }),
  event(8, 'REQUIREMENTS_EDITED', { reason: '改为3人' }),
  event(9, 'ASSISTANT_MESSAGE', { content: '双人餐未明确覆盖3人。', turn_id: 4 })
]
const run: HarnessSnapshot = { run_id: 'chat', phase: 'SUCCEEDED', outcome: 'SUCCEEDED', input_text: '改为3人', event_seq: 9, state: { turn_id: 4, reason: '双人餐未明确覆盖3人。' }, events }
const before = structuredClone(run)
const messages = projectHarness(run).messages
assert.deepEqual(messages.map(m => [m.role, m.content]), [
  ['user', '安排晚餐'], ['assistant', '几个人？'], ['user', '2人'], ['assistant', '已生成2人草案，规则待核对。'],
  ['user', '改为3人'], ['assistant', '双人餐未明确覆盖3人。'], ['user', '改为3人'], ['assistant', '双人餐未明确覆盖3人。']
])
assert.equal(new Set(messages.map(m => m.id)).size, messages.length)
assert.deepEqual(projectHarness(JSON.parse(JSON.stringify(run))).messages, messages, 'Restart restores all replies without a local UI cache')
assert.deepEqual(projectHarness({ ...run, events: [...events, events[8]] }).messages, messages, 'Duplicate event delivery cannot duplicate replies')
assert.deepEqual(run, before, 'Historical records stay unchanged')
const pending = { ...run, event_seq: 10, command_pending: true, events: [...events, event(10, 'USER_MESSAGE', { text: '预算200' })] }
assert.equal(projectHarness(pending).messages.at(-1)?.content, '预算200', 'A queued input must not show the previous answer as its reply')
const old = { ...run, events: undefined, state: { messages: [{ id: 'u', type: 'human', content: '历史问题' }, { id: 'a', type: 'ai', content: '历史回复' }] } }
assert.deepEqual(projectConversation(old).map(m => m.content), ['历史问题', '历史回复'])
assert.deepEqual(projectConversation({ ...old, events: events.slice(1) }).map(m => m.content), ['历史问题', '历史回复'], 'Incomplete events retain saved history')
const mixed = { ...old, input_text: '历史问题', event_seq: 1, events: [event(1, 'RUN_CREATED', { input_text: '历史问题' })] }
assert.deepEqual(projectConversation(mixed).map(m => m.content), ['历史问题', '历史回复'])
console.log('Conversation history: alternating rounds, restart, duplicate delivery, queued input and legacy replies passed')
