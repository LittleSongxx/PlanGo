import type { AgentStep, StepStatus } from '@shared/types'
import { CheckCircle2, Circle, Clock3, Loader2, XCircle, ChevronDown } from 'lucide-react'
import { SourceBadge } from './SourceBadge'

const STATUS_HINT: Record<StepStatus, string> = {
  running: '进行中',
  waiting: '等待你继续',
  error: '未完成',
  done: '已结束',
  idle: '准备中'
}

function StepIcon({ status, animate = false }: { status: StepStatus; animate?: boolean }): JSX.Element {
  if (status === 'done') return <CheckCircle2 size={14} className="text-emerald-600 shrink-0" />
  if (status === 'error') return <XCircle size={14} className="text-red-500 shrink-0" />
  if (status === 'waiting') return <Clock3 size={14} className="text-amber-600 shrink-0" />
  if (status === 'running') return <Loader2 size={14} className={`text-brand-strong shrink-0 ${animate ? 'animate-spin' : ''}`} />
  return <Circle size={14} className="text-neutral-300 shrink-0" />
}

export function StepFlow({ steps }: { steps: AgentStep[] }): JSX.Element {
  const latest = steps.at(-1)
  const finished = steps.filter(step => step.status === 'done').length
  return <details className="group bg-[var(--surface-soft)] border border-[var(--line)] rounded-2xl animate-in">
    <summary className="cursor-pointer list-none p-4 flex items-center gap-2.5 text-xs min-w-0">
      {latest && <StepIcon status={latest.status} animate={latest.status === 'running'} />}
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-brand-ink truncate">{latest?.label || '任务过程'}</span>
        <span className="block mt-0.5 text-[11px] text-[var(--muted)]">
          {STATUS_HINT[latest?.status || 'idle']}
          {finished ? ` · ${finished} 步已完成` : ''}
        </span>
      </span>
      <ChevronDown size={14} className="shrink-0 text-neutral-400 transition-transform group-open:rotate-180" />
    </summary>
    <ol className="px-4 pb-4 border-t border-[var(--line)] pt-3" aria-label="任务过程">
      {steps.map((step, index) => {
        const current = index === steps.length - 1
        return <li key={step.id} aria-current={current ? 'step' : undefined} className="flex items-stretch gap-2.5 text-xs min-w-0">
          <div className="flex flex-col items-center w-3.5 shrink-0">
            <StepIcon status={step.status} animate={current && step.status === 'running'} />
            {index < steps.length - 1 && <span className={`w-px flex-1 min-h-3 mt-1 ${step.status === 'done' ? 'bg-emerald-200' : 'bg-[var(--line)]'}`} />}
          </div>
          <div className={`min-w-0 flex-1 ${index < steps.length - 1 ? 'pb-3' : ''}`}>
            <div className="flex items-center gap-2 min-w-0">
              <span title={step.label} className={`truncate ${step.status === 'error' ? 'text-red-500' : current ? 'font-medium text-brand-ink' : 'text-neutral-600'}`}>{step.label}</span>
              {step.source && <SourceBadge source={step.source} />}
            </div>
            {step.detail && <p title={step.detail} className="mt-0.5 text-[11px] leading-5 text-[var(--muted)] truncate">{step.detail}</p>}
          </div>
        </li>
      })}
    </ol>
  </details>
}

export { Circle }
