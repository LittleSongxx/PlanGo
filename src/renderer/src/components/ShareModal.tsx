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
  const [state, setState] = useState<{ id: string; url: string; qr: string } | null>(null)
  const [error, setError] = useState('')
  const [fb, setFb] = useState<Feedback | null>(null)
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!plan) return
    let alive = true
    setState(null)
    setError('')
    setFb(null)
    window.plango
      .shareCreate({ plan, city })
      .then((r) => {
        if (!alive) return
        if (!r.ok || !r.id || !r.url) {
          setError(r.error || '创建分享失败')
          return
        }
        setState({ id: r.id, url: r.url, qr: r.qr || '' })
        timer.current = setInterval(async () => {
          const f = await window.plango.shareFeedback(r.id!)
          if (alive) setFb(f)
        }, 3000)
      })
      .catch((e) => alive && setError(String(e)))
    return () => {
      alive = false
      if (timer.current) clearInterval(timer.current)
    }
  }, [plan, city])

  if (!plan) return null

  const total = fb ? fb.tally.up + fb.tally.meh + fb.tally.down : 0
  const merge = (): void => {
    if (!fb?.mergeInstruction) return
    void send(`【并入朋友意见】${fb.mergeInstruction}`)
    close()
  }
  const copy = (): void => {
    if (!state) return
    navigator.clipboard?.writeText(state.url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={close}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-[560px] max-w-[92vw] max-h-[88vh] overflow-y-auto bg-white rounded-2xl shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-white h-12 flex items-center px-4 border-b border-neutral-200 rounded-t-2xl">
          <QrCode size={16} className="text-brand-ink" />
          <span className="font-semibold text-sm ml-2">发给同行人确认</span>
          <button onClick={close} className="ml-auto p-1.5 rounded hover:bg-neutral-100">
            <X size={16} />
          </button>
        </div>

        <div className="p-4 grid grid-cols-2 gap-4">
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
                  <input readOnly value={state.url} className="flex-1 text-[11px] px-2 py-1.5 rounded-lg border border-neutral-200 bg-neutral-50" />
                  <button onClick={copy} className="p-1.5 rounded-lg border border-neutral-200 hover:bg-neutral-100" title="复制链接">
                    {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
                  </button>
                </div>
              </>
            )}
          </div>

          {/* 右：实时反馈 */}
          <div className="flex flex-col">
            <div className="flex items-center gap-1.5 text-sm font-semibold mb-2">
              朋友反馈
              <RefreshCw size={12} className="text-neutral-400" />
              {fb && <span className="text-[11px] text-neutral-400 font-normal ml-auto">{fb.views} 次查看</span>}
            </div>

            <div className="grid grid-cols-3 gap-1.5 mb-3">
              <VoteStat icon={<ThumbsUp size={14} />} n={fb?.tally.up ?? 0} label="可以" color="text-green-600 bg-green-50" />
              <VoteStat icon={<Meh size={14} />} n={fb?.tally.meh ?? 0} label="一般" color="text-neutral-600 bg-neutral-100" />
              <VoteStat icon={<ThumbsDown size={14} />} n={fb?.tally.down ?? 0} label="不行" color="text-red-500 bg-red-50" />
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
              onClick={merge}
              disabled={!fb?.mergeInstruction}
              className="mt-3 flex items-center justify-center gap-1.5 py-2 rounded-xl bg-brand text-brand-ink font-medium text-sm disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Wand2 size={14} /> 把朋友意见并入方案
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function VoteStat({ icon, n, label, color }: { icon: JSX.Element; n: number; label: string; color: string }): JSX.Element {
  return (
    <div className={`rounded-xl px-2 py-2 text-center ${color}`}>
      <div className="flex items-center justify-center gap-1 text-base font-bold">
        {icon}
        {n}
      </div>
      <div className="text-[10px] mt-0.5 opacity-80">{label}</div>
    </div>
  )
}
