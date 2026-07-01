// 工具注册表（官方风格 @is_tool READ/WRITE + JSON Schema）。
// Harness Loop 通过 getToolDefs 拿到工具清单喂给 LongCat；getTool 执行。
import type { OutcomeCard } from '@shared/types'
import type { ToolDef } from '../llm'

export interface ToolOutcome {
  result: string // 回喂给模型的文本（错误即信息）
  cards?: OutcomeCard[]
  activities?: string[]
}

export interface Tool {
  name: string
  label: string
  description: string
  kind: 'READ' | 'WRITE'
  parameters: Record<string, unknown>
  execute: (args: Record<string, unknown>) => Promise<ToolOutcome>
}

const REGISTRY = new Map<string, Tool>()

export function registerTool(t: Tool): void {
  REGISTRY.set(t.name, t)
}

export function getTool(name: string): Tool | undefined {
  return REGISTRY.get(name)
}

export function allTools(): Tool[] {
  return [...REGISTRY.values()]
}

export function getToolDefs(names?: string[]): ToolDef[] {
  const tools = names ? names.map((n) => REGISTRY.get(n)).filter(Boolean) as Tool[] : allTools()
  return tools.map((t) => ({
    type: 'function',
    function: { name: t.name, description: `[${t.kind}] ${t.description}`, parameters: t.parameters }
  }))
}

export function toolLabel(name: string): string {
  return REGISTRY.get(name)?.label ?? name
}
