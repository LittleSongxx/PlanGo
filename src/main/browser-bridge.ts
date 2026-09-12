// Only the trusted main process binds durable Harness commands to visible browser contents.
import { allowedBrowserSite, browserErrorMessage, browserCommandGuard, isBrowserWrite, validateBrowserCommand, type BrowserCommand, type BrowserObservation } from '@shared/browser'
import { createHash } from 'node:crypto'
import { executeBrowserOperation, captureScreenshot } from './browserDriver'
import { activateBrowserTab, createBrowserTab, getActiveBrowserTab, getBrowserTab, getBrowserTabSignal, isBrowserTabReady, isBrowserTabVisible, loadBrowserURL, notifyBrowserActivity, onBrowserPopup } from './browserView'

export type BrowserActionResult = Partial<BrowserObservation>
const commands = new Map<string, { fingerprint: string; result?: Promise<BrowserObservation> }>()
// ponytail: keep at most 10,000 process-local identities; persist an indexed tombstone ledger if longer sessions are needed. Never evict an identity and replay a write.
const maxCommandIdentities = 10_000
const bindings = new Map<string, string>()
const tabOwners = new Map<string, string>()
const cancelledRuns = new Set<string>()
const runEpochs = new Map<string, number>()
const aborts = new Map<string, AbortController>()
let queue: Promise<unknown> = Promise.resolve()

// Inherit ownership synchronously before the view manager publishes the new popup.
onBrowserPopup((parentId, childId) => {
  const owner = tabOwners.get(parentId)
  if (owner) { tabOwners.set(childId, owner); bindings.set(owner, childId) }
})

export function cancelBrowserRun(runId: string): void {
  cancelledRuns.add(runId)
  runEpochs.set(runId, (runEpochs.get(runId) || 0) + 1)
  aborts.get(runId)?.abort(new Error('run_cancelled'))
  aborts.delete(runId)
}

export function activateBrowserRun(runId: string): void { cancelledRuns.delete(runId) }

export function releaseBrowserRun(runId: string): void {
  cancelBrowserRun(runId)
  for (const [tabId, owner] of [...tabOwners]) {
    if (JSON.parse(owner)[1] === runId) {
      tabOwners.delete(tabId)
      bindings.delete(owner)
    }
  }
}

export function releaseOtherBrowserRuns(exceptRunId: string): void {
  const released = new Set<string>()
  for (const [tabId, owner] of [...tabOwners]) {
    const runId = JSON.parse(owner)[1]
    if (runId === exceptRunId) continue
    tabOwners.delete(tabId)
    bindings.delete(owner)
    released.add(runId)
  }
  for (const owner of [...bindings.keys()]) {
    const runId = JSON.parse(owner)[1]
    if (runId === exceptRunId) continue
    bindings.delete(owner)
    released.add(runId)
  }
  for (const runId of released) cancelBrowserRun(runId)
}

export function cancelBrowserTab(tabId: string): void {
  const owner = tabOwners.get(tabId)
  if (owner) cancelBrowserRun(JSON.parse(owner)[1])
}

function failure(commandId: string, kind: string, outcome: BrowserObservation['outcome'] = 'blocked'): BrowserObservation {
  return { command_id: commandId, ok: false, outcome, error_kind: kind, error: browserErrorMessage(kind) }
}

const commandFingerprint = (command: BrowserCommand): string => createHash('sha256').update(JSON.stringify(command)).digest('hex')

export function acknowledgeBrowserCommand(raw: BrowserCommand): void {
  const command = validateBrowserCommand(raw)
  const saved = commands.get(command.command_id)
  // The durable transport keeps the full receipt. Drop large observations only after its acknowledgement is safely recorded.
  if (saved?.fingerprint === commandFingerprint(command)) delete saved.result
}

