import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { publicStatus, userMessage } from '@shared/userMessages'
import { canResolveAction, readProgress } from '../lib/harnessProjection'
import type { OutcomeCard, Plan, DishReco, ReceiptItem, SourceTag, GroupBuyPackage, HarnessEvidence } from '@shared/types'
import { SourceBadge } from './SourceBadge'
import { PlanMap } from './PlanMap'
import { PoiImage } from './PoiImage'
import { ResultFeedback } from './ResultFeedback'
import { DraftReviewCard } from './DraftReviewCard'
import { RequirementsCard } from './RequirementsCard'
import { OfferComparisonCard } from './OfferComparisonCard'
import { MapPin, Clock, Utensils, Ticket, CheckCircle2, XCircle, AlertTriangle, Send, ListChecks, Wallet, Globe, Navigation, Mic, MicOff, ArrowRight, Sparkles, FileText } from 'lucide-react'

const cardPriority = (card: OutcomeCard): number => card.kind === 'confirm' || card.kind === 'draft_review' ? 2 : card.kind === 'task_answer' || card.kind === 'booking_preview' || card.kind === 'preparation' && card.current ? 1 : 0

export function OutcomeCanvas(): JSX.Element {
  const cards = useStore((s) => s.cards)
  const run = useStore((s) => s.run)
  const setView = useStore((s) => s.setView)
  const refreshRun = useStore((s) => s.refreshRun)
  const reading = readProgress(run)
  return (
    <div className="h-full flex flex-col bg-[var(--surface-soft)]">
      <div className="min-h-[52px] shrink-0 flex items-center gap-2 px-5 border-b border-[var(--line)] bg-white/75">
        <ListChecks size={16} className="text-brand-ink" />
        <span className="text-xs font-medium text-brand-ink">本次安排</span>
        <span className="text-[11px] text-[var(--muted)] hidden xl:inline">资料、方案与需要你确认的事项</span>
        <div className="ml-auto flex items-center gap-1">
          {cards.length > 0 && (
            <button onClick={() => void refreshRun()} className="text-xs flex items-center gap-1 px-2 py-1 rounded-lg text-neutral-500 hover:bg-neutral-100" title="从运行服务重新加载成果">
              <ListChecks size={13} /> 刷新
            </button>
          )}
          <button onClick={() => setView('browser')} className="text-xs flex items-center gap-1 px-2 py-1 rounded-lg text-neutral-500 hover:bg-neutral-100">
            <Globe size={13} /> 回到浏览器
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-5 lg:p-6">
        <RequirementsCard />
        {run?.offer_comparison_error && <div role="status" className="max-w-4xl mx-auto mb-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-800">{userMessage(run.offer_comparison_error, 'offer')}{run.selected_offer && <p className="mt-1 text-xs">你之前选择的优惠仍保留在这次安排中。</p>}</div>}
        {!!(reading.observed.length || reading.missing.length) && <div aria-label="资料读取范围" className="max-w-4xl mx-auto plango-card p-4 mb-5 text-sm leading-6 space-y-2">
          {!!reading.observed.length && <div className="flex items-start gap-3"><span className="text-brand-strong font-medium shrink-0">已读</span><span>{reading.observed.join('、')}</span></div>}
          {!!reading.missing.length && <div className="flex items-start gap-3"><span className="text-amber-800 font-medium shrink-0">仍需核对</span><span className="text-neutral-600">{reading.missing.map(field => `${field}${reading.partial.includes(field) ? '（仅取得部分条件）' : ''}`).join('、')}</span></div>}
        </div>}
        {cards.length === 0 && run?.outcome ? <div className="max-w-4xl mx-auto plango-card p-6"><h2 className="text-base font-semibold">本轮尚无可展示的成果</h2><p className="text-sm text-[var(--muted)] leading-6 mt-3">{publicStatus(String(run.state.reason || '你可以在对话中查看任务记录，调整需求后继续。'))}</p></div> : cards.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center px-5">
            <div aria-hidden="true" className="relative w-32 h-28 mb-7"><div className="absolute left-4 top-3 w-20 h-24 rounded-2xl border border-brand/50 bg-brand-soft rotate-[-10deg]" /><div className="absolute right-3 top-1 w-20 h-24 rounded-2xl border border-[var(--line)] bg-white shadow-panel rotate-[8deg] flex items-center justify-center"><FileText size={30} className="text-brand-strong" /></div><span className="absolute bottom-0 right-0 h-10 w-10 rounded-2xl bg-brand text-brand-ink flex items-center justify-center"><Sparkles size={18} /></span></div>
            <div className="plango-kicker">A LITTLE PLANNING, A BETTER DAY</div>
            <h2 className="text-[25px] leading-9 tracking-[-0.6px] font-semibold text-brand-ink mt-3">把想去的地方，变成清楚的安排</h2>
            <p className="text-[13px] leading-6 text-[var(--muted)] max-w-sm mt-3">在右侧说说人数、时间和偏好。查到的资料、可调整的方案与执行记录，会逐步汇集到这里。</p>
            <button onClick={() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务输入"]')?.focus()} className="plango-primary mt-7">从对话开始<ArrowRight size={15} /></button>
            <div className="flex items-center gap-5 mt-9 text-[11px] text-[var(--muted)]"><span>有来源的资料</span><span className="h-1 w-1 rounded-full bg-[#d8d4ca]" /><span>可以继续修改</span><span className="h-1 w-1 rounded-full bg-[#d8d4ca]" /><span>由你确认关键操作</span></div>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto space-y-5">
            {cards.map((c, i) => ({ c, i })).reverse().sort((a, b) => cardPriority(b.c) - cardPriority(a.c)).map(({ c, i }) => (
              <CardView key={`${c.kind}-${i}`} card={c} />
            ))}
          </div>
        )}
        {run?.outcome && <div className="mt-5"><ResultFeedback /></div>}
      </div>
    </div>
  )
}

