// 主进程 ↔ 渲染层 浏览器动作桥。Agent 工具在主进程，<webview> 在渲染层；
// 用 webContents.send 下发动作 + id + Promise 注册表，渲染层执行后回执。
import { getMainWindow } from './index'
import { IPC } from '@shared/ipc'

export type BrowserActionName = 'read_page' | 'navigate' | 'open_tab' | 'click' | 'type' | 'scroll' | 'extract' | 'extract_tables' | 'current' | 'highlight'

export interface BrowserActionResult {
  ok?: boolean
  error?: string
  url?: string
  title?: string
  text?: string
  elements?: { idx: number; tag: string; role: string; name: string; text: string }[]
  tables?: { headers: string[]; rows: string[][] }[]
}

let seq = 0
const pending = new Map<number, (r: BrowserActionResult) => void>()

// 本地生活相关站点白名单（写操作/危险操作仅在这些站点考虑）
const ALLOW = [/dianping\.com/, /meituan\.com/, /amap\.com/, /xiaohongshu\.com/, /baidu\.com/, /douyin\.com/]

export function isAllowed(url: string): boolean {
  return ALLOW.some((re) => re.test(url || ''))
}

// 危险词：涉及取号/支付/下单/提交 → 强制两步确认（由 Agent 层拦截，这里提供识别）
const DANGER = /(支付|付款|下单|提交订单|确认支付|立即购买|取号|预约|结算)/

export function isDangerAction(actionText: string): boolean {
  return DANGER.test(actionText || '')
}

export function browserAction(action: BrowserActionName, args: Record<string, unknown> = {}): Promise<BrowserActionResult> {
  const win = getMainWindow()
  if (!win || win.isDestroyed()) return Promise.resolve({ error: '没有可用的应用窗口' })
  const id = ++seq
  return new Promise((resolve) => {
    pending.set(id, resolve)
    win.webContents.send(IPC.browserExec, { id, action, args })
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id)
        resolve({ error: '浏览器动作超时（请确认已打开浏览器标签并停留在目标页面）' })
      }
    }, 20_000)
  })
}

export function resolveBrowserAction(id: number, result: BrowserActionResult): void {
  const fn = pending.get(id)
  if (fn) {
    pending.delete(id)
    fn(result)
  }
}
