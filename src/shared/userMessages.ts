/** Presentation only. Protocol errors and delivery states remain unchanged. */
export type MessageContext = 'task' | 'offer' | 'browser' | 'settings' | 'storage' | 'share' | 'discover' | 'action' | 'reminder'

const fallback: Record<MessageContext, string> = {
  task: '暂时无法查看任务结果。请重新连接后查看原任务。',
  offer: '暂时无法读取优惠信息。请重新打开来源页面核对。',
  browser: '这个页面暂时无法打开。请检查网址和网络，再重新加载页面。',
  settings: '暂时无法完成设置。请检查填写内容，稍后再试。',
  storage: '暂时无法保存或恢复本地内容。请保留当前输入，稍后再试。',
  share: '暂时无法生成分享内容。请先查看原行程，再重新打开分享。',
  discover: '暂时无法查找附近地点。请先确认位置，再刷新列表。',
  action: '暂时无法确认这次操作的结果。请先核对当前页面或实际记录，不要重复提交。',
  reminder: '暂时无法确认提醒的最新状态。请重新打开提醒列表核对。'
}
const technical = /traceback|validation.?error|pydantic|zoderror|input_value|input_type|(?:string|literal|value)_\w+|should match pattern|\bregex\b|https?:\/\/|\b(?:HTTP|ECONN\w*|ENOENT|ENOSPC|ERR_\w+|TypeError|ReferenceError)\b|(?:^|\s)at \S+\(|Error invoking remote method|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|[\[{]\s*["'](?:detail|code|error|type|loc)["']|\\[dspw]|api.?key|bearer\s|password|secret|authorization/i
const known: { code: string; pattern: RegExp; message: string }[] = [
  { code: 'uncertain_action', pattern: /write_commands_cannot_be_retried|only_current_unknown|action_resolution|结果(?:仍|尚)?(?:未确认|未知)|已有浏览器操作尚未确认/, message: '这次操作的结果仍需核对。请先查看实际订单、预约或页面记录，不要重复提交。' },
  { code: 'request_conflict', pattern: /request_id_conflicts_with_saved_content|内容指纹不匹配|请求身份.*冲突/, message: '这条输入与原请求记录不一致。请保留草稿，先核对原任务。' },
  { code: 'stale_confirmation', pattern: /stale_|approval_expired|exact_interrupt_id_required|unknown_or_stale_candidate/, message: '方案或确认内容已更新。请先查看最新内容，再决定下一步。' },
  { code: 'busy', pattern: /run_is_busy|run_busy|pending_command/, message: '当前任务还在处理，请稍后查看进展。' },
  { code: 'ambiguous_location', pattern: /ambiguous_location|geocode_ambiguous/, message: '这个地点有多个同名结果。请补充区县、路名或门牌号后再查找。' },
  { code: 'login', pattern: /authentication_required|captcha_required/, message: '请在浏览器中完成登录或页面验证，再点击“已处理，继续”。' },
  { code: 'image', pattern: /image_exceeds|image_requires|invalid_image|image_base64/, message: '这张图片暂时无法读取。请选择 8 MB 以内的 PNG、JPEG 或 WebP 图片。' },
  { code: 'source_changed', pattern: /offer_comparison_unavailable|offer_source|offer_hash/, message: fallback.offer },
  { code: 'connection', pattern: /invalid backend credential|backend not ready|Harness unavailable|fetch failed|ECONN|timeout|timed out|HTTP (?:401|403|502|503|504)/i, message: '暂时连接不上任务服务。请重新连接后查看原任务。' }
]

function textOf(value: unknown): string {
  return typeof value === 'string' ? value : value instanceof Error ? value.message
    : value && typeof value === 'object' && 'message' in value && typeof value.message === 'string' ? value.message : ''
}

export function userMessage(value: unknown, context: MessageContext = 'task'): string {
  const text = textOf(value).replace(/^Error invoking remote method '[^']+':\s*/, '').replace(/^(?:Error|TypeError):\s*/, '').trim()
  if (context === 'offer') return fallback.offer
  const match = known.find(item => item.pattern.test(text))
  if (match?.code === 'connection' && context === 'settings') return '模型连接暂不可用。请在设置中核对模型服务，或稍后再次测试。'
  if (match?.code === 'connection' && context !== 'task') return fallback[context]
  if (match) return match.message
  return text && /[\u3400-\u9fff]/.test(text) && !technical.test(text) && text.length <= 500 ? text : fallback[context]
}

/** Keep ordinary answers and quoted sources intact; filter technical status/error text only. */
export function publicStatus(text: string, context: MessageContext = 'task'): string {
  return /traceback|validation.?error|pydantic|zoderror|input_value|input_type|should match pattern|\b(?:TypeError|ReferenceError|ECONN\w*|ERR_\w+)\b|\bHTTP\s+\d{3}|Error invoking remote method|\b[a-z0-9]+_(?:error|failed|unavailable|mismatch|expired|blocked|not_found)\b|^\s*(?:Error:|\{\s*["']detail["']|\[\s*\{)|^\s*(?:Harness:\s*)?[a-z][a-z0-9]*(?:_[a-z0-9]+)+\s*$|[（(][a-z][a-z0-9]*(?:_[a-z0-9]+)+[）)]/i.test(text)
    ? userMessage(text, context) : text
}

export function deliveryMessage(status: 'not_sent' | 'unconfirmed' | 'accepted' | 'delivered', error?: unknown): string {
  if (status === 'not_sent') return known.find(item => ['request_conflict', 'uncertain_action'].includes(item.code) && item.pattern.test(textOf(error)))?.message
    || '这条消息尚未送达。请保留输入，连接恢复后继续发送原请求。'
  if (status === 'accepted' || status === 'delivered') return '服务已接收这条消息，暂时还取不回结果。请点击“核对送达并取回”继续查看原任务。'
  return '这条消息是否送达还未确认。请点击“核对送达并取回”，继续核对原请求。'
}

/** Diagnostics intentionally omit raw request values, provider bodies and credentials. */
export function errorDiagnostic(value: unknown): { name: string; category: string; httpStatus?: number } {
  const text = textOf(value)
  const name = value instanceof Error && /^[A-Za-z]{0,35}Error$/.test(value.name) ? value.name : 'Error'
  const status = /\bHTTP\s+(\d{3})\b/.exec(text)
  return { name, category: known.find(item => item.pattern.test(text))?.code || (/validation|pydantic|zod|pattern|input_value/i.test(text) ? 'validation' : 'unclassified'),
    ...(status ? { httpStatus: Number(status[1]) } : {}) }
}
