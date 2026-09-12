import { formatCarryOutText, carryOutFromPlan } from '@shared/carryOut'
import { userMessage } from '@shared/userMessages'
import { DialogShell } from './DialogShell'
import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { X, QrCode, RefreshCw, ThumbsUp, ThumbsDown, Meh, Wand2, Copy, Check } from 'lucide-react'

interface Feedback {
  found: boolean
  views: number
  tally: { up: number; meh: number; down: number }
  prefs: { member: string; idea: string; budget?: number; ts: number }[]
  mergeInstruction: string
}

// 发给同行人：本地生成分享→二维码+局域网URL；实时轮询朋友的投票/评语；一键把意见并入方案。
export function ShareModal(): JSX.Element | null {
  const plan = useStore((s) => s.sharePlan)
  const close = useStore((s) => s.closeShare)
  const city = useStore((s) => s.city)
  const send = useStore((s) => s.send)
  const run = useStore((s) => s.run)
  const editRequirements = useStore((s) => s.editRequirements)
  const [state, setState] = useState<{ id: string; url: string; qr: string } | null>(null)
  const [error, setError] = useState('')
  const [fb, setFb] = useState<Feedback | null>(null)
  const [copied, setCopied] = useState(false)
  const [copiedPlan, setCopiedPlan] = useState(false)
  const [feedbackError, setFeedbackError] = useState('')
  const [merging, setMerging] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!plan) return
    let alive = true
    setState(null)
    setError('')
    setFb(null)
    setFeedbackError('')
    setCopied(false)
    window.plango
      .shareCreate({ plan, city })
      .then((r) => {
        if (!alive) return
        if (!r.ok || !r.id || !r.url) {
          setError(userMessage(r.error || '创建分享失败', 'share'))
          return
        }
        setState({ id: r.id, url: r.url, qr: r.qr || '' })
        timer.current = setInterval(async () => {
          try {
            const f = await window.plango.shareFeedback(r.id!)
            if (alive) { setFb(f); setFeedbackError('') }
          } catch { if (alive) setFeedbackError('反馈暂时无法刷新，正在重试。') }
        }, 3000)
      })
      .catch((e) => alive && setError(userMessage(e, 'share')))
    return () => {
      alive = false
      if (timer.current) clearInterval(timer.current)
    }
  }, [plan, city])

  if (!plan) return null

  const total = fb ? fb.tally.up + fb.tally.meh + fb.tally.down : 0
  const merge = async (): Promise<void> => {
    if (!fb?.mergeInstruction || merging) return
    const current = useStore.getState()
    if (current.busy || current.pendingDelivery || !current.backendReady) return
    const budgets = fb.prefs.map((item) => item.budget).filter((value): value is number => typeof value === 'number' && value > 0)
    const ideas = fb.prefs.map((item) => item.idea.trim()).filter(Boolean)
    const voteNote = fb.tally.down > fb.tally.up ? '多数朋友对当前方案有保留。' : ''
    const note = [...ideas.slice(0, 5), voteNote].filter(Boolean).join('；')
    setMerging(true)
    try {
      if (budgets.length) {
        if (!run?.version) {
          useStore.setState({ backendError: '当前任务无法写入人均预算，请先打开对应方案后再并入。' })
          return
        }
        await editRequirements({ expected_version: run.version, fields: { per_person_budget: Math.min(...budgets) } })
      }
      if (note) {
        const started = Date.now()
        let sent = false
        while (Date.now() - started < 180000) {
          const idle = useStore.getState()
          if (!idle.busy && !idle.pendingDelivery && idle.backendReady && idle.composerReady) {
            await send(`朋友想法：${note}`)
            sent = true
            break
          }
          await new Promise(resolve => setTimeout(resolve, 400))
        }
        if (!sent) useStore.setState({ backendError: '人均预算已写入；朋友想法请等当前任务空闲后再发，或直接在对话里补充。' })
      }
      close()
    } finally {
      setMerging(false)
    }
  }
  const copy = (): void => {
    if (!state) return
    navigator.clipboard.writeText(state.url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }).catch(() => setFeedbackError('链接复制失败，可选中链接手动复制。'))
  }
  const copyPlan = (): void => {
    navigator.clipboard.writeText(formatCarryOutText(carryOutFromPlan(plan))).then(() => {
      setCopiedPlan(true)
      setFeedbackError('')
      setTimeout(() => setCopiedPlan(false), 1600)
    }).catch(() => setFeedbackError('出行长文复制失败，请重试。'))
  }

  return (
    <DialogShell label="发给同行人确认" onClose={close} className="w-[680px]">
        <div className="plango-panel-header shrink-0">
          <QrCode size={16} className="text-brand-ink" />
          <div className="ml-3"><div className="plango-kicker">BETTER TOGETHER</div><h2 className="font-semibold text-lg mt-1">发给同行人确认</h2></div>
          <button onClick={close} aria-label="关闭分享" className="ml-auto plango-icon-button">
            <X size={16} />
          </button>
        </div>

        <div className="p-6 grid grid-cols-2 gap-6 overflow-y-auto">
          {/* 左：二维码 + 链接 */}
          <div>
            {error ? (
              <div className="text-sm text-red-500 py-8 text-center">{error}</div>
            ) : !state ? (
              <div className="text-sm text-neutral-400 py-8 text-center">生成分享中…</div>
            ) : (
              <>
                <div className="rounded-xl border border-neutral-200 p-3 flex items-center justify-center bg-white">
                  {state.qr ? <img src={state.qr} alt="扫码查看" className="w-[210px] h-[210px]" /> : <div className="text-xs text-neutral-400">二维码生成失败</div>}
                </div>
                <div className="mt-2 text-[11px] text-neutral-500 leading-relaxed">
                  📱 让朋友用手机<b>连同一 WiFi</b>扫码，即可查看方案、投票、留想法。
                </div>
                <div className="mt-2 flex items-center gap-1">
                  <input readOnly aria-label="分享链接" value={state.url} className="min-w-0 flex-1 text-[11px] px-2 py-1.5 rounded-lg border border-neutral-200 bg-neutral-50" />
                  <button onClick={copy} className="p-1.5 rounded-lg border border-neutral-200 hover:bg-neutral-100" title="复制链接">
                    {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
                  </button>
                </div>
              </>
            )}
            <button type="button" onClick={copyPlan} className="mt-2 w-full text-[11px] py-1.5 rounded-lg border border-neutral-200 bg-white hover:bg-neutral-50">
              {copiedPlan ? '出行长文已复制' : '复制出行长文（不依赖同一 WiFi）'}
            </button>
          </div>

          {/* 右：实时反馈 */}
          <div className="flex flex-col">
            <div className="flex items-center gap-1.5 text-sm font-semibold mb-2">
              朋友反馈
              <RefreshCw size={12} className="text-neutral-400" />
              {fb && <span className="text-[11px] text-neutral-400 font-normal ml-auto">{fb.views} 次查看</span>}
            </div>

            {feedbackError && <p role="status" className="text-xs text-amber-700 mb-3">{feedbackError}</p>}
            <div className="grid grid-cols-3 gap-1.5 mb-3">
              <VoteStat icon={<ThumbsUp size={14} />} n={fb?.tally.up ?? null} label="可以" color="text-green-600 bg-green-50" />
              <VoteStat icon={<Meh size={14} />} n={fb?.tally.meh ?? null} label="一般" color="text-neutral-600 bg-neutral-100" />
              <VoteStat icon={<ThumbsDown size={14} />} n={fb?.tally.down ?? null} label="不行" color="text-red-500 bg-red-50" />
            </div>

            <div className="flex-1 overflow-y-auto space-y-1.5 min-h-[80px]">
              {fb && fb.prefs.length > 0 ? (
                fb.prefs.map((p, i) => (
                  <div key={i} className="rounded-lg bg-neutral-50 border border-neutral-200 px-2.5 py-1.5 text-xs">
                    <span className="font-medium">{p.member}</span>
                    {p.budget ? <span className="text-brand-ink ml-1">期望人均¥{p.budget}</span> : null}
                    {p.idea ? <div className="text-neutral-600 mt-0.5">{p.idea}</div> : null}
                  </div>
                ))
              ) : (
                <div className="text-[11px] text-neutral-400 text-center py-6">
                  {total ? '暂无文字想法' : '等待朋友查看与反馈…'}
                </div>
              )}
            </div>

            <button
              onClick={() => void merge()}
              disabled={!fb?.mergeInstruction || merging}
              className="mt-3 flex items-center justify-center gap-1.5 py-2 rounded-xl bg-brand text-brand-ink font-medium text-sm disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Wand2 size={14} /> 把朋友意见并入方案
            </button>
          </div>
        </div>
    </DialogShell>
  )
}

function VoteStat({ icon, n, label, color }: { icon: JSX.Element; n: number | null; label: string; color: string }): JSX.Element {
  return (
    <div className={`rounded-xl px-2 py-2 text-center ${color}`}>
      <div className="flex items-center justify-center gap-1 text-base font-bold">
        {icon}
        {n ?? '—'}
      </div>
      <div className="text-[10px] mt-0.5 opacity-80">{label}</div>
    </div>
  )
}
