import { useState } from 'react'
import { Bell, Calendar, Copy, ImageDown } from 'lucide-react'
import { carryOutFromPlan, carryOutReminderAt, formatCarryOutText } from '@shared/carryOut'
import type { Plan } from '@shared/types'
import { userMessage } from '@shared/userMessages'

export function CarryOutBar({ plan }: { plan: Plan }): JSX.Element {
  const carry = carryOutFromPlan(plan)
  const reminderAt = carryOutReminderAt(plan)
  const [busy, setBusy] = useState<'copy' | 'ics' | 'image' | 'remind' | null>(null)
  const [copied, setCopied] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  const run = async (kind: typeof busy, work: () => Promise<string | void>): Promise<void> => {
    if (busy) return
    setBusy(kind)
    setError('')
    setStatus('')
    try {
      const next = await work()
      if (next) setStatus(next)
    } catch (value) {
      setError(userMessage(value, 'carry'))
    } finally {
      setBusy(null)
    }
  }

  const copy = (): void => {
    void run('copy', async () => {
      await navigator.clipboard.writeText(formatCarryOutText(carry))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
      return '已复制，可直接粘贴到微信。'
    })
  }

  const save = (kind: 'ics' | 'image'): void => {
    void run(kind, async () => {
      const result = kind === 'ics' ? await window.plango.carryOut.saveIcs(plan) : await window.plango.carryOut.saveImage(plan)
      if (!result.ok) throw new Error(result.error || '导出未完成')
      if (result.canceled) return
      return result.path ? `已保存到 ${result.path.split(/[\\/]/).pop()}` : '已保存。'
    })
  }

  const remind = (): void => {
    if (!reminderAt) return
    void run('remind', async () => {
      await window.plango.reminders.create(formatCarryOutText(carry).slice(0, 2000), reminderAt)
      return '已写入提醒，应用打开时会显示。'
    })
  }

  return (
    <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--surface-soft)] p-3 space-y-2" aria-label="带走这次安排">
      <div className={`text-xs font-semibold ${carry.verdict === 'go' ? 'text-emerald-800' : 'text-amber-800'}`}>{carry.headline}</div>
      {carry.verdict === 'go' && !!carry.pending.length && (
        <p className="text-[11px] leading-5 text-[var(--muted)]">仍要现场核对：{carry.pending.slice(0, 4).join('；')}{carry.pending.length > 4 ? '…' : ''}</p>
      )}
      {carry.verdict === 'hold' && <p className="text-[11px] leading-5 text-amber-800">可以先复制，但还不能当作最终出门安排。</p>}
      <div className="flex flex-wrap gap-1.5">
        <button type="button" disabled={!!busy} onClick={copy} className="text-[11px] px-2.5 py-1 rounded-full bg-brand text-brand-ink font-medium disabled:opacity-40 inline-flex items-center gap-1">
          <Copy size={12} />{copied ? '已复制' : '复制安排'}
        </button>
        <button type="button" disabled={!!busy} onClick={() => save('ics')} className="text-[11px] px-2.5 py-1 rounded-full bg-white border border-[var(--line)] disabled:opacity-40 inline-flex items-center gap-1">
          <Calendar size={12} />加入日历
        </button>
        <button type="button" disabled={!!busy} onClick={() => save('image')} className="text-[11px] px-2.5 py-1 rounded-full bg-white border border-[var(--line)] disabled:opacity-40 inline-flex items-center gap-1">
          <ImageDown size={12} />保存图片
        </button>
        {reminderAt && (
          <button type="button" disabled={!!busy} onClick={remind} className="text-[11px] px-2.5 py-1 rounded-full bg-white border border-[var(--line)] disabled:opacity-40 inline-flex items-center gap-1">
            <Bell size={12} />出门前一小时
          </button>
        )}
      </div>
      {status && <p role="status" className="text-[11px] text-[var(--muted)] break-all">{status}</p>}
      {error && <p role="alert" className="text-[11px] text-amber-800 break-words">{error}</p>}
    </div>
  )
}