export async function executeBrowserCommand(raw: BrowserCommand): Promise<BrowserObservation> {
  let command: BrowserCommand
  try { command = validateBrowserCommand(raw) }
  catch { return failure(typeof raw?.command_id === 'string' ? raw.command_id : '', 'invalid_command') }
  const fingerprint = commandFingerprint(command)
  const cached = commands.get(command.command_id)
  if (cached) return cached.fingerprint === fingerprint ? cached.result || failure(command.command_id, 'command_already_delivered') : failure(command.command_id, 'command_conflict')
  if (commands.size >= maxCommandIdentities) return failure(command.command_id, 'browser_command_capacity')
  command.expires_at ||= new Date(Date.now() + 25_000).toISOString()
  const epoch = runEpochs.get(command.run_id) || 0
  const run = async (): Promise<BrowserObservation> => {
    let dispatched = false
    let tabId = command.tab_id
    const controller = aborts.get(command.run_id) || new AbortController()
    aborts.set(command.run_id, controller)
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(25_000)])
    const check = (): void => {
      if (epoch !== (runEpochs.get(command.run_id) || 0)) throw new Error('run_superseded')
      if (cancelledRuns.has(command.run_id)) throw new Error('run_cancelled')
      if (signal.aborted) throw new Error('browser_timeout')
      const guard = browserCommandGuard(command)
      if (guard) throw new Error(guard)
    }
    try {
      check()
      const owner = JSON.stringify([command.browser_session_id, command.run_id])
      tabId ||= bindings.get(owner)
      if (command.operation === 'open_tab') tabId = undefined
      if (!tabId && ['navigate', 'open_tab'].includes(command.operation)) {
        tabId = (await createBrowserTab(String(command.arguments.url), signal)).id
      } else tabId ||= getActiveBrowserTab()?.id
      check()
      if (!tabId) return failure(command.command_id, 'tab_required')
      if (tabOwners.has(tabId) && tabOwners.get(tabId) !== owner) return failure(command.command_id, 'tab_session_mismatch')
      const contents = getBrowserTab(tabId)
      if (!contents || contents.isDestroyed()) return failure(command.command_id, 'tab_closed')
      bindings.set(owner, tabId)
      tabOwners.set(tabId, owner)
      notifyBrowserActivity({ active: true, action: command.operation, site: contents.getTitle() || new URL(contents.getURL()).hostname })
      activateBrowserTab(tabId)
      while (!isBrowserTabReady(tabId)) {
        check()
        await new Promise(resolve => setTimeout(resolve, 50))
      }
      check()
      if (isBrowserWrite(command.operation) && !allowedBrowserSite(contents.getURL())) return failure(command.command_id, 'site_not_allowed')
      const visibleSignal = AbortSignal.any([signal, getBrowserTabSignal(tabId)])
      const context = { signal: visibleSignal, check: (): void => {
        check()
        if (visibleSignal.aborted) throw new Error('browser_not_visible')
        if (contents.isDestroyed() || getBrowserTab(tabId!) !== contents) throw new Error('tab_closed')
        if (!isBrowserTabVisible(tabId!)) throw new Error('browser_not_visible')
      }, owner, epoch }
      let result: Partial<BrowserObservation>
      if (command.operation === 'navigate' || command.operation === 'open_tab') {
        if (contents.getURL() !== command.arguments.url) await loadBrowserURL(contents, String(command.arguments.url), visibleSignal)
        result = { ok: true, outcome: 'observed', url: contents.getURL(), title: contents.getTitle() }
      } else {
        dispatched = true
        result = command.operation === 'screenshot'
          ? await captureScreenshot(contents, command, context)
          : await executeBrowserOperation(contents, command, context)
      }
      return { ...result, ...(!result.ok ? { error: browserErrorMessage(result.error_kind || 'browser_execution_failed') } : {}), command_id: command.command_id, tab_id: tabId, ok: result.ok === true, outcome: result.outcome || 'failed' }
    } catch (error) {
      const kind = error instanceof Error ? error.message : 'browser_execution_failed'
      return { ...failure(command.command_id, kind, dispatched && isBrowserWrite(command.operation) ? 'unknown' : 'blocked'), tab_id: tabId }
    } finally { notifyBrowserActivity({ active: false }) }
  }
  const result = queue.then(run, run)
  queue = result.catch(() => {})
  commands.set(command.command_id, { fingerprint, result })
  return result
}
