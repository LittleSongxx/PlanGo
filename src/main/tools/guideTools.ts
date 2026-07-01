// 攻略导入：把小红书/攻略的「链接或截图」抽成结构化需求（城市/地点/人群/氛围/菜品/预算），
// 再交给 plan_outing 出方案。链接走浏览器读正文(Readability)+og 兜底；截图走多模态视觉。
import { registerTool, type ToolOutcome } from './registry'
import { emitStep, updateStep } from '../agent/bus'
import { session } from '../agent/session'
import { getConfig } from '../config'
import { chat, chatVision, isConfigured } from '../llm'
import { browserAction } from '../browser-bridge'

interface GuideDemand {
  city?: string
  area?: string
  crowd?: string
  vibe?: string
  dishes?: string[]
  interests?: string[]
  budget_per_person?: number
  summary?: string
}

const EXTRACT_SYS =
  '你是本地生活攻略解析器。从给定的攻略文字或图片里，抽取出可用于「周末几小时本地出行」规划的要素，只输出 JSON：' +
  '{"city":城市或空,"area":商圈/地点或空,"crowd":人群如带娃/约会/朋友聚,"vibe":氛围如出片/安静/热闹,"dishes":[想吃的菜/店类型],"interests":[玩乐类型],"budget_per_person":人均数字或0,"summary":一句话概括这篇攻略在推荐什么}。' +
  '不确定的字段留空/0，不要编造。'

async function fetchOg(url: string): Promise<string> {
  try {
    const res = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } })
    const html = await res.text()
    const pick = (re: RegExp): string => (html.match(re)?.[1] || '').trim()
    const title = pick(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i) || pick(/<title>([^<]+)<\/title>/i)
    const desc = pick(/<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']+)["']/i) || pick(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["']/i)
    return `${title}\n${desc}`.trim()
  } catch {
    return ''
  }
}

registerTool({
  name: 'import_guide',
  label: '攻略导入',
  kind: 'READ',
  description:
    '用户贴了小红书/攻略「链接」或上传了「截图」想照着安排时调用：抽取其中的城市/地点/人群/氛围/菜品/预算等要素。抽取完请紧接着用 plan_outing 按这些要素规划。截图无需传参（系统已暂存用户最近上传的图）。',
  parameters: {
    type: 'object',
    properties: {
      link: { type: 'string', description: '攻略链接（小红书/大众点评/公众号等），有则填' }
    }
  },
  async execute(args): Promise<ToolOutcome> {
    if (!isConfigured()) return { result: '还没配置大模型，无法解析攻略。请在设置里填 API Key。' }
    const link = String(args.link || '').trim()
    const img = session.pendingGuideImage

    let demand: GuideDemand | null = null
    let via = ''

    if (img) {
      const st = emitStep('识别攻略截图（视觉）')
      try {
        const raw = await chatVision(EXTRACT_SYS, '这是用户上传的攻略截图，请抽取规划要素并只回 JSON。', img, { json: true, temperature: 0.2 })
        demand = safeJson(raw)
        via = '截图视觉识别'
        updateStep(st, 'done', demand?.summary || '已识别', 'real')
      } catch (e) {
        updateStep(st, 'error', (e as Error).message)
      } finally {
        session.pendingGuideImage = undefined // 用完即清，避免污染后续
      }
    }

    if (!demand && link) {
      const st = emitStep(`读取攻略：${link.slice(0, 40)}`)
      let text = ''
      try {
        await browserAction('navigate', { url: link })
        const r = await browserAction('read_page')
        text = String(r.text || '').slice(0, 4000)
      } catch {
        /* 浏览器读失败，走 og 兜底 */
      }
      if (!text) text = await fetchOg(link)
      if (text) {
        try {
          const raw = await chat(EXTRACT_SYS, `攻略正文：\n${text}\n\n只回 JSON。`, { json: true, temperature: 0.2 })
          demand = safeJson(raw)
          via = text.length > 200 ? '浏览器读取正文' : '链接摘要(og)'
          updateStep(st, 'done', demand?.summary || '已抽取', 'real')
        } catch (e) {
          updateStep(st, 'error', (e as Error).message)
        }
      } else {
        updateStep(st, 'error', '读不到内容（可能需登录/风控）')
        return { result: `这个链接我没读到正文（可能需要登录或被风控挡了）。你可以先在浏览器里登录该站点，或直接把攻略截图发我，我用视觉识别。` }
      }
    }

    if (!demand) return { result: '没拿到攻略内容。请提供攻略链接，或上传一张攻略截图后再说一次"按这个安排"。' }

    const city = demand.city || getConfig().city
    const parts: string[] = []
    if (demand.area) parts.push(`地点/商圈：${demand.area}`)
    if (demand.crowd) parts.push(`人群：${demand.crowd}`)
    if (demand.vibe) parts.push(`氛围：${demand.vibe}`)
    if (demand.dishes?.length) parts.push(`想吃：${demand.dishes.join('、')}`)
    if (demand.interests?.length) parts.push(`玩乐：${demand.interests.join('、')}`)
    if (demand.budget_per_person) parts.push(`人均预算：¥${demand.budget_per_person}`)

    return {
      result:
        `已从攻略（${via}）抽取要素：\n城市：${city}\n${parts.join('\n') || '（攻略偏氛围向，具体店未指名）'}\n` +
        `概括：${demand.summary || '本地探店/游玩推荐'}\n\n` +
        `下一步：请立刻用 plan_outing 按上述要素在「${city}」规划 3 套方案（把 area/crowd/dishes/interests/budget 映射到对应参数），落地成真实可去的门店。`,
      activities: [`导入了一篇攻略（${city}）`]
    }
  }
})

function safeJson(raw: string): GuideDemand | null {
  try {
    const cleaned = raw.replace(/^```json\s*/i, '').replace(/```$/i, '').trim()
    return JSON.parse(cleaned) as GuideDemand
  } catch {
    return null
  }
}
