// Agent 入口：注册所有工具 → 跑 Harness Loop → 用完 RewriteMemory 越用越懂。
import type { AgentReply, ChatMessage } from '@shared/types'
import { runAgentLoop } from './loop'
import { session } from './session'
import { rewriteMemory } from '../brain/memory'

// 副作用导入：把工具注册进 registry
import '../tools/localTools'
import '../tools/browserTools'
import '../tools/guideTools'
import '../tools/queueTools'
import './subagents'

export async function runAgent(message: string, history: ChatMessage[] = []): Promise<AgentReply> {
  const reply = await runAgentLoop(message, history)
  // 异步整理记忆，不阻塞返回
  rewriteMemory().catch(() => {})
  return reply
}

// 两步确认：用户点确认后执行挂起的写操作
export async function confirmAction(token: string): Promise<AgentReply> {
  const action = session.consumeConfirm(token)
  if (!action) return { content: '这个确认已过期或已处理。', steps: [], cards: [], activities: [] }
  const out = await action.run()
  return { content: out.text, steps: [], cards: out.cards, activities: [] }
}

export function cancelAction(token: string): AgentReply {
  session.consumeConfirm(token)
  return { content: '好的，已取消该操作。', steps: [], cards: [], activities: [] }
}
