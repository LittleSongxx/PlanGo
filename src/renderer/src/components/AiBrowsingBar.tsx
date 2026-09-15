import { useStore } from '../store'
import { projectEvents } from '../lib/harnessProjection'
import { Globe, Hand } from 'lucide-react'

// 顶部浮条：AI 操控浏览器时显示"PlanGo正在浏览 X…"+ 当前动作 + 人可"接管"（Manus/Fellou 式 human-in-the-loop）。
// 它跟着整个任务走，而不是跟着单条命令亮灭——命令之间的那一瞬会让"它还在干活"变得看不见。
export function AiBrowsingBar(): JSX.Element | null {
  const ai = useStore((s) => s.aiBrowsing)
  const busy = useStore((s) => s.busy)
  const run = useStore((s) => s.run)
  const steps = projectEvents(useStore((s) => s.events))
  const setAiBrowsing = useStore((s) => s.setAiBrowsing)
  if (!(ai.active || (busy && ai.site))) return null
  const action = ({ snapshot: '查看页面', read_page: '查看页面', extract: '读取资料', extract_tables: '读取表格', screenshot: '理解画面', click: '操作页面', type: '填写信息', navigate: '打开网页', open_tab: '打开网页', scroll: '浏览页面', highlight: '定位内容', current: '查看页面' } as Record<string, string>)[ai.action] || ai.action
  let site = ai.site
  try { site = new URL(site).hostname.replace(/^www\./, '') } catch {}
  const running = [...steps].reverse().find((s) => s.status === 'running')
  const used = Math.max(0, Number(run?.state.browser_steps || 0) - Number((run?.state.turn_budget as { browser_baseline?: number } | undefined)?.browser_baseline || 0))
  const limit = Number((run?.state.browser_task_context as { browser_step_limit?: number } | undefined)?.browser_step_limit || 0)
  const progress = used > 0 ? (limit > 0 && used <= limit ? `已读 ${used}/${limit} 步` : `已读 ${used} 步`) : ''
  return (
    <div className="absolute top-5 right-4 z-30 flex items-center gap-2 px-3 py-2 rounded-xl bg-brand-ink text-white shadow-card max-w-[calc(100%-145px)]">
      <span className="relative flex h-2 w-2">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand opacity-75" />
        <span className="relative inline-flex rounded-full h-2 w-2 bg-brand" />
      </span>
      <Globe size={13} className="text-brand" />
      <span className="text-[11px] font-medium truncate">
        {ai.active ? action : '任务进行中'}{site ? ` · ${site}` : ''}{progress ? ` · ${progress}` : ''}
      </span>
      {running && <span className="text-[11px] text-neutral-300 max-w-[140px] truncate hidden 2xl:block">{running.label}</span>}
      <button
        onClick={() => { void useStore.getState().cancelRun(); useStore.getState().setView('browser'); setAiBrowsing({ active: false }) }}
        className="ml-1 flex items-center gap-1 px-2 py-0.5 rounded-full bg-white/15 hover:bg-white/25 text-[11px]"
        title="请求停止任务，再手动接管浏览器"
      >
        <Hand size={11} /> 停止任务并接管
      </button>
    </div>
  )
}
