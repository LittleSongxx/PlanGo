// 桌面设置页的 OpenAI 兼容端点连通性测试；Agent 模型调用由 Harness 负责。
import { getConfig } from './config'

export async function pingLlm(): Promise<{ ok: boolean; message: string }> {
  try {
    const cfg = getConfig().llm
    if (!cfg.apiKey) throw new Error('未配置大模型 API Key（LongCat/MiniMax，请在「设置」或 .env 中填写）')
    const res = await fetch(`${cfg.baseURL.replace(/\/$/, '')}/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.apiKey}` },
      body: JSON.stringify({
        model: cfg.model,
        messages: [
          { role: 'system', content: '你是连通性测试助手。' },
          { role: 'user', content: '回复"ok"两个字符即可。' }
        ],
        temperature: 0.6,
        max_tokens: 8
      }),
      signal: AbortSignal.timeout(90_000)
    })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`模型返回 ${res.status}：${text.slice(0, 300)}`)
    }
    const data = await res.json() as { choices?: { message?: { content?: string } }[] }
    const message = (data.choices?.[0]?.message?.content || '')
      .replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '')
      .replace(/<think(?:ing)?>[\s\S]*$/gi, '')
      .replace(/<\/?think(?:ing)?>/gi, '').trim()
    return { ok: true, message: message || 'ok' }
  } catch (error) {
    return { ok: false, message: (error as Error).message }
  }
}