function CardView({ card }: { card: OutcomeCard }): JSX.Element {
  const busy = useStore(state => state.busy)
  const decideDraft = useStore(state => state.decideDraft)
  const resumePreparation = useStore(state => state.resumePreparation)
  switch (card.kind) {
    case 'task_answer':
      return <Card accent>
        <div className="plango-kicker">{card.complete ? '本次答复' : '已完成的部分'}</div>
        <p className="mt-3 text-sm leading-7 whitespace-pre-wrap break-words">{card.text}</p>
        {!!card.citations.length && <details className="mt-4 border-t border-[var(--line)] pt-3 text-sm">
          <summary className="cursor-pointer text-[var(--muted)]">查看引用资料</summary>
          {card.citations.map((citation, index) => <blockquote key={index} className="mt-3 border-l-2 border-brand/40 pl-3">
            <p className="font-medium">{citation.title}</p><p className="mt-1 text-neutral-600 whitespace-pre-wrap break-words">{citation.quote}</p>
            {citation.url && <button onClick={() => useStore.getState().navigateInApp(citation.url)} className="mt-2 text-brand-strong underline">打开来源页面</button>}
          </blockquote>)}
        </details>}
      </Card>
    case 'booking_preview':
      return <Card accent>
        <div className="flex items-center justify-between gap-3"><div><div className="plango-kicker">预约条件预览</div><h3 className="mt-1 font-semibold text-lg">{card.merchant ? `${card.merchant} · ` : ''}{card.complete ? '参数显示已核对' : '参数尚待核对'}</h3></div><SourceBadge source="browser" /></div>
        <div className="my-4 rounded-xl border border-[var(--line)] bg-[var(--surface-soft)] p-4"><p className="font-medium">{card.partySize ?? '待确认'} 人 · {card.date} {card.time} · 北京时间</p><p className="mt-2 text-xs leading-6 text-[var(--muted)]">网页显示：{card.labels.filter(Boolean).join(' · ') || '尚未取得完整控件'}<br />年份来自预填链接，网页控件只显示星期、月、日。</p></div>
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">未查询空位，未提交预约。网站购物车和查询流程已阻断；这些参数不能证明有位或预订成功。</p>
        <p className="mt-3 text-xs text-[var(--muted)]">核对记录：{card.observedAt ? new Date(card.observedAt).toLocaleString('zh-CN') : '时间未知'}</p>
        <button onClick={() => useStore.getState().navigateInApp(card.sourceUrl)} className="mt-3 text-sm font-medium text-brand-strong underline underline-offset-4">打开受保护的参数页面</button>
      </Card>
    case 'draft_review':
      return <DraftReviewCard draft={card.draft} busy={busy} onDecision={decision => void decideDraft(card.draft.runId, card.draft.interruptId, card.draft.planId, card.draft.planVersion, decision)} />
    case 'preparation':
      return <Card accent>
        <div className="flex items-center gap-3"><span className={`flex h-10 w-10 items-center justify-center rounded-xl ${card.ready ? 'bg-brand-soft text-brand-strong' : 'bg-amber-50 text-amber-700'}`}>{card.ready ? <ListChecks size={20} /> : <Clock size={20} />}</span><div><div className="plango-kicker">准备核对</div><h3 className="font-semibold text-base mt-1">{card.ready ? '已准备，待你复核' : '准备事项尚待完善'}</h3></div><SourceBadge source="browser" /></div>
        <p className="text-[13px] leading-6 text-neutral-600 mt-4">{card.summary}</p>
        {!!card.pendingChecks?.length && <div className="mt-3 p-4 rounded-xl border border-amber-100 bg-amber-50 text-xs leading-6 text-amber-800"><b>计划仍有待核验事项</b><ul className="list-disc pl-4">{card.pendingChecks.map((note, index) => <li key={index}>{note}</li>)}</ul></div>}
        {card.entries.map((entry, index) => <div key={index} className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--surface-soft)] p-4"><h4 className="font-semibold text-sm">{entry.name}</h4><p className="text-xs text-[var(--muted)] mt-1">{entry.address}</p><p className="text-sm mt-3">{entry.partySize === undefined ? '人数待核对' : `${entry.partySize} 人`} · {entry.date} {entry.time} {entry.timezone === 'Asia/Shanghai' ? '北京时间' : entry.timezone}</p></div>)}
        {card.issues.map((issue, index) => <div key={index} className={`mt-3 rounded-xl border p-3 text-xs leading-6 ${issue.mismatch ? 'border-red-100 bg-red-50 text-red-700' : 'border-amber-100 bg-amber-50 text-amber-800'}`}><b>{issue.name} · {issue.mismatch ? '需要修正' : '待补充核对'}</b><div>{issue.detail}</div></div>)}
        <div className="mt-4 flex items-center justify-between gap-4 border-t border-[var(--line)] pt-4"><div className="text-[11px] text-[var(--muted)] leading-5">尚未提交预约、订单或付款。<br />{card.observedAt ? `核对记录：${new Date(card.observedAt).toLocaleString('zh-CN')}` : '核对时间未知'}</div><div className="flex flex-wrap gap-2"><button onClick={() => useStore.getState().setView('browser')} className="inline-flex items-center gap-2 rounded-xl border border-[var(--line)] px-3 py-2 text-xs bg-white"><Globe size={14} />打开浏览器核对</button>{card.resume && <button disabled={busy || !card.resume.can_resume} onClick={() => void resumePreparation(card.resume!.run_id, card.resume!.plan_id, card.resume!.plan_version, card.resume!.approval_id)} className="plango-primary disabled:opacity-40">继续核对表单</button>}</div></div>
        {card.resume?.can_resume && <p className="mt-3 text-[11px] text-[var(--muted)]">继续会重新读取原方案的表单，并在填写前请你确认。</p>}
        {card.resume && !card.resume.can_resume && !!card.resume.blockers.length && <p className="mt-3 text-xs text-amber-700">{card.resume.blockers.join('；')}</p>}
      </Card>
    case 'browser_page':
      return <BrowserPageCard card={card} />
    case 'evidence':
      return <EvidenceCard items={card.items} />
    case 'plan':
      return <PlanCard plan={card.plan} />
    case 'plans':
      return <PlansCard variants={card.variants} city={card.city} budget={card.budget} />
    case 'price_comparison':
      return <PriceComparisonCard card={card} />
    case 'dishes':
      return <DishesCard shopName={card.shopName} dishes={card.dishes} mode={card.mode} source={card.source} />
    case 'groupbuy':
      if (card.comparison && card.runId && card.version) return <OfferComparisonCard comparison={card.comparison} runId={card.runId} version={card.version} />
      return <GroupBuyCard shopName={card.shopName} packages={card.packages} source={card.source} />
    case 'receipt':
      return <ReceiptCard items={card.items} shareMessage={card.shareMessage} />
    case 'confirm':
      return <ConfirmCard token={card.token} title={card.title} detail={card.detail} danger={card.danger} />
    default:
      return <></>
  }
}

