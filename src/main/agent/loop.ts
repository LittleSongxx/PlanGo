// Harness 式 Agent Loop（对标 Claude Code）：while + tool_calls + 错误即信息 + MAX_TURNS 兜底。
// 小悠=总调度：LongCat 决定调哪个工具，结果回喂，直到给出最终答复。
import type { AgentReply, ChatMessage as DomainMsg, OutcomeCard } from '@shared/types'
import { chatWithTools, type ChatMessage } from '../llm'
import { getToolDefs, getTool, toolLabel } from '../tools/registry'
import { buildSystemPrompt } from './prompt'
import { emitStep, updateStep } from './bus'

const MAX_TURNS = 8
const TOOL_OUTPUT_CAP = 4000

export interface LoopOptions {
  toolNames?: string[]
  maxTurns?: number
  systemSuffix?: string
  noHistory?: boolean
  silent?: boolean // 子 Agent：不往主步骤流推
}

export async function runAgentLoop(message: string, history: DomainMsg[] = [], opts: LoopOptions = {}): Promise<AgentReply> {
  const activities: string[] = []
  const cards: OutcomeCard[] = []

  const sys = buildSystemPrompt(opts.systemSuffix)
  const messages: ChatMessage[] = [{ role: 'system', content: sys }]
  if (!opts.noHistory) {
    for (const h of history.slice(-6)) {
      if (h.role === 'user' || h.role === 'assistant') messages.push({ role: h.role, content: h.content })
    }
  }
  messages.push({ role: 'user', content: message })

  const tools = getToolDefs(opts.toolNames)
  const maxTurns = opts.maxTurns ?? MAX_TURNS

  for (let turn = 0; turn < maxTurns; turn++) {
    let res
    try {
      res = await chatWithTools(messages, tools, { temperature: 0.5 })
    } catch (e) {
      return { content: `我这边连模型时出了点问题：${(e as Error).message}。请稍后再试或检查设置里的 LongCat Key。`, steps: [], cards, activities }
    }

    const assistantMsg: ChatMessage = { role: 'assistant', content: res.content || '' }
    if (res.tool_calls?.length) assistantMsg.tool_calls = res.tool_calls
    messages.push(assistantMsg)

    if (!res.tool_calls || res.tool_calls.length === 0) {
      return { content: res.content || '好的。', steps: [], cards, activities }
    }

    for (const tc of res.tool_calls) {
      const tool = getTool(tc.function.name)
      let outcomeText = ''
      if (!tool) {
        outcomeText = `错误：未知工具 ${tc.function.name}`
      } else {
        let args: Record<string, unknown> = {}
        try {
          args = tc.function.arguments ? JSON.parse(tc.function.arguments) : {}
        } catch {
          args = {}
        }
        const sid = opts.silent ? '' : emitStep(`调用 ${toolLabel(tool.name)}`)
        try {
          const outcome = await tool.execute(args)
          outcomeText = outcome.result
          if (outcome.cards) cards.push(...outcome.cards)
          if (outcome.activities) activities.push(...outcome.activities)
          if (sid) updateStep(sid, 'done')
        } catch (e) {
          outcomeText = `工具 ${tool.name} 执行失败：${(e as Error).message}`
          if (sid) updateStep(sid, 'error', (e as Error).message)
        }
      }
      messages.push({ role: 'tool', tool_call_id: tc.id, content: truncate(outcomeText) })
    }
  }

  return { content: '我执行了几步，但还需要更明确的指令才能继续。可以把需求说得更具体一点吗？（比如几个人、预算、几点出发）', steps: [], cards, activities }
}

function truncate(s: string): string {
  if (s.length <= TOOL_OUTPUT_CAP) return s
  return s.slice(0, TOOL_OUTPUT_CAP) + `\n…（已截断，共${s.length}字）`
}
