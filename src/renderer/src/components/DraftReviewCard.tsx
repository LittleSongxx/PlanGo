import { FileCheck2, Save, Globe, AlertTriangle } from 'lucide-react'

import type { DraftReviewCardDetails } from '@shared/types'

export function DraftReviewCard({ draft, busy, onDecision }: {
  draft: DraftReviewCardDetails
  busy: boolean
  onDecision: (decision: 'save' | 'prepare') => void
}): JSX.Element {
  return <section className="plango-card p-5 border-amber-200" aria-label="草案待核对">
    <div className="flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-50 text-amber-700"><FileCheck2 size={20} /></span><div><div className="plango-kicker">草案核对</div><h3 className="font-semibold text-base mt-1">方案已整理，仍有信息待确认</h3></div><span className="ml-auto text-xs text-[var(--muted)]">版本 {draft.planVersion}</span></div>
    <p className="text-[13px] text-neutral-600 leading-6 mt-4">你可以先保存这份草案。继续准备只会核对页面上的表单，预约提交、下单和支付仍需另行确认。</p>
    {draft.unknowns.length > 0 && <div className="rounded-xl bg-amber-50 border border-amber-100 p-4 mt-4"><p className="flex items-center gap-2 text-xs font-semibold text-amber-800"><AlertTriangle size={14} />以下信息仍未核验</p><ul className="list-disc pl-5 mt-2 space-y-1.5 text-xs leading-5 text-amber-800">{draft.unknowns.map((note, index) => <li key={index}>{note}</li>)}</ul></div>}
    {!draft.canPrepare && <p className="mt-3 text-xs text-amber-800">{draft.blockedReason || '当前信息尚不满足页面准备条件，请补充或修改要求。'}</p>}
    <div className="flex flex-wrap gap-2 mt-5"><button disabled={busy} onClick={() => onDecision('save')} className="inline-flex items-center gap-2 rounded-xl border border-[var(--line)] px-4 py-2.5 text-xs bg-white text-brand-ink disabled:opacity-40"><Save size={14} />保存草案</button><button disabled={busy || !draft.canPrepare} onClick={() => onDecision('prepare')} className="plango-primary disabled:opacity-40"><Globe size={14} />仅准备表单</button></div>
    <p className="mt-3 text-[11px] text-[var(--muted)]">准备完成也不代表商家已接受预约或产生订单。</p>
  </section>
}
