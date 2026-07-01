// IM Bridge 抽象层：onMessage / sendCard / pushProactive。微信优先（cyberboss 同款 iLink Bot 路径）。
// 现场不强求真连；留好核心接口 + 扫码登录占位 + 可注入模拟入站消息用于演示。
import QRCode from 'qrcode'
import { emitImIncoming } from '../agent/bus'

export interface ImMessage {
  from: string
  text: string
  ts: number
}

export interface ImBridge {
  readonly channel: 'wechat' | 'feishu'
  status(): { connected: boolean; note: string }
  loginQr(): Promise<{ dataUrl: string; note: string }>
  sendCard(to: string, text: string): Promise<{ ok: boolean }>
  pushProactive(text: string): Promise<{ ok: boolean }>
  onMessage(cb: (m: ImMessage) => void): void
}

// 微信 Bridge —— 走 cyberboss 同款 iLink Bot API(ilinkai.weixin.qq.com) 长轮询 + 扫码登录。
// 说明：真实长连接需企业机器人凭证，此处保留接口与扫码占位，未配置时优雅降级。
export class WeChatBridge implements ImBridge {
  readonly channel = 'wechat' as const
  private connected = false
  private cb: ((m: ImMessage) => void) | null = null

  status() {
    return {
      connected: this.connected,
      note: this.connected
        ? '微信已连接（iLink Bot 长轮询）· 小悠 24h 在线，随时收你消息、主动惦记你'
        : '未连接：接口已就绪，扫码后小悠就能在你微信里 24h 收发消息、主动关心你。现场演示可用「模拟入站」。'
    }
  }

  async loginQr() {
    // 真实实现：调用 iLink 获取登录二维码 URL；这里生成占位二维码，指向登录说明。
    const payload = 'xiaonian-wechat-login://ilinkai.weixin.qq.com/bot/login?ts=' + Date.now()
    const dataUrl = await QRCode.toDataURL(payload, { margin: 1, width: 220 })
    return { dataUrl, note: '用微信扫码登录机器人（演示占位）。真实接入 iLink Bot 后即可收发。' }
  }

  async sendCard(to: string, text: string) {
    // 真实实现：POST 到 iLink 发送消息接口。未连接则本地记录。
    console.log(`[wechat] → ${to}: ${text}`)
    return { ok: this.connected }
  }

  async pushProactive(text: string) {
    console.log(`[wechat] 主动推送: ${text}`)
    return { ok: this.connected }
  }

  onMessage(cb: (m: ImMessage) => void) {
    this.cb = cb
  }

  // 供演示：注入一条模拟入站消息（现场无需真连即可跑通"微信发消息→小悠处理"闭环）
  simulateIncoming(text: string, from = '老婆') {
    const m: ImMessage = { from, text, ts: Date.now() }
    this.cb?.(m)
    emitImIncoming(m)
  }
}

let wechat: WeChatBridge | null = null
export function getWeChatBridge(): WeChatBridge {
  if (!wechat) wechat = new WeChatBridge()
  return wechat
}
