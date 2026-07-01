// 主动关心引擎（借 cyberboss 机制，换本地生活内核）：
// 随机/定时唤醒(Stochastic Pulse，按 DPVP 时段) + 提醒队列(给未来的自己留提醒) + 本地生活触发话术。
// opt-in、本地、不碰隐私。现场可手动触发一条演示。
import { emitProactive } from './agent/bus'
import { getProfile } from './brain/memory'
import { getWeChatBridge } from './im/bridge'

export interface Reminder {
  id: string
  at: number // 时间戳
  text: string
  fired: boolean
}

export interface ProactiveMessage {
  id: string
  ts: number
  text: string
  kind: 'care' | 'reminder' | 'deal'
}

class ProactiveEngine {
  private reminders: Reminder[] = []
  private history: ProactiveMessage[] = []
  private timer: NodeJS.Timeout | null = null
  private enabled = false

  start(): void {
    if (this.timer) return
    this.enabled = true
    // 每 60s 一次脉冲：按时段 + 随机性决定是否发声
    this.timer = setInterval(() => this.pulse(), 60_000)
  }

  stop(): void {
    this.enabled = false
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  }

  // 提醒队列：小悠给"未来的自己"留提醒
  addReminder(text: string, at: number): Reminder {
    const r: Reminder = { id: 'rm_' + Math.random().toString(36).slice(2, 8), at, text, fired: false }
    this.reminders.push(r)
    return r
  }

  // 到点/出发提醒：取号或预约后，隔一小段就"催你出发"（现场可见地触发，助理化文案）。
  scheduleDeparture(destName: string, aheadOrEtaMin?: number): Reminder {
    const tail = aheadOrEtaMin ? `前面大概还剩几桌，路上约 ${aheadOrEtaMin} 分钟` : '差不多该动身了'
    return this.addReminder(`该出发去「${destName}」啦～${tail}，我帮你把路线也备好了，点开就能看。`, Date.now() + 45_000)
  }

  list(): { reminders: Reminder[]; history: ProactiveMessage[] } {
    return { reminders: this.reminders, history: this.history }
  }

  private pulse(): void {
    if (!this.enabled) return
    const now = Date.now()
    // 1) 先看到期提醒
    for (const r of this.reminders) {
      if (!r.fired && r.at <= now) {
        r.fired = true
        this.emit(r.text, 'reminder')
        return
      }
    }
    // 2) DPVP 时段 + 随机脉冲：只在"合适时机"、以较低概率发声，避免打扰
    const hour = new Date().getHours()
    const inSlot = (hour >= 11 && hour <= 13) || (hour >= 17 && hour <= 20) || (hour >= 9 && hour <= 10)
    if (inSlot && Math.random() < 0.15) {
      const text = this.pickCareLine()
      if (text) this.emit(text, 'care')
    }
  }

  // 本地生活触发话术（只用本地行为数据 + 记忆，助理口吻，越用越懂）
  private pickCareLine(): string {
    const prof = getProfile()
    const day = new Date().getDay()
    const hour = new Date().getHours()
    const lines: string[] = []
    if (day === 5 || day === 6) lines.push('周末到啦～要不要我先按你的习惯排个下午的安排？热门店我顺手帮你取号，免得排队。')
    if (prof.favorite_shops.length) lines.push(`记得你挺喜欢「${prof.favorite_shops[prof.favorite_shops.length - 1]}」，这周要不要再约一次？我顺手比个价、看有没有团购。`)
    const strong = prof.preferences.filter((c) => c.strength >= 0.5 && c.polarity === 'positive')[0]
    if (strong) lines.push(`我记得你${strong.text}，刚好附近有合适的，要不要我排一套？`)
    if (hour >= 11 && hour <= 13) lines.push('这个点该吃午饭了，不想出门的话我可以按你口味配份外卖，送到公司？')
    if (hour >= 17 && hour <= 20) lines.push('快到饭点啦，要不要我就近排个晚餐？清淡/热闹你说一声。')
    lines.push('周末天气看着不错，带家人出去走走？我可以顺路把餐厅和路线都排好。')
    return lines[Math.floor(Math.random() * lines.length)]
  }

  private emit(text: string, kind: ProactiveMessage['kind']): void {
    const msg: ProactiveMessage = { id: 'pm_' + Math.random().toString(36).slice(2, 8), ts: Date.now(), text, kind }
    this.history.push(msg)
    emitProactive(msg)
    // 24h 陪伴：主动关心同步推送到微信（连接后即真正送达，未连接则本地记录）。
    // 这样即使人不在电脑前，小悠也能在微信里惦记你。
    try {
      getWeChatBridge().pushProactive(text)
    } catch {
      /* 微信未连不影响本地 */
    }
  }

  // 供演示/IPC：立刻发一条主动关心
  triggerNow(): ProactiveMessage {
    const text = this.pickCareLine()
    const msg: ProactiveMessage = { id: 'pm_' + Math.random().toString(36).slice(2, 8), ts: Date.now(), text, kind: 'care' }
    this.history.push(msg)
    emitProactive(msg)
    return msg
  }
}

export const proactive = new ProactiveEngine()
