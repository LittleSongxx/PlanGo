// 会话态：记住上一次方案（供 refine/consensus/批量执行引用）+ 两步确认待办登记表。
import type { Plan, OutcomeCard, SceneDemand } from '@shared/types'

export interface PendingAction {
  token: string
  title: string
  detail: string
  danger: boolean
  run: () => Promise<{ text: string; cards: OutcomeCard[] }>
}

class Session {
  lastPlan?: Plan
  lastDemand?: SceneDemand
  pendingGuideImage?: string // 用户上传的攻略截图（dataURL），供 import_guide 视觉抽取
  private pending = new Map<string, PendingAction>()

  setLastPlan(p: Plan): void {
    this.lastPlan = p
  }

  setLastDemand(d: SceneDemand): void {
    this.lastDemand = d
  }

  registerConfirm(a: Omit<PendingAction, 'token'>): string {
    const token = 'cf_' + Math.random().toString(36).slice(2, 10)
    this.pending.set(token, { ...a, token })
    return token
  }

  getConfirm(token: string): PendingAction | undefined {
    return this.pending.get(token)
  }

  consumeConfirm(token: string): PendingAction | undefined {
    const a = this.pending.get(token)
    if (a) this.pending.delete(token)
    return a
  }
}

export const session = new Session()
