import type { AgentStep } from '@shared/types'
import { CheckCircle2, Circle, Clock3, Loader2, XCircle, ChevronDown } from 'lucide-react'
import { SourceBadge } from './SourceBadge'

function StepIcon({ status, animate = false }: { status: AgentStep['status']; animate?: boolean }): JSX.Element {
  if (status === 'done') return <CheckCircle2 size={14} className="text-green-600 shrink-0" />
  if (status === 'error') return <XCircle size={14} className="text-red-500 shrink-0" />
  if (status === 'waiting') return <Clock3 size={14} className="text-amber-600 shrink-0" />
  if (status === 'running') return <Loader2 size={14} className={`text-brand-strong shrink-0 ${animate ? 'spin' : ''}`} />
  return <Circle size={14} className="text-neutral-400 shrink-0" />
}

export function StepFlow({ steps }: { steps: AgentStep[] }): JSX.Element {
  const latest = steps.at(-1)
  return <details className="group bg-[var(--surface-soft)] border border-[var(--line)] rounded-2xl animate-in">
    <summary className="cursor-pointer list-none p-4 flex items-center gap-2 text-xs min-w-0">
      {latest && <StepIcon status={latest.status} animate />}
      <span className="shrink-0 font-medium">任务过程（{steps.length}）</span>
      {latest && <span title={latest.label} className="min-w-0 truncate text-[var(--muted)]">{latest.label}</span>}
      <ChevronDown size={14} className="ml-auto shrink-0 text-neutral-400 transition-transform group-open:rotate-180" />
    </summary>
    <div className="px-4 pb-4 space-y-2.5 border-t border-[var(--line)] pt-3">
      {steps.map((step, index) => <div key={step.id} className="flex items-center gap-2 text-xs text-neutral-600 min-w-0">
        <StepIcon status={step.status} animate={index === steps.length - 1} />
        <span title={step.label} className={`shrink-0 max-w-[65%] truncate ${step.status === 'error' ? 'text-red-500' : ''}`}>{step.label}</span>
        {step.detail && <span title={step.detail} className="text-[var(--muted)] min-w-0 truncate">· {step.detail}</span>}
        {step.source && <SourceBadge source={step.source} />}
      </div>)}
    </div>
  </details>
}

export { Circle }
