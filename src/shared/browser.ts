import { z } from 'zod'

const id = z.string().min(1).max(200)
export const browserOperations = ['navigate', 'open_tab', 'snapshot', 'read_page', 'extract', 'extract_tables', 'current', 'click', 'type', 'scroll', 'highlight'] as const
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
  tab_id?: string
  url?: string
  title?: string
  text?: string
  elements?: { idx: number; tag: string; role: string; name: string; text: string }[]
  tables?: { headers: string[]; rows: string[][] }[]
  fields?: Record<string, unknown>
  receipt?: Record<string, unknown>
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
  snapshot: empty, read_page: empty, extract: empty, extract_tables: empty, current: empty,
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
  if (['click', 'type', 'highlight'].includes(command.operation) && (!command.tab_id || !command.expected_snapshot_id)) return 'snapshot_required'
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