function Card({ children, accent }: { children: React.ReactNode; accent?: boolean }): JSX.Element {
  return <div className={`plango-card ${accent ? 'border-brand/60 shadow-panel' : ''} p-5 animate-in`}>{children}</div>
}

function BrowserPageCard({ card }: { card: Extract<OutcomeCard, { kind: 'browser_page' }> }): JSX.Element {
  const preview = card.scope === 'booking_parameters'
  const structured = preview || !!(card.places?.length || card.menuCount || card.offerCount)
  return <Card>
    <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="plango-kicker">{preview ? '参数页面来源' : card.scope ? '识别记录' : '页面资料'}</div><h3 className="mt-1 text-lg leading-7 font-semibold break-words">{card.title}</h3></div><SourceBadge source={card.source || 'browser'} /></div>
    <div className="text-xs text-[var(--muted)] mt-2">读取于 {card.observedAt ? new Date(card.observedAt).toLocaleString('zh-CN') : '时间未知'}</div>
    {card.scope === 'image_text' && <p className="text-sm leading-6 text-amber-700 mt-3">图片识别：内容来自你提供的图片，价格和商家信息尚未实时核验。</p>}
    {card.scope === 'visual_observation' && <p className="text-sm leading-6 text-amber-700 mt-3">截图理解：仅描述画面，不代表已核验商家事实或完成业务操作。</p>}
    {!preview && card.places?.map((place, index) => <div key={index} className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--surface-soft)] p-4 text-sm leading-6">
      {card.places!.length > 1 && <h4 className="font-semibold mb-2">{place.name}</h4>}
      <div className="flex items-start gap-2"><MapPin size={16} className="mt-1 shrink-0 text-brand-strong" /><span>{place.address || '门店地址待核验'}</span></div>
      <div className="mt-2 flex items-center gap-2"><Wallet size={16} className="shrink-0 text-brand-strong" /><span>{place.averagePrice === undefined ? '人均费用待核验' : `页面人均 ¥${place.averagePrice}`}</span></div>
    </div>)}
    {!preview && structured && <div className="mt-4 flex flex-wrap gap-2 text-xs"><span className="rounded-lg bg-brand-soft px-3 py-2 text-brand-ink">菜品摘录 {card.menuCount || 0} 项</span><span className="rounded-lg bg-brand-soft px-3 py-2 text-brand-ink">{card.offerCount ? `套餐摘录 ${card.offerCount} 项` : '套餐条件待核验'}</span>{!card.places?.length && <span className="rounded-lg bg-amber-50 px-3 py-2 text-amber-800">门店地址待核验</span>}</div>}
    <details open={!structured} className="mt-4 border-t border-[var(--line)] pt-3 text-sm">
      <summary className="cursor-pointer text-[var(--muted)] py-1">查看原始网页摘录与来源</summary>
      {card.rawTitle && <p className="mt-3 text-xs leading-6 text-neutral-500 break-words">网页原标题：{card.rawTitle}</p>}
      {card.places?.filter(place => place.quote).map((place, index) => <blockquote key={index} className="mt-3 border-l-2 border-brand/40 pl-3 text-sm leading-6 text-neutral-600 whitespace-pre-wrap break-words">{place.quote}</blockquote>)}
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-7 text-neutral-700 bg-[var(--surface-soft)] rounded-xl border border-[var(--line)] p-4 mt-3">{card.text}</pre>
    </details>
    {card.limitations?.map((limitation, index) => <p key={index} className="text-sm leading-6 text-neutral-600 mt-2">{limitation}</p>)}
    {/^https?:\/\//i.test(card.url) && <button onClick={() => useStore.getState().navigateInApp(card.url)} className="text-sm font-medium text-brand-strong underline underline-offset-4 mt-4">查看原始页面</button>}
  </Card>
}

// SVG 五维雷达图（移植 weplan ui.js radar）。scores 为 0-100，labels 省钱/好玩/便捷/合适/特色。
function RadarChart({ radar, size = 168 }: { radar: Record<string, number>; size?: number }): JSX.Element {
  const labels = Object.keys(radar)
  const vals = labels.map((k) => Math.max(0.06, Math.min(1, (radar[k] || 0) / 100)))
  const n = labels.length || 5
  const cx = size / 2
  const cy = size / 2
  const r = size * 0.34
  const pt = (i: number, frac: number): [number, number] => {
    const ang = -Math.PI / 2 + (i * 2 * Math.PI) / n
    return [cx + r * frac * Math.cos(ang), cy + r * frac * Math.sin(ang)]
  }
  const gridRings = [0.25, 0.5, 0.75, 1].map((f, gi) => (
    <polygon key={gi} points={labels.map((_, i) => pt(i, f).join(',')).join(' ')} fill="none" stroke="#e8e8e8" strokeWidth={1} />
  ))
  const axes = labels.map((_, i) => {
    const [x, y] = pt(i, 1)
    return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="#f0f0f0" strokeWidth={1} />
  })
  const dataPts = vals.map((v, i) => pt(i, v).join(',')).join(' ')
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {gridRings}
      {axes}
      <polygon points={dataPts} fill="rgba(255,209,0,.24)" stroke="#b77900" strokeWidth={2} strokeLinejoin="round" />
      {vals.map((v, i) => {
        const [x, y] = pt(i, v)
        return <circle key={i} cx={x} cy={y} r={3} fill="#b77900" />
      })}
      {labels.map((l, i) => {
        const [x, y] = pt(i, 1.26)
        const score = Math.round(radar[l] || 0)
        return (
          <text key={l} x={x} y={y} fontSize={11} fontWeight={700} fill="#6b7280" textAnchor="middle" dominantBaseline="middle">
            {l} {score}
          </text>
        )
      })}
    </svg>
  )
}

