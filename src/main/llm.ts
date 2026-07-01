// 大模型接入层：OpenAI 兼容客户端，默认 LongCat-2.0（api.longcat.chat/openai/v1）。
// 原生 tool_calls + 文本 <longcat_tool_call> 解析兜底。Provider 抽象可切 DeepSeek/Kimi。
import { getConfig } from './config'

export type ChatRole = 'system' | 'user' | 'assistant' | 'tool'

export interface ChatMessage {
  role: ChatRole
  content: string | unknown
  name?: string
  tool_call_id?: string
  tool_calls?: ToolCall[]
}

export interface ToolCall {
  id: string
  type: 'function'
  function: { name: string; arguments: string }
}

export interface ToolDef {
  type: 'function'
  function: { name: string; description: string; parameters: Record<string, unknown> }
}

export interface ChatResult {
  content: string
  tool_calls?: ToolCall[]
  finish_reason?: string
}

interface ChatOpts {
  temperature?: number
  maxTokens?: number
  tools?: ToolDef[]
  json?: boolean
  signal?: AbortSignal
}

// 推理模型可能把思维链以 <think>…</think> 内联，统一剥离。
export function stripReasoning(text: string): string {
  if (!text) return ''
  let t = text
  t = t.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '')
  t = t.replace(/<think(?:ing)?>[\s\S]*$/gi, '')
  t = t.replace(/<\/?think(?:ing)?>/gi, '')
  return t.trim()
}

// 文本兜底：某些 OpenAI 兼容端点在不返回原生 tool_calls 时，会把工具调用写进正文。
// 解析形如 <longcat_tool_call>{"name":"x","arguments":{...}}</longcat_tool_call> 或 ```json {tool_call}```
export function parseTextToolCalls(content: string): { calls: ToolCall[]; rest: string } {
  const calls: ToolCall[] = []
  let rest = content
  const patterns = [
    /<longcat_tool_call>\s*([\s\S]*?)\s*<\/longcat_tool_call>/gi,
    /<tool_call>\s*([\s\S]*?)\s*<\/tool_call>/gi
  ]
  for (const re of patterns) {
    rest = rest.replace(re, (_m, body) => {
      try {
        const obj = JSON.parse(body)
        const name = obj.name || obj.tool || obj.function?.name
        const args = obj.arguments ?? obj.parameters ?? obj.function?.arguments ?? {}
        if (name) {
          calls.push({
            id: 'txt_' + Math.random().toString(36).slice(2, 9),
            type: 'function',
            function: { name, arguments: typeof args === 'string' ? args : JSON.stringify(args) }
          })
        }
      } catch {
        /* ignore */
      }
      return ''
    })
  }
  return { calls, rest: rest.trim() }
}

export function isConfigured(): boolean {
  const c = getConfig()
  return !!c.llm.apiKey && !!c.llm.baseURL && !!c.llm.model
}

async function rawChat(messages: ChatMessage[], opts: ChatOpts = {}): Promise<ChatResult> {
  const cfg = getConfig().llm
  if (!cfg.apiKey) throw new Error('未配置大模型 API Key（LongCat/MiniMax，请在「设置」或 .env 中填写）')
  const url = `${cfg.baseURL.replace(/\/$/, '')}/chat/completions`
  const body: Record<string, unknown> = {
    model: cfg.model,
    messages,
    temperature: opts.temperature ?? 0.6
  }
  if (opts.maxTokens) body.max_tokens = opts.maxTokens
  if (opts.tools && opts.tools.length) {
    body.tools = opts.tools
    body.tool_choice = 'auto'
  }
  if (opts.json) body.response_format = { type: 'json_object' }

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 90_000)
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.apiKey}` },
      body: JSON.stringify(body),
      signal: opts.signal ?? controller.signal
    })
    if (!res.ok) {
      const txt = await res.text().catch(() => '')
      throw new Error(`模型返回 ${res.status}：${txt.slice(0, 300)}`)
    }
    const data = (await res.json()) as {
      choices?: { message?: { content?: string; tool_calls?: ToolCall[] }; finish_reason?: string }[]
    }
    const choice = data.choices?.[0]
    let content = stripReasoning(choice?.message?.content ?? '')
    let tool_calls = choice?.message?.tool_calls
    // 文本兜底：没有原生 tool_calls 但正文里藏了工具调用
    if ((!tool_calls || tool_calls.length === 0) && content) {
      const parsed = parseTextToolCalls(content)
      if (parsed.calls.length) {
        tool_calls = parsed.calls
        content = parsed.rest
      }
    }
    return { content, tool_calls, finish_reason: choice?.finish_reason }
  } finally {
    clearTimeout(timeout)
  }
}

export async function chat(system: string, user: string, opts: { temperature?: number; maxTokens?: number; json?: boolean } = {}): Promise<string> {
  const r = await rawChat(
    [
      { role: 'system', content: system },
      { role: 'user', content: user }
    ],
    opts
  )
  return r.content.trim()
}

export async function chatJSON<T = unknown>(system: string, user: string, opts: { temperature?: number } = {}): Promise<T> {
  const text = await chat(system, user, { ...opts, json: true })
  const cleaned = text.replace(/^```json\s*/i, '').replace(/```$/i, '').trim()
  return JSON.parse(cleaned) as T
}

export async function chatWithTools(messages: ChatMessage[], tools: ToolDef[], opts: { temperature?: number } = {}): Promise<ChatResult> {
  return rawChat(messages, { ...opts, tools })
}

// 多模态：把截图（dataURL/http）连同提示发给视觉模型（MiniMax-M2/LongCat-VL 等 OpenAI 兼容多模态端点）。
export async function chatVision(system: string, text: string, imageUrl: string, opts: { temperature?: number; json?: boolean } = {}): Promise<string> {
  const r = await rawChat(
    [
      { role: 'system', content: system },
      {
        role: 'user',
        content: [
          { type: 'text', text },
          { type: 'image_url', image_url: { url: imageUrl } }
        ]
      }
    ],
    opts
  )
  return r.content.trim()
}

export async function pingLlm(): Promise<{ ok: boolean; message: string }> {
  try {
    const r = await chat('你是连通性测试助手。', '回复"ok"两个字符即可。', { maxTokens: 8 })
    return { ok: true, message: r || 'ok' }
  } catch (e) {
    return { ok: false, message: (e as Error).message }
  }
}
