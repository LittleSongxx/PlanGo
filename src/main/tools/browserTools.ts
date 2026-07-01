// 浏览器感知-动作工具：让小悠"自己操控真实美团/点评页面"读数据、搜攻略、点按。
// 危险动作（取号/支付/下单）→ 强制两步确认（DPT System2）。
import { registerTool, type ToolOutcome } from './registry'
import { emitStep, updateStep, emitCard } from '../agent/bus'
import { session } from '../agent/session'
import { browserAction, isDangerAction } from '../browser-bridge'

registerTool({
  name: 'browser_navigate',
  label: '打开网址',
  kind: 'READ',
  description: '在内置浏览器打开网址（真实美团/点评/小红书/高德）。不是网址则按百度搜索。',
  parameters: { type: 'object', properties: { url: { type: 'string' } }, required: ['url'] },
  async execute(args): Promise<ToolOutcome> {
    const st = emitStep(`打开 ${args.url}`)
    const r = await browserAction('navigate', { url: args.url })
    if (r.error) {
      updateStep(st, 'error', r.error)
      return { result: `打开失败：${r.error}` }
    }
    updateStep(st, 'done', r.url, 'real')
    return { result: `已打开 ${r.url || args.url}。可以 browser_read_page 读取页面。` }
  }
})

registerTool({
  name: 'browser_read_page',
  label: '读取网页',
  kind: 'READ',
  description: '把当前页面蒸馏成"可交互元素清单(带序号)+正文"，用于理解页面并决定点哪里。',
  parameters: { type: 'object', properties: {} },
  async execute(): Promise<ToolOutcome> {
    const st = emitStep('读取当前页面')
    const r = await browserAction('read_page')
    if (r.error) {
      updateStep(st, 'error', r.error)
      return { result: `读取失败：${r.error}（请先打开一个浏览器标签并停在目标页）` }
    }
    updateStep(st, 'done', r.title, 'real')
    const els = (r.elements || []).slice(0, 40).map((e) => `[${e.idx}] ${e.tag}${e.role ? '(' + e.role + ')' : ''} ${e.name || e.text}`).join('\n')
    return { result: `页面《${r.title}》\n正文摘要：${(r.text || '').slice(0, 1200)}\n可交互元素：\n${els}` }
  }
})

registerTool({
  name: 'browser_extract',
  label: '提取正文',
  kind: 'READ',
  description: 'Horsepower 式：提取当前页正文（含团购/菜单表格），用于汇总攻略或抽取真实门店数据。',
  parameters: { type: 'object', properties: {} },
  async execute(): Promise<ToolOutcome> {
    const st = emitStep('提取页面正文/表格')
    const r = await browserAction('extract_tables')
    if (r.error) {
      updateStep(st, 'error', r.error)
      return { result: `提取失败：${r.error}` }
    }
    updateStep(st, 'done', undefined, 'real')
    const tables = (r.tables || []).map((t, i) => `表${i + 1}：${t.headers.join(' | ')}\n${t.rows.slice(0, 6).map((row) => row.join(' | ')).join('\n')}`).join('\n\n')
    return { result: `正文：${(r.text || '').slice(0, 1800)}${tables ? `\n\n提取到表格：\n${tables}` : ''}（来源 real）` }
  }
})

registerTool({
  name: 'browser_click',
  label: '点击元素',
  kind: 'WRITE',
  description: '点击页面元素（按 browser_read_page 返回的 idx）。若涉及取号/支付/下单等危险动作会先要求两步确认。',
  parameters: { type: 'object', properties: { idx: { type: 'number' }, hint: { type: 'string', description: '该元素的文字，用于危险动作识别' } }, required: ['idx'] },
  async execute(args): Promise<ToolOutcome> {
    const hint = String(args.hint || '')
    if (isDangerAction(hint)) {
      const token = session.registerConfirm({
        title: `确认页面操作：${hint}`,
        detail: '这是涉及取号/支付/下单的写操作，确认后才会点击。',
        danger: true,
        run: async () => {
          const st = emitStep(`点击「${hint}」`)
          const r = await browserAction('click', { idx: args.idx })
          updateStep(st, r.error ? 'error' : 'done', r.error || r.text, 'real')
          return { text: r.error ? `点击失败：${r.error}` : `已点击「${r.text || hint}」。`, cards: [] }
        }
      })
      emitCard({ kind: 'confirm', token, title: `确认页面操作：${hint}`, detail: '涉及取号/支付/下单，需两步确认', danger: true })
      return { result: `「${hint}」是危险写操作，已生成两步确认卡，等用户确认后再点击。` }
    }
    const st = emitStep(`点击元素 [${args.idx}]`)
    const r = await browserAction('click', { idx: args.idx })
    updateStep(st, r.error ? 'error' : 'done', r.error || r.text, 'real')
    return { result: r.error ? `点击失败：${r.error}` : `已点击：${r.text || '元素 ' + args.idx}` }
  }
})

registerTool({
  name: 'browser_type',
  label: '填写输入框',
  kind: 'WRITE',
  description: '在页面输入框填写文字（按 idx）。',
  parameters: { type: 'object', properties: { idx: { type: 'number' }, text: { type: 'string' } }, required: ['idx', 'text'] },
  async execute(args): Promise<ToolOutcome> {
    const st = emitStep(`填写 [${args.idx}]`)
    const r = await browserAction('type', { idx: args.idx, text: args.text })
    updateStep(st, r.error ? 'error' : 'done', r.error, 'real')
    return { result: r.error ? `填写失败：${r.error}` : `已在元素 ${args.idx} 填写：${args.text}` }
  }
})

registerTool({
  name: 'browser_scroll',
  label: '滚动页面',
  kind: 'READ',
  description: '上下滚动当前页面以加载更多内容。',
  parameters: { type: 'object', properties: { dir: { type: 'string', enum: ['up', 'down'] } } },
  async execute(args): Promise<ToolOutcome> {
    const r = await browserAction('scroll', { dir: args.dir || 'down' })
    return { result: r.error ? `滚动失败：${r.error}` : `已滚动。` }
  }
})