// Candidate plans retain their own identity and approval version.
function PlansCard({
  variants,
  city,
  budget
}: {
  variants: { plan: Plan; styleLabel: string; per: number | null; overBudget?: number }[]
  city: string
  budget?: number
}): JSX.Element {
  const [tab, setTab] = useState(0)
  const send = useStore((s) => s.send)
  const openShare = useStore((s) => s.openShare)
  const active = variants[tab]
  if (!active) return <></>
  return (
    <Card accent>
      <div className="flex items-center gap-1.5 mb-2">
        <MapPin size={15} className="text-brand-ink" />
        <span className="font-semibold text-[15px]">{city} · 行程方案 · {variants.length} 套</span>
      </div>

      {/* Tab 切换 */}
      <div className="grid grid-cols-3 gap-2 mb-4">
        {variants.map((v, i) => (
          <button
            key={i}
            onClick={() => setTab(i)} aria-pressed={i === tab}
            className={`rounded-xl border px-2 py-2 text-left transition-colors ${
              i === tab ? 'border-brand bg-brand-soft shadow-card' : 'border-[var(--line)] bg-white hover:border-brand'
            }`}
          >
            <div className="text-xs font-semibold flex items-center gap-1">
              {v.styleLabel}
              {v.overBudget ? <span className="text-[9px] text-amber-600">超¥{v.overBudget}</span> : budget && v.per !== null && v.per <= budget ? <span className="text-[9px] text-green-600">已知估算未超</span> : null}
            </div>
            <div className="text-[11px] text-neutral-500 mt-0.5">{v.per === null ? '费用待核验' : `人均¥${v.per}`}</div>
          </button>
        ))}
      </div>

      {/* 预算诚实横幅 */}
      {budget && active.per !== null ? (
        <div className={`mb-3 flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs ${active.overBudget ? 'bg-amber-50 text-amber-700' : 'bg-green-50 text-green-700'}`}>
          <Wallet size={13} />
          {active.overBudget ? `已知部分人均估算¥${active.per}，已超预算¥${budget}（+¥${active.overBudget}）。` : `已知部分人均估算¥${active.per}，未超预算¥${budget}；未估费用另计。`}
        </div>
      ) : null}

      <PlanCard plan={active.plan} embed />

      <div className="mt-3 flex flex-wrap gap-1.5">
        <button onClick={() => void useStore.getState().selectPlan(active.plan)} className="text-[11px] px-2.5 py-1 rounded-full bg-brand text-brand-ink font-medium">
          选这套 · 重新核验
        </button>
        <button onClick={() => openShare(active.plan)} className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-100 hover:bg-neutral-200 border border-neutral-200">
          发同行人确认
        </button>
        <button onClick={() => void send(`「${active.styleLabel}」这套里的正餐换一家更近的`)} className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-100 hover:bg-neutral-200 border border-neutral-200">
          换正餐
        </button>
      </div>

      {/* 针对当前方案的语音/文字修改框（仿 weplan plan-dock，绑定当前 Tab） */}
      <VariantEditBox label={active.styleLabel} planId={active.plan.plan_id} version={active.plan.version} />
    </Card>
  )
}

