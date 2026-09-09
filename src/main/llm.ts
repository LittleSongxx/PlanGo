// 桌面设置页的 OpenAI 兼容端点连通性测试；Agent 模型调用由 Harness 负责。
import { getConfig } from './config'

export async function pingLlm(): Promise<{ ok: boolean; message: string }> {
  try {
    const cfg = getConfig().llm
    if (!cfg.apiKey || !cfg.model) return { ok: false, message: '桌面未配置大模型 API Key 或模型名称，请在「连接与能力」填写。' }
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
      signal: AbortSignal.timeout(10_000)
    })
    if (!res.ok) {
      const reason: Record<number, string> = { 401: '密钥鉴权失败', 403: '模型权限不足', 404: '模型名称或接口地址错误', 429: '限流或额度不足' }
      return { ok: false, message: `桌面模型返回 HTTP ${res.status}：${reason[res.status] || '请核对接口配置或稍后重试'}` }
    }
    const data = await res.json() as { choices?: { message?: { content?: string } }[] }
    const message = (data.choices?.[0]?.message?.content || '')
      .replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '')
      .replace(/<think(?:ing)?>[\s\S]*$/gi, '')
      .replace(/<\/?think(?:ing)?>/gi, '').trim()
    return message ? { ok: true, message: '文本回复已返回；未测试工具调用或图片能力' } : { ok: false, message: '桌面模型未返回可读文本，请核对模型配置。' }
  } catch {
    return { ok: false, message: '桌面模型连接失败或超时，请检查桌面网络与接口地址。' }
  }
}
