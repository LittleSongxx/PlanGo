// Main validates and serializes commands; the renderer owns real, persistent webviews.
import { getMainWindow } from './index'
import { IPC } from '@shared/ipc'
import { randomUUID } from 'node:crypto'
import { allowedBrowserSite, browserCommandGuard, isBrowserWrite, validateBrowserCommand, type BrowserCommand, type BrowserObservation, type BrowserOperation } from '@shared/browser'

export type BrowserActionName = Exclude<BrowserOperation, 'snapshot'>
export type BrowserActionResult = Partial<BrowserObservation>
export const isAllowed = allowedBrowserSite
export function isDangerAction(text: string): boolean {
  return /(支付|付款|下单|提交|购买|取号|预约|结算|取消|删除)/.test(text || '')
}

let seq = 0
const pending = new Map<number, { resolve: (r: BrowserActionResult) => void; timer: ReturnType<typeof setTimeout> }>()
// The backend action ledger is authoritative across restarts; this prevents duplicate IPC delivery in one process.
const commands = new Map<string, { fingerprint: string; result: Promise<BrowserObservation> }>()
let queue: Promise<unknown> = Promise.resolve()
const cancelledRuns = new Set<string>()
const runEpochs = new Map<string, number>()

function changeBrowserRun(runId: string, action: 'cancel_run' | 'release_run' | 'activate_run'): void {
  if (action === 'activate_run') cancelledRuns.delete(runId)
  else {
    cancelledRuns.add(runId)
    runEpochs.set(runId, (runEpochs.get(runId) || 0) + 1)
  }
  const win = getMainWindow()
  if (win && !win.isDestroyed()) win.webContents.send(IPC.browserExec, { id: 0, action, args: { run_id: runId, epoch: runEpochs.get(runId) || 0 } })
}

export function cancelBrowserRun(runId: string): void { changeBrowserRun(runId, 'cancel_run') }
export function releaseBrowserRun(runId: string): void { changeBrowserRun(runId, 'release_run') }
export function activateBrowserRun(runId: string): void { changeBrowserRun(runId, 'activate_run') }

function dispatch(command: BrowserCommand, epoch: number): Promise<BrowserActionResult> {
  const win = getMainWindow()
  if (!win || win.isDestroyed()) return Promise.resolve({ ok: false, outcome: 'blocked', error_kind: 'browser_unavailable', error: '没有可用的应用窗口' })
  const id = ++seq
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      pending.delete(id)
      resolve({ ok: false, outcome: isBrowserWrite(command.operation) ? 'unknown' : 'failed', error_kind: 'browser_timeout', error: '浏览器动作超时；写入结果未知，请查询页面，勿重复提交' })
    }, 25_000)
    pending.set(id, { resolve, timer })
    try {
      win.webContents.send(IPC.browserExec, { id, action: 'harness', args: { command, epoch } })
    } catch (error) {
      clearTimeout(timer)
      pending.delete(id)
      resolve({ ok: false, outcome: 'failed', error_kind: 'dispatch_failed', error: (error as Error).message })
    }
  })
}

export async function executeBrowserCommand(raw: BrowserCommand): Promise<BrowserObservation> {
  let command: BrowserCommand
  try { command = validateBrowserCommand(raw) }
  catch (error) {
    return { command_id: typeof raw?.command_id === 'string' ? raw.command_id : '', ok: false, outcome: 'blocked', error_kind: 'invalid_command', error: (error as Error).message }
  }
  const fingerprint = JSON.stringify(command)
  const cached = commands.get(command.command_id)
  if (cached) return cached.fingerprint === fingerprint ? cached.result : { command_id: command.command_id, ok: false, outcome: 'blocked', error_kind: 'command_conflict', error: 'command_id 已关联其他参数' }
  command.expires_at ||= new Date(Date.now() + 25_000).toISOString()
  const epoch = runEpochs.get(command.run_id) || 0
  const run = async (): Promise<BrowserObservation> => {
    if (epoch !== (runEpochs.get(command.run_id) || 0)) return { command_id: command.command_id, ok: false, outcome: 'blocked', error_kind: 'run_superseded', error: '浏览器命令所属轮次已结束' }
    if (cancelledRuns.has(command.run_id)) return { command_id: command.command_id, ok: false, outcome: 'blocked', error_kind: 'run_cancelled', error: '任务已停止' }
    const guard = browserCommandGuard(command)
    if (guard) return { command_id: command.command_id, ok: false, outcome: 'blocked', error_kind: guard, error: guard }
    const result = await dispatch(command, epoch)
    return { ...result, command_id: command.command_id, ok: result.ok === true, outcome: result.outcome || (result.error ? 'failed' : 'observed') }
  }
  const result = queue.then(run, run)
  queue = result.catch(() => {})
  commands.set(command.command_id, { fingerprint, result })
  return result
}

// Legacy read tools keep their signature, but cannot bypass Harness approval or fresh snapshot checks.
let legacyTab: string | undefined
let legacySnapshot: string | undefined
export async function browserAction(action: BrowserActionName, args: Record<string, unknown> = {}): Promise<BrowserActionResult> {
  const { hint: _hint, ...arguments_ } = args
  const result = await executeBrowserCommand({
    command_id: randomUUID(), run_id: 'legacy', browser_session_id: 'legacy', tab_id: legacyTab,
    operation: action, arguments: arguments_, expected_snapshot_id: legacySnapshot,
    expires_at: new Date(Date.now() + 25_000).toISOString()
  })
  if (result.tab_id) legacyTab = result.tab_id
  legacySnapshot = result.snapshot_id
  return result
}

export function resolveBrowserAction(id: number, result: BrowserActionResult): void {
  const entry = pending.get(id)
  if (!entry) return
  pending.delete(id)
  clearTimeout(entry.timer)
  if (!result || typeof result !== 'object' || typeof result.ok !== 'boolean') {
    entry.resolve({ ok: false, outcome: 'unknown', error_kind: 'invalid_observation', error: '浏览器回执格式无效' })
  } else entry.resolve(result)
}