// 绑定当前方案的修改框：语音（Web Speech API）或输入文字，自动带上「只改这套」上下文。
function VariantEditBox({ label, planId, version }: { label: string; planId?: string; version?: number }): JSX.Element {
  const send = useStore((s) => s.send)
  const busy = useStore((s) => s.busy)
  const [text, setText] = useState('')
  const [listening, setListening] = useState(false)
  const recRef = useRef<any>(null)
  const supportsSpeech = typeof window !== 'undefined' && (window as any).webkitSpeechRecognition

  useEffect(() => {
    return () => {
      try {
        recRef.current?.stop?.()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const submit = (raw: string): void => {
    const msg = raw.trim()
    if (!msg || busy) return
    setText('')
    void send(`【修改方案 ${planId || label}，当前版本 ${version || 1}】${msg}；保留其它已确认的人数、预算、时间和人群约束，修改后重新校验。`)
  }

  const toggleMic = (): void => {
    if (!supportsSpeech) return
    if (listening) {
      try {
        recRef.current?.stop?.()
      } catch {
        /* ignore */
      }
      setListening(false)
      return
    }
    const Rec = (window as any).webkitSpeechRecognition
    const rec = new Rec()
    rec.lang = 'zh-CN'
    rec.interimResults = true
    rec.continuous = false
    let finalText = ''
    rec.onresult = (e: any) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript
        if (e.results[i].isFinal) finalText += t
        else interim += t
      }
      setText(finalText + interim)
    }
    rec.onerror = () => setListening(false)
    rec.onend = () => {
      setListening(false)
      if (finalText.trim()) submit(finalText)
    }
    recRef.current = rec
    setListening(true)
    try {
      rec.start()
    } catch {
      setListening(false)
    }
  }

  return (
    <div className="mt-3 pt-3 border-t border-dashed border-neutral-200">
      <div className="text-[11px] text-neutral-400 mb-1.5">
        直接对<span className="text-brand-ink font-medium">「{label}」</span>这套说改动，比如「正餐换清淡点的」「第一站换室内的」「预算再降50」
      </div>
      <div className="flex items-center gap-1.5">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit(text)
          }}
          placeholder={listening ? '🎙️ 正在听…' : `改「${label}」这套…`}
          className="plango-field flex-1 min-w-0"
        />
        {supportsSpeech && (
          <button
            onClick={toggleMic}
            title={listening ? '停止' : '语音输入'}
            className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center border transition-colors ${
              listening ? 'bg-red-500 text-white border-red-500 animate-pulse' : 'bg-white text-neutral-500 border-neutral-200 hover:border-brand'
            }`}
          >
            {listening ? <MicOff size={14} /> : <Mic size={14} />}
          </button>
        )}
        <button
          onClick={() => submit(text)}
          disabled={busy || !text.trim()}
          className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-brand text-brand-ink disabled:opacity-40"
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  )
}

function PlanCard({ plan, embed }: { plan: Plan; embed?: boolean }): JSX.Element {
  const catIcon = (c: string) => (c === 'dining' ? <Utensils size={13} /> : c === 'activity' ? <Ticket size={13} /> : <MapPin size={13} />)
  const openRoute = useStore((s) => s.openRoute)
  const coords = useStore((s) => s.coords)
  const city = useStore((s) => s.city)
  const body = (
    <>
      {!embed && (
        <div className="flex items-center justify-between mb-2">
          <div className="font-semibold text-[15px]">{plan.title}</div>
          <span className="text-xs px-2 py-0.5 rounded-full bg-brand/20 text-brand-ink">{styleLabel(plan.style)}</span>
        </div>
      )}
      <div className="text-xs text-neutral-500 mb-3 flex items-center gap-2 flex-wrap">
        {plan.visit_date && <span>{plan.visit_date}{plan.timezone === 'Asia/Shanghai' ? ' · 北京时间' : plan.timezone ? ` · ${plan.timezone}` : ''}</span>}
        <span>{plan.total_cost === null ? '费用待核验' : `已知估算小计 ¥${plan.total_cost}`} · {plan.nodes.length} 站{plan.party_size ? ` · ${plan.party_size} 人` : ''}{plan.version ? ` · v${plan.version}` : ''}</span>
        {plan.budget_limit === null ? <span>预算未设上限</span> : plan.budget_limit !== undefined ? <span>总预算 ¥{plan.budget_limit}</span> : null}
        {plan.total_distance_km ? <span className="flex items-center gap-0.5"><Navigation size={11} /> 全程约 {plan.total_distance_km}km</span> : null}
        {plan.total_travel_min ? <span className="flex items-center gap-0.5"><Clock size={11} /> 通勤约 {plan.total_travel_min}分钟</span> : null}
        {plan.source_mix && (
          <span>
            {Object.entries(plan.source_mix).map(([k]) => (
              <span key={k} className="mr-1">
                <SourceBadge source={k as never} />
              </span>
            ))}
          </span>
        )}
      </div>

      {plan.cost_breakdown && <div aria-label="费用明细" className="mb-4 rounded-xl border border-[var(--line)] bg-[var(--surface-soft)] p-3 text-xs leading-6">
        <div className="flex flex-wrap gap-x-5 gap-y-1">{([['餐饮', plan.cost_breakdown.dining], ['活动', plan.cost_breakdown.activities], ['去程交通', plan.cost_breakdown.transport]] as const).map(([label, value]) => <span key={label}>{label}：{value === null ? '待核对' : `约 ¥${value}`}</span>)}</div>
        {plan.cost_breakdown.pending.map(note => <p key={note} className="text-[var(--muted)]">{note}</p>)}
      </div>}
      {/* 地图（真实路网） */}
      <PlanMap plan={plan} />

      {/* 时间线 */}
      <div className="relative pl-4 space-y-3 mb-3">
        <div className="absolute left-[6px] top-1 bottom-1 w-px bg-neutral-200" />
        {plan.nodes.map((n) => (
          <div key={n.node_id} className="relative">
            <div className="absolute -left-[13px] top-1 w-3 h-3 rounded-full bg-brand border-2 border-white" />
            {n.distance_kind === 'straight_line_lower_bound' && n.distance_km != null ? <div className="text-[11px] text-amber-700 mb-1">↓ 直线至少 {distanceLabel(n.distance_km, true)}，路线待核验</div> : n.poi?.tags.includes('route_unknown') && <div className="text-[11px] text-amber-700 mb-1">↓ 路线与通勤时间待核验</div>}
            {n.distance_kind === 'route' && n.distance_km != null && !n.route_from_prev && <div className="text-[11px] text-[var(--muted)] mb-1">↓ 路线约 {distanceLabel(n.distance_km)}{n.transit_from_prev_min != null && n.transit_from_prev_min > 0 ? ` · 约 ${n.transit_from_prev_min} 分钟` : ''}</div>}
            {n.transport_summary && <details className="mb-2 text-xs leading-6 text-[var(--muted)]"><summary className="cursor-pointer">查看交通路线与费用依据</summary><p className="mt-1 whitespace-pre-wrap break-words">{n.transport_summary}</p></details>}
            {!n.poi?.tags.includes('route_unknown') && n.route_from_prev && (n.transit_from_prev_min ?? 0) > 0 && (
              <div className="text-[11px] text-neutral-400 mb-1">
                ↓ {n.route_from_prev.desc}
                {n.route_from_prev.distance_m ? ` · ${(n.route_from_prev.distance_m / 1000).toFixed(1)}km` : ''}
              </div>
            )}
            <div className="flex items-center gap-1.5 text-xs text-neutral-500">
              <Clock size={11} /> {n.time_start}
              {n.time_end && <>–{n.time_end}</>}
              <span className="flex items-center gap-0.5 ml-1 text-neutral-400">{catIcon(n.category)}</span>
              {n.verify_state === 'booked' && <span className="text-[10px] text-green-600">已预订</span>}
            </div>
            <div className="flex gap-2 mt-0.5">
              <PoiImage src={n.poi?.image} name={n.title} className="w-20 h-20 shrink-0 border border-[var(--line)]" />
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm">{n.title}</div>
                <div className="text-[11px] text-neutral-500 mt-0.5 flex items-center gap-1 flex-wrap">
                  {n.poi?.filtered_score != null && <span className="text-amber-600">★{n.poi.filtered_score}</span>}
                  {(n.poi?.raw_score && n.poi?.filtered_score == null) ? <span className="text-amber-600">★{n.poi.raw_score}</span> : null}
                  {n.poi?.price_per_person !== undefined ? <span>人均¥{n.poi.price_per_person}</span> : <span>价格待核验</span>}
                  {n.wait_min === null ? <span className="text-amber-700">排队待核验</span> : n.wait_min !== undefined ? <span>预计排队 {n.wait_min} 分钟</span> : null}
                  {n.poi?.tags.includes('supply_unknown') && <span className="text-amber-700">营业状态待核验</span>}
                  {n.poi && <SourceBadge source={n.poi.source} />}
              {n.poi?.lng && n.poi?.lat && (
                <button
                  onClick={() => openRoute({ origin: plan.origin ? `${plan.origin.longitude},${plan.origin.latitude}` : coords || undefined, originName: plan.origin?.name, originGranularity: plan.origin ? 'unknown' : undefined, initialMode: plan.travel_mode, dest: `${n.poi!.lng},${n.poi!.lat}`, destName: n.title, city })}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-neutral-100 hover:bg-brand/20 text-neutral-600"
                  title="页内查看路线（驾车/公交地铁/步行），打车再跳转"
                >
                  <Navigation size={10} /> 路线
                </button>
              )}
                </div>
                {n.poi?.recommended && n.poi.recommended.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {n.poi.recommended.slice(0, 4).map((r) => (
                      <span key={r} className="text-[10px] px-1.5 py-0.5 rounded-full bg-brand/10 text-brand-ink">{r}</span>
                    ))}
                  </div>
                )}
                <div className="text-[11px] text-neutral-400 mt-0.5">💡 {n.reason}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 五维雷达图（确定性打分，与真实数据严格对齐） */}
      {plan.radar && Object.keys(plan.radar).length > 0 && (
        <div className="mb-3 flex items-center gap-3 rounded-xl bg-neutral-50 border border-neutral-100 p-2">
          <div className="shrink-0">
            <RadarChart radar={plan.radar} />
          </div>
          {plan.radar_reasons && (
            <div className="flex-1 space-y-1">
              {Object.entries(plan.radar_reasons).map(([k, v]) => (
                <div key={k} className="text-[11px] text-neutral-500 leading-snug">
                  <span className="font-semibold text-neutral-700">{k}</span> · {v}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {plan.share_message && (
        <div className="text-xs bg-neutral-50 rounded-lg p-2 text-neutral-600 border border-neutral-100">
          <span className="text-neutral-400">发同行人：</span>
          {plan.share_message}
        </div>
      )}
      {plan.validation_notes?.length ? <div className="mt-2 text-xs text-amber-700">{plan.validation_notes.join('；')}</div> : null}
      {!embed && <><div className="mt-3 flex gap-2"><button onClick={() => useStore.getState().openShare(plan)} className="text-xs px-3 py-1 rounded-full bg-neutral-100">分享给同行人</button><button onClick={() => void useStore.getState().send('请基于当前行程，读取真实菜单和团购价格并比较适用条件。')} className="text-xs px-3 py-1 rounded-full bg-brand/20">比价与点菜</button></div><VariantEditBox label={plan.title} planId={plan.plan_id} version={plan.version} /></>}
    </>
  )
  return embed ? body : <Card accent>{body}</Card>
}

function PriceComparisonCard({ card }: { card: Extract<OutcomeCard, { kind: 'price_comparison' }> }): JSX.Element {
  const navigate = useStore(s => s.navigateInApp)
  const { data } = card
  return <Card accent>
    <div className="flex items-center gap-2 text-sm font-semibold"><Wallet size={15} className="text-brand-ink" />{card.title}<SourceBadge source={card.source} /></div>
    <div className="mt-1 text-xs text-neutral-500">按人均计价 · {data.party_size === null ? '人数待确认' : `${data.party_size} 人`} · {data.total_budget === null ? '总预算待确认' : `总预算 ¥${data.total_budget}`}</div>
    {data.summary && <p className="mt-2 text-sm text-neutral-700 whitespace-pre-wrap">{data.summary}</p>}
    <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs">
      <thead className="bg-neutral-50 text-neutral-500"><tr><th scope="col" className="p-2">选项</th><th scope="col" className="p-2">人均价格</th><th scope="col" className="p-2">总价</th><th scope="col" className="p-2">预算核对</th><th scope="col" className="p-2">来源</th></tr></thead>
      <tbody>{data.entries.map((entry, index) => <tr key={entry.evidence_id || `${entry.name}:${index}`} className="border-b border-neutral-100 align-top">
        <th scope="row" className="p-2 font-medium">{entry.name}</th>
        <td className="p-2 whitespace-nowrap">{entry.unit_price === null ? '价格待核验' : `¥${entry.unit_price} / 人`}</td>
        <td className="p-2 whitespace-nowrap">{entry.total === null ? '总价待核验' : `¥${entry.total}`}</td>
        <td className={`p-2 whitespace-nowrap ${entry.within_budget === true ? 'text-green-700' : 'text-amber-700'}`}>{entry.within_budget === null ? '待核验' : entry.within_budget ? '预算内' : '超预算'}</td>
        <td className="p-2">{/^https?:\/\//i.test(entry.source_url) ? <button onClick={() => navigate(entry.source_url)} aria-label={`查看${entry.name}价格来源`} className="text-brand-ink underline whitespace-nowrap">查看原页</button> : <span className="text-neutral-400">来源未提供</span>}</td>
      </tr>)}</tbody>
    </table></div>
    {data.recommendation && <div className="mt-2 text-sm text-neutral-700">选择建议：{data.recommendation}</div>}
    {data.savings !== null && <div className="mt-1 text-xs text-neutral-600">总价差额：¥{data.savings}</div>}
    {!!data.limitations.length && <div className="mt-2 text-xs text-amber-700 whitespace-pre-wrap">{data.limitations.join('；')}</div>}
    <details className="mt-3 text-xs"><summary className="cursor-pointer text-neutral-600">查看价格原文证据</summary><div className="mt-2 space-y-2">{data.entries.map((entry, index) => <div key={entry.evidence_id || index}><div className="font-medium">{entry.name}</div><blockquote className="mt-1 pl-2 border-l-2 border-neutral-200 text-neutral-500 whitespace-pre-wrap break-words">{entry.quote || '原文证据尚未提供'}</blockquote></div>)}</div></details>
  </Card>
}

function DishesCard({ shopName, dishes, mode = 'recommendation', source }: { shopName: string; dishes: DishReco[]; mode?: 'menu' | 'recommended_dishes' | 'excerpt' | 'recommendation'; source?: SourceTag }): JSX.Element {
  const send = useStore((s) => s.send)
  const recos = dishes.filter((d) => !d.excluded)
  const avoids = dishes.filter((d) => d.excluded)
  const allPricesUnknown = recos.every(dish => dish.price === undefined)
  const excerpt = mode !== 'recommendation'
  const list = (items: DishReco[]) => <ul className="grid sm:grid-cols-2 gap-x-6">{items.map((dish, index) => <li key={index} className="py-3 border-b border-[var(--line)] text-sm leading-6 min-w-0">
    <div className="flex items-baseline justify-between gap-3"><span className="font-medium break-words">{dish.name}{dish.signature && <span className="ml-2 rounded bg-brand-soft px-1.5 py-0.5 text-xs text-brand-ink">招牌</span>}</span><span className={`shrink-0 tabular-nums ${dish.price === undefined ? 'text-[var(--muted)] text-xs' : 'text-brand-ink font-semibold'}`}>{dish.price === undefined ? allPricesUnknown ? '—' : '价格待核验' : `¥${dish.price}${dish.priceUnit ? ` / ${dish.priceUnit}` : ''}`}</span></div>
    {!excerpt && dish.reason && <p className="mt-1 text-[var(--muted)]">{dish.reason}</p>}
  </li>)}</ul>
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><div className="flex items-center gap-2 text-xs text-[var(--muted)]"><Utensils size={15} />{mode === 'recommended_dishes' ? '推荐菜摘录' : mode === 'menu' ? '菜单摘录' : mode === 'excerpt' ? '菜品摘录' : '点菜建议'} · {recos.length} 项</div><h3 className="mt-1 text-lg leading-7 font-semibold break-words">{shopName}</h3></div>{source && <SourceBadge source={source} />}
      </div>
      {allPricesUnknown && <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">价格待核验 · 当前摘录仅确认菜名，暂无法计算用餐费用。</p>}
      {list(excerpt ? recos.slice(0, 8) : recos)}
      {excerpt && recos.length > 8 && <details className="mt-3 text-sm"><summary className="cursor-pointer py-1 text-brand-strong">展开其余 {recos.length - 8} 道菜品</summary>{list(recos.slice(8))}</details>}
      {excerpt && recos.some(dish => dish.reason) && <details className="mt-3 text-sm"><summary className="cursor-pointer py-1 text-[var(--muted)]">查看菜品原文证据</summary><div className="mt-2 max-h-60 overflow-auto space-y-2">{recos.filter(dish => dish.reason).map((dish, index) => <blockquote key={index} className="border-l-2 border-brand/40 pl-3 text-sm leading-6 text-neutral-600 break-words">{dish.reason}</blockquote>)}</div></details>}
      {avoids.length > 0 && (
        <div className="mt-2 pt-2 border-t border-neutral-100">
          <div className="text-[11px] text-red-500 font-medium mb-1">避雷菜（按人群约束建议避开）</div>
          <div className="flex flex-wrap gap-1">
            {avoids.map((d, i) => (
              <span key={i} className="text-[11px] px-2 py-0.5 rounded-full bg-red-50 text-red-500 line-through">{d.name}</span>
            ))}
          </div>
        </div>
      )}
      <button disabled={allPricesUnknown} title={allPricesUnknown ? '先取得单品价格，再比较套餐费用' : undefined} onClick={() => void send(`「${shopName}」单点 vs 团购套餐，哪个更划算？`)} className="mt-4 w-full py-2.5 text-sm rounded-xl bg-neutral-50 enabled:hover:bg-brand-soft border border-[var(--line)] disabled:text-[var(--muted)] disabled:cursor-not-allowed">
        {allPricesUnknown ? '单品价格待核验' : '对比单点与团购套餐'}
      </button>
    </Card>
  )
}

function GroupBuyCard({ shopName, packages, source }: { shopName: string; packages: GroupBuyPackage[]; source: SourceTag }): JSX.Element {
  const send = useStore((s) => s.send)
  return (
    <Card accent>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="min-w-0"><div className="flex items-center gap-2 text-xs text-[var(--muted)]"><Ticket size={15} />到店优惠 · {packages.length} 项</div><h3 className="mt-1 font-semibold text-lg leading-7 break-words">{shopName}</h3></div>
        <SourceBadge source={source} />
      </div>
      <div className="space-y-3">
        {packages.map((p, i) => (
          <div key={i} className={`rounded-xl border p-4 ${p.recommended ? 'border-brand bg-brand/5' : 'border-[var(--line)] bg-[var(--surface-soft)]'}`}>
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-base leading-6">{p.name}</span>
              {p.recommended && <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-brand text-brand-ink">推荐</span>}
              {p.sold && <span className="ml-auto text-[10px] text-neutral-400">{p.sold}</span>}
            </div>
            <div className="mt-3 flex flex-wrap items-baseline gap-2">
              <span className={`font-semibold tabular-nums ${p.price === null ? 'text-amber-800 text-sm' : 'text-brand-ink text-2xl'}`}>{p.price === null ? '价格待核验' : `¥${p.price}`}</span>
              {p.originalPrice !== null && <span className="text-[11px] text-neutral-400 line-through">¥{p.originalPrice}</span>}
              {p.originalPrice !== null && p.price !== null && p.originalPrice > p.price && <span className="text-[10px] text-green-600">省¥{p.originalPrice - p.price}</span>}
              <span className="ml-auto text-sm text-neutral-600">{p.fitPeople}</span>
            </div>
            <div className="mt-3 text-sm leading-6 text-neutral-600"><h4 className="font-medium text-brand-ink">使用条件</h4>{p.includes?.length ? <ul className="list-disc pl-5 mt-1 space-y-1">{p.includes.map((condition, index) => <li key={index}>{condition}</li>)}</ul> : <p className="mt-1 text-amber-800">使用日期、预约要求及其他限制待核验。</p>}</div>
            {p.quote && <details className="mt-3 text-sm"><summary className="cursor-pointer py-1 text-[var(--muted)]">查看套餐原文证据</summary><blockquote className="mt-2 border-l-2 border-brand/40 pl-3 leading-6 text-neutral-600 whitespace-pre-wrap break-words">{p.quote}</blockquote></details>}
            <button
              onClick={() => void send(`请核对「${shopName}」的「${p.name}」套餐价格、适用人数与使用条件；只读，不下单。`)}
              className="mt-4 w-full py-2.5 rounded-xl bg-brand text-brand-ink text-sm font-medium hover:brightness-95"
            >
              核对这个套餐
            </button>
          </div>
        ))}
      </div>
      <div className="mt-4 text-xs leading-6 text-[var(--muted)]">价格来自读取时的页面，尚未确认实时可用。当前仅核对资料；下单前需要单独确认。</div>
    </Card>
  )
}

function ReceiptCard({ items, shareMessage }: { items: ReceiptItem[]; shareMessage: string }): JSX.Element {
  const send = useStore((s) => s.send)
  return (
    <Card>
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-sm">
        <ListChecks size={14} className="text-brand-ink" /> 执行记录
        {items.some(item => item.business_confirmed) && <span className="ml-auto text-[10px] text-neutral-400 font-normal">已确认的业务结果可继续提出修改或取消请求</span>}
      </div>
      <div className="space-y-1.5">
        {items.map((it, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            {it.status === 'ok' ? (
              <CheckCircle2 size={13} className="text-green-500 mt-0.5 shrink-0" />
            ) : it.status === 'fail' ? (
              <AlertTriangle size={13} className="text-amber-500 mt-0.5 shrink-0" />
            ) : (
              <Clock size={13} className="text-neutral-400 mt-0.5 shrink-0" />
            )}
            <div className="flex-1">
              <span className="font-medium">{it.label}</span>
              <span className="text-neutral-500 ml-1">{it.detail}</span>
              {it.source === 'user' ? <span className="ml-1 text-[10px] rounded-full px-1.5 py-0.5 bg-blue-100 text-blue-700">用户确认</span> : <SourceBadge source={it.source} />}
              {it.resolution_required && <ManualResolution key={`${it.run_id}:${it.action_id}`} item={it} />}
              {it.status === 'ok' && it.business_confirmed && (
                <span className="ml-2 inline-flex gap-1">
                  <button
                    onClick={() => void send(`临时有变，帮我改一下「${it.label}」（换时间/换人数/换一家）`)}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-100 hover:bg-brand/20 border border-neutral-200 text-neutral-600"
                  >
                    改一下
                  </button>
                  <button
                    onClick={() => void send(`帮我取消「${it.label}」`)}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-100 hover:bg-red-50 border border-neutral-200 text-red-500"
                  >
                    取消
                  </button>
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      {shareMessage && (
        <div className="mt-2 text-xs bg-brand-soft border border-brand/30 rounded-lg p-2 flex items-start gap-1.5">
          <Send size={12} className="text-brand-ink mt-0.5 shrink-0" />
          <span className="text-neutral-700">{shareMessage}</span>
        </div>
      )}
    </Card>
  )
}

function ManualResolution({ item }: { item: ReceiptItem }): JSX.Element {
  const run = useStore(s => s.run)
  const busy = useStore(s => s.busy)
  const ready = useStore(s => s.backendReady)
  const resolve = useStore(s => s.resolveAction)
  const [checked, setChecked] = useState(false)
  const [status, setStatus] = useState<'SUCCEEDED' | 'FAILED'>('FAILED')
  const [note, setNote] = useState('')
  const [reference, setReference] = useState('')
  const current = !!item.run_id && !!item.action_id && canResolveAction(run, item.run_id, item.action_id)
  const disabled = !current || busy || !ready
  return <form className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 space-y-3" onSubmit={event => {
    event.preventDefault()
    if (!disabled && checked && note.trim()) void resolve(item.run_id!, item.action_id!, status, note, reference)
  }}>
    <label className="flex items-start gap-1.5"><input type="checkbox" required checked={checked} onChange={event => setChecked(event.target.checked)} disabled={disabled} /><span>我已在网站核对本次任务结果</span></label>
    <div className="text-[11px] text-neutral-500">这里只记录你的核对结论；记录会标为「用户确认」。页面按钮已点击不代表业务已完成。</div>
    <label className="block">核对结果<select value={status} onChange={event => setStatus(event.target.value as 'SUCCEEDED' | 'FAILED')} disabled={disabled} className="plango-field mt-2 bg-white"><option value="FAILED">任务未完成</option><option value="SUCCEEDED">任务已完成</option></select></label>
    <label className="block">核对说明（必填）<textarea value={note} onChange={event => setNote(event.target.value)} required maxLength={500} disabled={disabled} rows={2} placeholder="说明核对了哪个订单、预约或取号记录，以及实际结果" className="plango-field mt-2 bg-white" /></label>
    <label className="block">业务编号（可选）<input value={reference} onChange={event => setReference(event.target.value)} maxLength={200} disabled={disabled} className="plango-field mt-2 bg-white" /></label>
    <button disabled={disabled || !checked || !note.trim()} className="rounded bg-brand text-brand-ink px-3 py-1 disabled:opacity-40">记录核对结果</button>
  </form>
}

function ConfirmCard({ token, title, detail, danger }: { token: string; title: string; detail: string; danger: boolean }): JSX.Element {
  const confirm = useStore((s) => s.confirm)
  const busy = useStore((s) => s.busy)
  return (
    <Card accent>
      <div className="flex items-center gap-1.5 mb-1 font-semibold text-sm">
        {danger ? <AlertTriangle size={15} className="text-amber-500" /> : <CheckCircle2 size={15} className="text-brand-ink" />}
        确认下一步
      </div>
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs text-neutral-500 mt-0.5 mb-2.5 whitespace-pre-wrap break-words">{detail}</div>
      <div className="text-[11px] text-neutral-400 mb-2">确认仅授权这里列出的操作。页面或关键参数变化时会重新确认；未取得业务回执会标为待核验。</div>
      <div className="flex gap-2">
        <button disabled={busy} onClick={() => void confirm(token, true)} className="flex-1 py-2.5 text-sm rounded-xl bg-brand text-brand-ink font-medium disabled:opacity-50">
          确认执行
        </button>
        <button disabled={busy} onClick={() => void confirm(token, false)} className="px-5 py-2.5 text-sm rounded-xl border border-[var(--line)] bg-white text-neutral-600 disabled:opacity-50">
          取消
        </button>
      </div>
    </Card>
  )
}

function distanceLabel(km: number, lowerBound = false): string {
  if (km < 1) return `${lowerBound ? Math.floor(km * 1000) : Math.round(km * 1000)} 米`
  return `${(lowerBound ? Math.floor(km * 10) / 10 : km).toFixed(1)} 公里`
}

function styleLabel(s: string): string {
  return { economic: '经济', balanced: '均衡', premium: '品质', special: '特别版' }[s] || s
}

function EvidenceCard({ items }: { items: HarnessEvidence[] }): JSX.Element {
  const navigate = useStore((s) => s.navigateInApp)
  return <Card><details><summary className="cursor-pointer text-sm font-semibold">来源与页面证据 · {items.length} 条</summary>
    <div className="mt-2 space-y-2">{items.map(item => <div key={item.evidence_id} className="text-xs border-t border-neutral-100 pt-2">
      <div className="flex items-center gap-2"><SourceBadge source={item.source} /><span className="text-neutral-400">{item.observed_at ? new Date(item.observed_at).toLocaleString('zh-CN') : '观测时间未知'}</span>{item.expires_at && new Date(item.expires_at).getTime() < Date.now() && <span className="text-amber-700">已过期，需重新核验</span>}</div>
      <div className="mt-1 whitespace-pre-wrap break-words text-neutral-600">{item.claim || item.evidence_id}</div>
      {/^https?:\/\//i.test(item.source_ref) && <button onClick={() => navigate(item.source_ref)} className="mt-1 text-brand-ink underline break-all">打开原始页面</button>}
    </div>)}</div>
  </details></Card>
}
