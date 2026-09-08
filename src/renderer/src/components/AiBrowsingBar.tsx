import { useStore } from '../store'
import { Globe, Hand } from 'lucide-react'

// 顶部浮条：AI 操控浏览器时显示"PlanGo正在浏览 X…"+ 当前动作 + 人可"接管"（Manus/Fellou 式 human-in-the-loop）。
export function AiBrowsingBar(): JSX.Element | null {
  const ai = useStore((s) => s.aiBrowsing)
  const steps = useStore((s) => s.steps)
  const setAiBrowsing = useStore((s) => s.setAiBrowsing)
  if (!ai.active) return null
  const running = [...steps].reverse().find((s) => s.status === 'running')
  return (
    <div className="absolute top-2 left-1/2 -translate-x-1/2 z-30 flex items-center gap-2 px-3 py-1.5 rounded-full bg-neutral-900/90 text-white shadow-lg backdrop-blur animate-in">
      <span className="relative flex h-2 w-2">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand opacity-75" />
        <span className="relative inline-flex rounded-full h-2 w-2 bg-brand" />
      </span>
      <Globe size={13} className="text-brand" />
      <span className="text-xs font-medium">
        PlanGo正在{ai.action}{ai.site ? ` ${ai.site}` : ''}…
      </span>
      {running && <span className="text-[11px] text-neutral-300 max-w-[220px] truncate">{running.label}</span>}
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
