import { useEffect, useRef, useState } from 'react'
import { ThumbsDown, ThumbsUp, Check, Loader2 } from 'lucide-react'
import type { HarnessFeedback, HarnessFeedbackInput } from '@shared/types'
import { useStore } from '../store'

export function ResultFeedback(): JSX.Element | null {
  const run = useStore(state => state.run)
  const turn = Number(run?.state.turn_id || 1)
  if (!run || !['SUCCEEDED', 'PARTIAL_FAILED', 'FAILED', 'CANCELLED', 'INFEASIBLE'].includes(run.phase)) return null
  return <FeedbackChoice key={`${run.run_id}:${turn}`} runId={run.run_id} turn={turn} records={run.feedback || []} />
}

function FeedbackChoice({ runId, turn, records }: { runId: string; turn: number; records: HarnessFeedback[] }): JSX.Element {
  const [saved, setSaved] = useState<HarnessFeedback | undefined>(() => records.filter(item => item.turn_id === turn).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)).at(-1))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [remember, setRemember] = useState(false)
  const [preferenceText, setPreferenceText] = useState('')
  const [polarity, setPolarity] = useState<'positive' | 'negative'>('positive')
  const key = `plango_feedback_attempt:${runId}:${turn}`
  const pending = useRef<HarnessFeedbackInput | null>(null)
  useEffect(() => {
    const latest = records.filter(item => item.turn_id === turn).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)).at(-1)
    setSaved(latest)
  }, [records, turn])

  const submit = async (rating: HarnessFeedbackInput['rating']) => {
    if (busy || saved?.rating === rating) return
    const preference = remember && preferenceText.trim()
      ? { text: preferenceText.trim(), polarity } : undefined
    if (remember && !preference) {
      setError('勾选记住偏好时请填写要保存的内容。')
      return
    }
    setBusy(true); setError('')
    try {
      if (!pending.current) {
        try {
          const attempt = JSON.parse(localStorage.getItem(key) || 'null')
          if (attempt?.turn_id === turn && typeof attempt.feedback_id === 'string' && ['helpful', 'unhelpful'].includes(attempt.rating)) pending.current = attempt
        } catch { /* A feedback draft is not a business action receipt. */ }
      }
      if (pending.current?.rating !== rating || JSON.stringify(pending.current.preference) !== JSON.stringify(preference)) {
        pending.current = { feedback_id: crypto.randomUUID(), turn_id: turn, rating, ...(preference ? { preference } : {}) }
      }
      localStorage.setItem(key, JSON.stringify(pending.current))
      const result = await window.plango.harness.feedback(runId, pending.current)
      if (result.accepted !== true || result.feedback.feedback_id !== pending.current.feedback_id || result.feedback.run_id !== runId || result.feedback.turn_id !== turn || result.feedback.rating !== rating) throw new Error('反馈确认不匹配')
      setSaved(result.feedback)
      localStorage.removeItem(key); pending.current = null
      await useStore.getState().refreshRun()
    } catch { setError('反馈尚未确认保存，点击原选项可以重试。') }
    finally { setBusy(false) }
  }

  return <div className="plango-card plango-workspace-col mb-5 px-4 py-3 flex flex-wrap items-center gap-3" aria-label="结果反馈">
    <div className="flex-1 min-w-[150px]"><p className="text-xs font-medium">这次结果有帮助吗？</p><p className="text-[10px] text-[var(--muted)] mt-1">点赞不会自动变成偏好；只有你明确勾选并填写后才会记住。</p></div>
    {(['helpful', 'unhelpful'] as const).map(rating => <button key={rating} onClick={() => void submit(rating)} disabled={busy} aria-pressed={saved?.rating === rating}
      className={`inline-flex items-center gap-1.5 border rounded-lg px-3 py-2 text-xs disabled:opacity-50 ${saved?.rating === rating ? 'bg-brand-soft border-brand text-brand-strong' : 'bg-white border-[var(--line)] text-neutral-600'}`}>
      {rating === 'helpful' ? <ThumbsUp size={13} /> : <ThumbsDown size={13} />}{rating === 'helpful' ? '有帮助' : '需改进'}
    </button>)}
    {busy ? <Loader2 size={14} className="animate-spin text-brand-strong" /> : saved && <span role="status" className="flex items-center gap-1 text-[10px] text-brand-strong"><Check size={12} />反馈已保存</span>}
    {!saved && <label className="w-full flex flex-wrap items-center gap-2 text-[11px] text-neutral-600">
      <input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)} disabled={busy} />
      同时记为偏好
      {remember && <>
        <input value={preferenceText} onChange={event => setPreferenceText(event.target.value)} maxLength={1000} placeholder="例如：聚餐优先清淡" className="plango-field flex-1 min-w-[160px]" />
        <select aria-label="偏好类型" value={polarity} onChange={event => setPolarity(event.target.value as 'positive' | 'negative')} className="plango-field w-28">
          <option value="positive">我偏好</option>
          <option value="negative">希望避开</option>
        </select>
      </>}
    </label>}
    {error && <p role="alert" className="w-full text-xs text-amber-700">{error}</p>}
  </div>
}
