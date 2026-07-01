// 专家子 Agent（Agents-as-Tools，只读、上下文隔离、可并行）。对标美团 WOWService。
// Primary 小悠把它们当工具调；每个子 Agent 跑独立 Harness Loop，限定工具子集，只回摘要。
import { registerTool, type ToolOutcome } from '../tools/registry'
import { runAgentLoop } from './loop'
import { emitStep, updateStep } from './bus'

interface SubAgentSpec {
  name: string
  label: string
  description: string
  toolNames: string[]
  systemSuffix: string
}

const SUBAGENTS: SubAgentSpec[] = [
  {
    name: 'dining_agent',
    label: '餐饮专家',
    description: '委派"找餐厅/查可订/AI点菜/比价"的复杂餐饮需求给餐饮专家子Agent（只读）。',
    toolNames: ['search_nearby_poi', 'check_availability', 'recommend_dishes', 'compare_deal'],
    systemSuffix: '你现在是【餐饮专家子Agent】。只负责餐饮：按人群硬约束找餐厅、查可订/排队、AI点菜、单点vs套餐比价。用最少的工具调用得到结论，最后用 3-5 行给出明确推荐与理由，不要反问。'
  },
  {
    name: 'activity_agent',
    label: '玩乐专家',
    description: '委派"找玩乐/亲子/展览/citywalk + 看天气选室内外"的需求给玩乐专家子Agent（只读）。',
    toolNames: ['search_nearby_poi', 'get_weather'],
    systemSuffix: '你现在是【玩乐专家子Agent】。只负责非餐饮玩乐：亲子/展览/citywalk/休闲；先看天气决定室内外，再按人群约束推荐 2-3 个，给理由。不要反问。'
  },
  {
    name: 'research_agent',
    label: '攻略调研专家',
    description: 'Horsepower 式：委派"上网搜攻略并汇总"（打开小红书/点评攻略页→提取→总结）给调研子Agent（只读）。',
    toolNames: ['browser_navigate', 'browser_read_page', 'browser_extract', 'browser_scroll'],
    systemSuffix: '你现在是【攻略调研子Agent】。用内置浏览器打开攻略页（小红书/大众点评/百度），读取并提取正文，汇总出可执行的本地玩法要点（地点+亮点+人均+注意事项），最后用要点列表输出。若页面打不开就基于已知常识给通用建议并说明未联网。'
  },
  {
    name: 'memory_agent',
    label: '记忆管家',
    description: '委派"从这次对话里识别并沉淀长期偏好、并把已知偏好带入本次规划"给记忆管家子Agent（对齐用户记忆，越用越懂）。',
    toolNames: ['remember_preference'],
    systemSuffix: '你现在是【记忆管家子Agent】。上文「关于用户（记忆）」已给出已知长期偏好。任务：1) 从用户这次的话里识别出值得长期记住的新偏好（口味/人群/常带的人/家或公司地址/预算习惯），用 remember_preference 记住；2) 用 2-3 行列出"本次应自动带入的偏好"，供主Agent规划时使用。不要反问。'
  }
]

for (const spec of SUBAGENTS) {
  registerTool({
    name: spec.name,
    label: spec.label,
    kind: 'READ',
    description: spec.description,
    parameters: { type: 'object', properties: { task: { type: 'string', description: '交给该专家的具体任务' } }, required: ['task'] },
    async execute(args): Promise<ToolOutcome> {
      const task = String(args.task || '')
      // 多 Agent 协同可视化（仿 WOWService）：主 Agent 派单 → 子 Agent 干活 → 回传结论
      const sid = emitStep(`主 Agent 派单 → ${spec.label}`)
      try {
        const reply = await runAgentLoop(task, [], {
          toolNames: spec.toolNames,
          systemSuffix: spec.systemSuffix,
          noHistory: true,
          silent: false,
          maxTurns: 5
        })
        updateStep(sid, 'done', `${spec.label} 已回传结论`)
        return { result: `【${spec.label}】结论：\n${reply.content}`, cards: reply.cards }
      } catch (e) {
        updateStep(sid, 'error', (e as Error).message)
        return { result: `${spec.label}执行失败：${(e as Error).message}` }
      }
    }
  })
}
