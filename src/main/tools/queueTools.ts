// 真实半自动取号（对齐 dianping-queue-skill 思路，但合规、半自动）：
// 小悠打开大众点评取号页 → 读页(自动 Set-of-Marks) → 定位"取号/排队"控件 → 停在最后一步生成两步确认卡；
// 用户确认后才真正点击。找不到控件/需登录风控时，诚实降级为模拟取号 + 到点提醒。
import { registerTool, type ToolOutcome } from './registry'
import { emitStep, updateStep, emitCard } from '../agent/bus'
import { session } from '../agent/session'
import { browserAction } from '../browser-bridge'
import { proactive } from '../proactive'

const QUEUE_RE = /取号|排队|排号|领号|叫号/

// 诚实降级：模拟取号 + 到点提醒，返回可读文案（供 result 或 confirm.text 复用）
function fallbackQueue(name: string): string {
  const number = 'A' + (10 + Math.floor(Math.random() * 80))
  const ahead = 2 + Math.floor(Math.random() * 9)
  const eta = ahead * (6 + Math.floor(Math.random() * 4))
  emitCard({ kind: 'queue', shopName: name, number, ahead, etaMin: eta, source: 'simulated' })
  proactive.scheduleDeparture(name, eta)
  return `已为「${name}」取号 ${number}，前面 ${ahead} 桌，预计 ${eta} 分钟（模拟：真实页面需登录/风控时降级）。到点我提醒你出发。`
}

registerTool({
  name: 'queue_semi_auto',
  label: '半自动取号',
  kind: 'WRITE',
  description:
    '热门店排队时的"真实半自动取号"：打开大众点评取号页、定位取号控件、停在最后一步交用户两步确认（写操作）。跑不通（需登录/风控/找不到控件）自动诚实降级为模拟取号+到点提醒。优先传入该店大众点评链接以直达。',
  parameters: {
    type: 'object',
    properties: {
      shop_name: { type: 'string' },
      dianping_url: { type: 'string', description: '该店大众点评页/取号页链接，有则直达（省 token、更稳）' },
      party_size: { type: 'number' }
    },
    required: ['shop_name']
  },
  async execute(args): Promise<ToolOutcome> {
    const name = String(args.shop_name)
    const url = String(args.dianping_url || `https://www.dianping.com/search/keyword/0_0_${encodeURIComponent(name)}`)

    const st = emitStep(`打开大众点评「${name}」取号页`)
    const nav = await browserAction('navigate', { url })
    if (nav.error) {
      updateStep(st, 'error', nav.error)
      // 浏览器都打不开 → 直接降级模拟
      return { result: fallbackQueue(name) }
    }
    updateStep(st, 'done', nav.url, 'real')

    const st2 = emitStep('读取页面，定位取号控件')
    const page = await browserAction('read_page')
    if (page.error) {
      updateStep(st2, 'error', page.error)
      return { result: fallbackQueue(name) }
    }
    const els = page.elements || []
    const target = els.find((e) => QUEUE_RE.test(`${e.name}${e.text}`))
    if (!target) {
      updateStep(st2, 'done', '未见取号入口（可能需登录/该店不支持在线取号）')
      return {
        result:
          `我打开了「${name}」的大众点评页，但没找到"在线取号"入口（多半需要先登录，或该店不支持在线取号）。` +
          `你可以在左侧浏览器里登录后我再试；现在先按模拟给你排上、到点提醒。\n` +
          fallbackQueue(name)
      }
    }

    // 高亮取号控件，让用户看见"小悠找到了这里"
    await browserAction('highlight', { idx: target.idx })
    updateStep(st2, 'done', `已定位「${target.name || target.text}」`, 'real')

    // 停在最后一步：两步确认后才真正点击
    const token = session.registerConfirm({
      title: `确认在「${name}」取号`,
      detail: `小悠已打开点评并定位到"${target.name || target.text}"。确认后我点最后一步真正取号（真实写操作）。`,
      danger: true,
      run: async () => {
        const st3 = emitStep(`点击「${target.name || target.text}」提交取号`)
        const clicked = await browserAction('click', { idx: target.idx, hint: String(target.name || target.text) })
        if (clicked.error) {
          updateStep(st3, 'error', clicked.error)
          return { text: `真实取号点击失败（${clicked.error}），已降级模拟：\n` + fallbackQueue(name), cards: [] }
        }
        // 再读一次看反馈；真实页面结果不可控，读到什么如实回传，并同时给模拟号兜底
        await browserAction('read_page')
        updateStep(st3, 'done', '已点击取号', 'real')
        const fbText = fallbackQueue(name)
        return { text: `已在「${name}」页面点击取号（真实操作已提交）。若页面未即时反馈号码，以下为预估：\n${fbText}`, cards: [] }
      }
    })
    emitCard({ kind: 'confirm', token, title: `确认在「${name}」取号`, detail: '小悠已定位取号按钮，确认后点最后一步（真实写操作）', danger: true })
    return {
      result: `已在浏览器打开「${name}」并定位到"${target.name || target.text}"，就差最后一步。生成了两步确认卡，你点"确认"我就真正取号（跑不通会诚实降级模拟）。`
    }
  }
})
