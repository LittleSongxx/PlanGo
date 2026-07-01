// 事件总线：把 Agent 运行过程（透明步骤流）与成果卡片实时推给渲染层。
import { getMainWindow } from '../index'
import { IPC } from '@shared/ipc'
import type { AgentStep, OutcomeCard, StepStatus, SourceTag } from '@shared/types'

let stepSeq = 0

export function emitStep(label: string, status: StepStatus = 'running', detail?: string, source?: SourceTag): string {
  const id = 'step_' + ++stepSeq
  push(IPC.agentStep, { id, label, status, detail, source } as AgentStep)
  return id
}

export function updateStep(id: string, status: StepStatus, detail?: string, source?: SourceTag): void {
  push(IPC.agentStep, { id, label: '', status, detail, source, patch: true })
}

export function emitCard(card: OutcomeCard): void {
  push(IPC.agentStreamCard, card)
}

export function emitProactive(payload: unknown): void {
  push(IPC.proactivePush, payload)
}

export function emitImIncoming(payload: unknown): void {
  push(IPC.imIncoming, payload)
}

function push(channel: string, payload: unknown): void {
  const win = getMainWindow()
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload)
}
