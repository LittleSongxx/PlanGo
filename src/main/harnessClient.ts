import { mkdirSync, readFileSync, existsSync, openSync, writeFileSync, fsyncSync, closeSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import type { BrowserCommand, BrowserObservation } from '../shared/browser'
import type { HarnessEvent, HarnessSnapshot } from '../shared/types'

interface Options {
  baseURL: string
  token: string
  browserSessionId: string
  dataDir: string
  execute: (command: BrowserCommand) => Promise<BrowserObservation>
  emit: (event: HarnessEvent) => void
  onTerminal?: (runId: string) => void
  onActivate?: (runId: string, supersede: boolean) => void
  enabledSkills?: () => string[]
  location?: () => { city: string; longitude?: number; latitude?: number; source: 'config' | 'manual' | 'device' }
  remind?: (notification: { id: string; text: string; ts: number; kind: 'reminder' }) => boolean
}

interface Receipt {
  command: BrowserCommand
  result?: BrowserObservation
  delivered?: boolean
}

/** Transport retries replay receipts, never browser side effects. */
export class HarnessClient {
  private stopped = false
  private watchers = new Map<string, number>()
  private receipts = new Map<string, Receipt>()
  private browserCursor = 0
  private polling?: Promise<void>
  private reminderPolling?: Promise<void>
  private notified = new Set<string>()
  private readonly journalPath: string
  private abort = new AbortController()
  private userIntents = new Set<string>()
  private released = new Map<string, string>()

  constructor(private options: Options) {
    const url = new URL(options.baseURL)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Invalid Harness URL')
    if (!options.token) throw new Error('Missing Harness authentication')
    mkdirSync(options.dataDir, { recursive: true, mode: 0o700 })
    this.journalPath = join(options.dataDir, 'browser-receipts.json')
    if (existsSync(this.journalPath)) {
      // A corrupt journal must not silently erase evidence of a submitted action.
      const rows: [string, Receipt][] = JSON.parse(readFileSync(this.journalPath, 'utf8'))
      if (!Array.isArray(rows)) throw new Error('Invalid browser receipt journal')
      this.receipts = new Map(rows)
    }
  }

  async request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
    if (this.stopped) throw new Error('Harness connection closed')
    const response = await fetch(this.options.baseURL.replace(/\/$/, '') + path, {
      method,
      headers: { Authorization: `Bearer ${this.options.token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.any([this.abort.signal, AbortSignal.timeout(15_000)])
    })
    let data: any
    try { data = await response.json() } catch { throw new Error(`Harness HTTP ${response.status}`) }
    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.error === 'string' ? data.error : `HTTP ${response.status}`
      throw new Error(`Harness: ${detail.slice(0, 240)}`)
    }
    return data as T
  }

  async status(): Promise<{ ready: boolean; error?: string }> {
    try {
      const result = await this.request<Record<string, unknown>>('/api/v1/health/ready')
      return { ready: result.ready !== false }
    } catch (error) { return { ready: false, error: (error as Error).message } }
  }

  async createRun(text: string, image?: string): Promise<HarnessSnapshot> {
    const result = await this.request<{ run_id: string }>('/api/v1/runs', 'POST', {
      user_id: 'desktop', input_text: text, browser_session_id: this.options.browserSessionId,
      enabled_skills: this.options.enabledSkills?.(), location_context: this.options.location?.(), ...(image ? { image } : {})
    })
    return this.getRun(result.run_id)
  }

  async getRun(runId: string): Promise<HarnessSnapshot> {
    const result = await this.request<HarnessSnapshot>(`/api/v1/runs/${encodeURIComponent(runId)}`)
    this.observeSnapshot(result)
    this.watch(runId)
    return result
  }

  async listRuns(): Promise<HarnessSnapshot[]> {
    const result = await this.request<HarnessSnapshot[] | { runs: HarnessSnapshot[] }>('/api/v1/runs?user_id=desktop')
    return Array.isArray(result) ? result : result.runs
  }

  async mutate(runId: string, operation: 'messages' | 'replan' | 'cancel', body: unknown): Promise<HarnessSnapshot> {
    return this.withIntent(runId, operation !== 'cancel', async () => {
      await this.request(`/api/v1/runs/${encodeURIComponent(runId)}/${operation}`, 'POST', { ...(body as Record<string, unknown>), ...(operation === 'cancel' ? {} : { location_context: this.options.location?.() }) })
      return this.getRun(runId)
    }, operation === 'cancel')
  }

  async resume(runId: string, interruptId: string, decision: string, text = ''): Promise<HarnessSnapshot> {
    return this.withIntent(runId, false, async () => {
      await this.request(`/api/v1/runs/${encodeURIComponent(runId)}/interrupts/${encodeURIComponent(interruptId)}/resume`, 'POST', { decision, text })
      return this.getRun(runId)
    })
  }

  async selectPlan(runId: string, planId: string, planVersion: number): Promise<HarnessSnapshot> {
    return this.withIntent(runId, true, async () => {
      await this.request(`/api/v1/runs/${encodeURIComponent(runId)}/plans/select`, 'POST', { plan_id: planId, plan_version: planVersion })
      return this.getRun(runId)
    })
  }

  async resolveAction(runId: string, actionId: string, status: string, note: string, reference?: string): Promise<HarnessSnapshot> {
    await this.request(`/api/v1/runs/${encodeURIComponent(runId)}/actions/${encodeURIComponent(actionId)}/resolve`, 'POST', { status, note, reference })
    return this.getRun(runId)
  }

  private async withIntent<T>(runId: string, supersede: boolean, action: () => Promise<T>, cancel = false): Promise<T> {
    this.userIntents.add(runId)
    if (!cancel) this.options.onActivate?.(runId, supersede)
    try { return await action() } finally { this.userIntents.delete(runId) }
  }

  private observeSnapshot(snapshot: HarnessSnapshot): void {
    if (!this.terminal(snapshot) || this.userIntents.has(snapshot.run_id)) return
    const version = `${snapshot.version}:${snapshot.event_seq}:${snapshot.phase}`
    if (this.released.get(snapshot.run_id) === version) return
    this.released.set(snapshot.run_id, version)
    this.options.onTerminal?.(snapshot.run_id)
  }

  private terminal(snapshot: HarnessSnapshot): boolean {
    return ['SUCCEEDED', 'FAILED', 'CANCELLED', 'INFEASIBLE', 'PARTIAL_FAILED'].includes(snapshot.phase)
  }

  events(runId: string, after: number): Promise<{ events: HarnessEvent[] }> {
    return this.request(`/api/v1/runs/${encodeURIComponent(runId)}/events?after=${Math.max(0, after)}`)
  }

  private watch(runId: string): void {
    if (this.watchers.has(runId) || this.stopped) return
    this.watchers.set(runId, 0)
    void (async () => {
      while (!this.stopped && this.watchers.has(runId)) {
        try {
          const result = await this.events(runId, this.watchers.get(runId) || 0)
          for (const event of result.events || []) {
            if (event.seq <= (this.watchers.get(runId) || 0)) continue
            this.options.emit({ ...event, run_id: runId })
            this.watchers.set(runId, event.seq)
          }
          const snapshot = await this.request<HarnessSnapshot>(`/api/v1/runs/${encodeURIComponent(runId)}`)
          if (this.terminal(snapshot) && !this.userIntents.has(runId)) { this.observeSnapshot(snapshot); break }
        } catch (error) {
          if (this.stopped) break
          this.options.emit({ run_id: runId, seq: 0, event_type: 'connection_error', payload: { message: (error as Error).message } })
        }
        await this.delay(750)
      }
      this.watchers.delete(runId)
    })()
  }

  startBrowserPolling(): void {
    if (!this.polling) this.polling = this.pollBrowser()
    if (this.options.remind && !this.reminderPolling) this.reminderPolling = this.pollReminders()
  }

  private async pollReminders(): Promise<void> {
    while (!this.stopped) {
      try {
        const due = await this.request<{ reminders: { id: string; text: string; at: number }[] }>('/api/v1/reminders/due')
        for (const reminder of due.reminders) {
          if (this.stopped) break
          if (!this.notified.has(reminder.id)) {
            if (!this.options.remind?.({ id: reminder.id, text: reminder.text, ts: Date.now(), kind: 'reminder' })) continue
            this.notified.add(reminder.id)
          }
          await this.request(`/api/v1/reminders/${encodeURIComponent(reminder.id)}/ack`, 'POST', {})
        }
      } catch { /* durable due rows remain available after reconnect */ }
      await this.delay(10_000)
    }
  }

  private async pollBrowser(): Promise<void> {
    while (!this.stopped) {
      try {
        // Unacknowledged results survive transport failure and desktop restart.
        for (const [id, receipt] of this.receipts) {
          if (!receipt.delivered) {
            try { await this.deliver(id, receipt) } catch { /* keep pending; another run can still make progress */ }
          }
        }
        const result = await this.request<{ commands: BrowserCommand[]; cursor: number }>(
          `/api/v1/browser/commands?browser_session_id=${encodeURIComponent(this.options.browserSessionId)}&after=${this.browserCursor}`
        )
        for (const command of result.commands || []) {
          if (this.stopped) break
          if (command.browser_session_id !== this.options.browserSessionId) throw new Error('Browser session mismatch')
          let receipt = this.receipts.get(command.command_id)
          if (receipt && JSON.stringify(receipt.command) !== JSON.stringify(command)) {
            this.options.emit({ run_id: command.run_id, seq: 0, event_type: 'connection_error', payload: { message: 'Browser command identity conflict; execution blocked' } })
            continue
          }
          if (!receipt) {
            receipt = { command }
            this.receipts.set(command.command_id, receipt)
            this.persist()
            try { receipt.result = await this.options.execute(command) }
            catch { receipt.result = { command_id: command.command_id, ok: false, outcome: 'unknown', error_kind: 'executor_interrupted' } }
            this.persist()
          }
          try { await this.deliver(command.command_id, receipt) } catch { /* retry the persisted receipt next poll */ }
        }
        if (Number.isSafeInteger(result.cursor)) this.browserCursor = result.cursor
      } catch {
        // Receipt state is durable. A failed connection must never trigger a fresh submit.
      }
      await this.delay(750)
    }
  }

  private async deliver(id: string, receipt: Receipt): Promise<void> {
    if (!receipt.result) {
      receipt.result = { command_id: id, ok: false, outcome: 'unknown', error_kind: 'desktop_restarted_during_command' }
      this.persist()
    }
    await this.request(`/api/v1/browser/commands/${encodeURIComponent(id)}/result`, 'POST', {
      ...receipt.result, browser_session_id: this.options.browserSessionId
    })
    receipt.delivered = true
    this.persist()
  }

  private persist(): void {
    // ponytail: retain the latest 200 delivered receipts; pending commands are never pruned.
    const delivered = [...this.receipts].filter(([, value]) => value.delivered)
    for (const [id] of delivered.slice(0, Math.max(0, delivered.length - 200))) this.receipts.delete(id)
    const temp = this.journalPath + '.tmp'
    const fd = openSync(temp, 'w', 0o600)
    try { writeFileSync(fd, JSON.stringify([...this.receipts])); fsyncSync(fd) } finally { closeSync(fd) }
    renameSync(temp, this.journalPath)
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => {
      if (this.stopped) return resolve()
      const finish = () => { clearTimeout(timer); this.abort.signal.removeEventListener('abort', finish); resolve() }
      const timer = setTimeout(finish, ms)
      this.abort.signal.addEventListener('abort', finish, { once: true })
    })
  }

  stop(): void {
    this.stopped = true
    this.watchers.clear()
    this.abort.abort()
  }

  async close(): Promise<void> {
    this.stop()
    await Promise.all([this.polling, this.reminderPolling])
  }
}
