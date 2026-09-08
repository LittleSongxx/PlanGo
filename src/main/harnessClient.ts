import { mkdirSync, readFileSync, existsSync, openSync, writeFileSync, fsyncSync, closeSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import type { BrowserCommand, BrowserObservation } from '../shared/browser'
import type { HarnessEvent, HarnessSnapshot, HarnessFeedbackInput, HarnessFeedbackReply } from '../shared/types'
import type { LocationContext, SelectedPoi } from '../shared/location'

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
  location?: () => LocationContext
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
  private eventLogs = new Map<string, HarnessEvent[]>()
  private snapshots = new Map<string, HarnessSnapshot>()
  private streams = new Map<string, Promise<void>>()
  private disconnectedRuns = new Set<string>()
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

  async createRun(text: string, image?: string, selectedPoi?: SelectedPoi): Promise<HarnessSnapshot> {
    const result = await this.request<{ run_id: string }>('/api/v1/runs', 'POST', {
      user_id: 'desktop', input_text: text, browser_session_id: this.options.browserSessionId,
      enabled_skills: this.options.enabledSkills?.(), location_context: this.options.location?.(), ...(image ? { image } : {}), ...(selectedPoi ? { selected_poi: selectedPoi } : {})
    })
    return this.getRun(result.run_id)
  }

  async getRun(runId: string): Promise<HarnessSnapshot> {
    const result = await this.request<HarnessSnapshot>(`/api/v1/runs/${encodeURIComponent(runId)}`)
    // Historical recovery and live subscriptions share one cursor and event log.
    await this.catchUpEvents(runId, result.event_seq)
    const snapshot = this.publishSnapshot(result)
    if (!this.complete(snapshot)) this.watch(runId)
    return snapshot
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

  feedback(runId: string, value: HarnessFeedbackInput): Promise<HarnessFeedbackReply> {
    return this.request(`/api/v1/runs/${encodeURIComponent(runId)}/feedback`, 'POST', value)
  }

  async resumePreparation(runId: string, planId: string, planVersion: number, approvalId: string): Promise<HarnessSnapshot> {
    return this.withIntent(runId, true, async () => {
      await this.request(`/api/v1/runs/${encodeURIComponent(runId)}/preparation/resume`, 'POST', { plan_id: planId, plan_version: planVersion, approval_id: approvalId })
      return this.getRun(runId)
    })
  }

  async decideDraft(runId: string, interruptId: string, planId: string, planVersion: number, decision: 'save' | 'prepare'): Promise<HarnessSnapshot> {
    return this.withIntent(runId, decision === 'prepare', async () => {
      await this.request(`/api/v1/runs/${encodeURIComponent(runId)}/draft-decision`, 'POST', { decision, interrupt_id: interruptId, plan_id: planId, plan_version: planVersion })
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

  private acceptEvents(runId: string, events: HarnessEvent[]): void {
    const log = this.eventLogs.get(runId) || []
    this.eventLogs.set(runId, log) // A later gap/error must retain the already-accepted prefix.
    for (const event of events) {
      if (!Number.isSafeInteger(event.seq) || event.seq <= (this.watchers.get(runId) || 0)) continue
      if (event.run_id && event.run_id !== runId) throw new Error('Run event identity mismatch')
      if (event.seq !== (this.watchers.get(runId) || 0) + 1) throw new Error('Run event gap; reconnecting from the last complete cursor')
      log.push({ ...event, run_id: runId })
      this.watchers.set(runId, event.seq)
      this.options.emit({ ...event, run_id: runId })
    }
  }

  private async catchUpEvents(runId: string, target: number): Promise<void> {
    while (!this.stopped && (this.watchers.get(runId) || 0) < target) {
      const cursor = this.watchers.get(runId) || 0
      const result = await this.events(runId, cursor)
      this.acceptEvents(runId, result.events || [])
      if ((this.watchers.get(runId) || 0) === cursor) break // An event transaction may still be committing.
    }
  }

  private publishSnapshot(value: HarnessSnapshot): HarnessSnapshot {
    const previous = this.snapshots.get(value.run_id)
    const stale = previous && ((value.version ?? 0) < (previous.version ?? 0) || (value.version === previous.version && value.event_seq < previous.event_seq))
    const snapshot = { ...(stale ? previous : value), events: [...(this.eventLogs.get(value.run_id) || [])] }
    this.snapshots.set(value.run_id, snapshot)
    const recovered = this.disconnectedRuns.delete(value.run_id)
    if (recovered || !previous || JSON.stringify(previous) !== JSON.stringify(snapshot)) this.options.emit({ run_id: value.run_id, seq: 0, event_type: 'snapshot', payload: { snapshot } })
    if (this.complete(snapshot)) this.observeSnapshot(snapshot)
    return snapshot
  }

  private complete(snapshot: HarnessSnapshot): boolean {
    return this.terminal(snapshot) && !this.userIntents.has(snapshot.run_id) && (this.watchers.get(snapshot.run_id) || 0) >= snapshot.event_seq
  }

  private watch(runId: string): void {
    if (this.streams.has(runId) || this.stopped) return
    const pending = this.watchEvents(runId).finally(() => {
      this.streams.delete(runId)
      const latest = this.snapshots.get(runId)
      if (!this.stopped && latest && !this.complete(latest)) this.watch(runId)
    })
    this.streams.set(runId, pending)
  }

  private async watchEvents(runId: string): Promise<void> {
    while (!this.stopped) {
      const connection = new AbortController()
      let heartbeat: ReturnType<typeof setTimeout> | undefined
      const armHeartbeat = () => { clearTimeout(heartbeat); heartbeat = setTimeout(() => connection.abort(), 25_000) }
      try {
        armHeartbeat()
        const response = await fetch(`${this.options.baseURL.replace(/\/$/, '')}/api/v1/runs/${encodeURIComponent(runId)}/events?after=${this.watchers.get(runId) || 0}`, {
          headers: { Authorization: `Bearer ${this.options.token}`, Accept: 'text/event-stream', 'Last-Event-ID': String(this.watchers.get(runId) || 0) },
          signal: AbortSignal.any([this.abort.signal, connection.signal])
        })
        if (!response.ok) throw new Error(`Run event stream HTTP ${response.status}`)
        const refresh = async () => {
          const value = await this.request<HarnessSnapshot>(`/api/v1/runs/${encodeURIComponent(runId)}`)
          return this.complete(this.publishSnapshot(value))
        }
        if (!response.headers.get('content-type')?.includes('text/event-stream')) {
          // Compatibility with older endpoints; polling still has one main-process owner.
          const result = await response.json() as { events: HarnessEvent[] }
          this.acceptEvents(runId, result.events || [])
          if (await refresh()) return
        } else {
          if (!response.body) throw new Error('Missing run event stream')
          const reader = response.body.getReader(), decoder = new TextDecoder()
          let buffer = '', lastSnapshot = 0
          try {
            while (!this.stopped) {
              const chunk = await reader.read()
              if (chunk.done) break
              armHeartbeat()
              buffer = (buffer + decoder.decode(chunk.value, { stream: true })).replace(/\r\n/g, '\n')
              if (buffer.length > 1_000_000) throw new Error('Run event frame exceeds limit')
              let boundary: number, changed = false
              while ((boundary = buffer.indexOf('\n\n')) >= 0) {
                const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
                const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
                if (!data) continue
                const event = JSON.parse(data) as HarnessEvent
                const eventId = frame.split('\n').find(line => line.startsWith('id:'))?.slice(3).trim()
                if (eventId && eventId !== String(event.seq)) throw new Error('Run event cursor mismatch')
                this.acceptEvents(runId, [event]); changed = true
              }
              // Heartbeats also cover a state commit just after its event became visible.
              if (changed || Date.now() - lastSnapshot >= 2000) {
                if (await refresh()) return
                lastSnapshot = Date.now()
              }
            }
          } finally { await reader.cancel().catch(() => {}) }
        }
      } catch (error) {
        if (this.stopped) return
        this.disconnectedRuns.add(runId)
        this.options.emit({ run_id: runId, seq: 0, event_type: 'connection_error', payload: { message: (error as Error).message } })
      } finally { clearTimeout(heartbeat); connection.abort() }
      await this.delay(750)
    }
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
    this.abort.abort()
  }

  async close(): Promise<void> {
    this.stop()
    await Promise.all([this.polling, this.reminderPolling, ...this.streams.values()])
  }
}
