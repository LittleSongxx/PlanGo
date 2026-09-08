import { z } from 'zod'

const id = z.string().min(1).max(200)
export const browserOperations = ['navigate', 'open_tab', 'snapshot', 'read_page', 'extract', 'extract_tables', 'current', 'screenshot', 'click', 'type', 'scroll', 'highlight'] as const
export type BrowserOperation = typeof browserOperations[number]

export interface BrowserCommand {
  command_id: string
  run_id: string
  browser_session_id: string
  tab_id?: string
  operation: BrowserOperation
  arguments: Record<string, unknown>
  expected_snapshot_id?: string
  expires_at?: string
  approved_action_id?: string
}

export interface BrowserObservation {
  command_id: string
  ok: boolean
  outcome: 'observed' | 'executed' | 'blocked' | 'unknown' | 'failed'
  error_kind?: string
  error?: string
  snapshot_id?: string
  page_version?: string
  tab_id?: string
  url?: string
  title?: string
  text?: string
  elements?: { idx: number; tag: string; role: string; name: string; text: string; href?: string }[]
  tables?: { headers: string[]; rows: string[][] }[]
  fields?: Record<string, unknown>
  receipt?: Record<string, unknown>
  screenshot?: ScreenshotEvidence
  interaction_kind?: 'navigation'
}

export interface ScreenshotEvidence {
  screenshot_id: string
  snapshot_id: string
  url: string
  captured_at: string
  viewport: { width: number; height: number }
  image: { width: number; height: number }
  dpr: number
  zoom: number
  clip: { x: number; y: number; width: number; height: number }
  scroll: { x: number; y: number }
  view_bounds: { x: number; y: number; width: number; height: number }
  page_version: string
  data_url: string
}

export const browserCommandSchema = z.object({
  command_id: id,
  run_id: id,
  browser_session_id: id,
  tab_id: id.nullish().transform((value) => value ?? undefined),
  operation: z.enum(browserOperations),
  arguments: z.record(z.unknown()),
  expected_snapshot_id: id.nullish().transform((value) => value ?? undefined),
  expires_at: z.string().datetime({ offset: true }).nullish().transform((value) => value ?? undefined),
  approved_action_id: id.nullish().transform((value) => value ?? undefined)
}).strict()

const index = z.number().int().min(0).max(179)
const empty = z.object({}).strict()
const argumentSchemas: Record<BrowserOperation, z.ZodTypeAny> = {
  navigate: z.object({ url: z.string().min(1).max(8192) }).strict(),
  open_tab: z.object({ url: z.string().min(1).max(8192) }).strict(),
  snapshot: empty, screenshot: empty, read_page: empty, extract: empty, extract_tables: empty, current: empty,
  click: z.object({ idx: index }).strict(),
  type: z.object({ idx: index, text: z.string().max(10000) }).strict(),
  scroll: z.object({ dir: z.enum(['up', 'down']).default('down') }).strict(),
  highlight: z.object({ idx: index }).strict()
}

export function validateBrowserCommand(raw: unknown): BrowserCommand {
  const command = browserCommandSchema.parse(raw)
  command.arguments = argumentSchemas[command.operation].parse(command.arguments)
  if (command.operation === 'navigate' || command.operation === 'open_tab') {
    command.arguments.url = browserUrl(String(command.arguments.url))
  }
  return command
}

export function browserUrl(raw: string): string {
  const value = raw.trim()
  if (!value) throw new Error('缺少网址')
  const candidate = /^[a-z][a-z\d+.-]*:/i.test(value)
    ? value
    : /\.[a-z]{2,}/i.test(value) && !/\s/.test(value)
      ? `https://${value}`
      : `https://www.baidu.com/s?wd=${encodeURIComponent(value)}`
  const url = new URL(candidate)
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('仅允许无内嵌凭据的 HTTP(S) 网址')
  return url.href
}

export function isBrowserWrite(operation: BrowserOperation): boolean {
  // ponytail: unknown page handlers can submit on click/change; allow finer read-only actions after site adapters verify them.
  return operation === 'click' || operation === 'type'
}

export function browserCommandGuard(command: BrowserCommand, now = Date.now()): string | undefined {
  if (command.expires_at && Date.parse(command.expires_at) <= now) return 'command_expired'
  if (isBrowserWrite(command.operation) && !command.approved_action_id) return 'approval_required'
  if (['click', 'type', 'highlight', 'screenshot'].includes(command.operation) && (!command.tab_id || !command.expected_snapshot_id)) return 'snapshot_required'
  if (!command.tab_id && !['navigate', 'open_tab', 'snapshot', 'read_page', 'extract', 'extract_tables', 'current'].includes(command.operation)) return 'tab_required'
}

export function allowedBrowserSite(raw: string): boolean {
  try {
    const url = new URL(raw)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password &&
      ['dianping.com', 'meituan.com', 'amap.com', 'xiaohongshu.com', 'baidu.com', 'douyin.com']
        .some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))
  } catch { return false }
}

export function browserErrorMessage(kind: string): string {
  return ({
    approval_required: '该操作需要具体页面审批。',
    command_expired: '这条操作已过期，请重新读取页面并核对。',
    command_conflict: '操作身份与参数不一致，已阻止执行。',
    invalid_command: '浏览器操作参数无效，未执行。',
    stale_snapshot: '页面或表单已变化，请重新读取并核对。',
    snapshot_required: '需要先读取当前页面，再选择操作目标。',
    run_cancelled: '自动操作已停止，请核对页面后继续。',
    run_superseded: '已切换任务轮次，旧操作不会继续执行。',
    browser_not_visible: '请返回浏览器并关闭遮挡窗口，再继续操作。',
    tab_session_mismatch: '此页面正由另一任务使用，请另开标签页。',
    tab_closed: '浏览器标签页已关闭，请重新打开目标页面。',
    tab_required: '请先打开一个浏览器标签页。',
    browser_timeout: '浏览器步骤超时，请核对页面；未确认的提交不会重试。',
    input_not_applied: '输入没有保持在页面中，请手动核对，系统不会重复输入。',
    navigation_redirected: '页面跳转到了其他地址，请核对目标后继续。',
    manual_input_required: '当前页面需要人工登录、验证或填写敏感信息。',
    site_not_allowed: '此站点暂不支持自动写入，请人工操作。'
  } as Record<string, string>)[kind] || '浏览器步骤未完成，请检查当前页面后继续。'
}
