import type { AgentStep } from '@shared/types'
import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import { SourceBadge } from './SourceBadge'

export function StepFlow({ steps }: { steps: AgentStep[] }): JSX.Element {
  return (
    <div className="bg-neutral-50 border border-neutral-200 rounded-xl p-2.5 space-y-1.5 animate-in">
      <div className="text-[11px] text-neutral-400 font-medium px-0.5">执行过程（透明可见）</div>
      {steps.map((s) => (
        <div key={s.id} className="flex items-center gap-2 text-xs text-neutral-600">
          {s.status === 'done' ? (
            <CheckCircle2 size={14} className="text-green-500 shrink-0" />
          ) : s.status === 'error' ? (
            <XCircle size={14} className="text-red-400 shrink-0" />
          ) : (
            <Loader2 size={14} className="text-brand-ink shrink-0 spin" />
          )}
          <span className={s.status === 'error' ? 'text-red-500' : ''}>{s.label}</span>
          {s.detail && <span className="text-neutral-400 truncate">· {s.detail}</span>}
          {s.source && <SourceBadge source={s.source} />}
        </div>
      ))}
    </div>
  )
}

export { Circle }
