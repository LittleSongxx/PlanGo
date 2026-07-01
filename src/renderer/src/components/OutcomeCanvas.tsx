import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import type { OutcomeCard, Plan, DealRow, DishReco, ReceiptItem, SourceTag, TakeoutItem, DiscoverGroup, POISummary, GroupBuyPackage } from '@shared/types'
import { SourceBadge } from './SourceBadge'
import { PlanMap } from './PlanMap'
import { RouteSheet } from './RouteSheet'
import { ShareModal } from './ShareModal'
import { MapPin, Clock, Utensils, Ticket, Users, CheckCircle2, XCircle, AlertTriangle, Send, ListChecks, Tag, Wallet, Globe, Navigation, Mic, MicOff, Compass, Trash2 } from 'lucide-react'

// 无真实图时的分类占位图（对齐 yoyu _CAT_IMG，诚实标"示意图"）。按标题/标签选主题，避免千篇一律。
const THEME_IMG: { re: RegExp; url: string }[] = [
  { re: /火锅|烧烤|串/, url: 'https://images.unsplash.com/photo-1552611052-33e04de081de?w=400&q=70' },
  { re: /咖啡|coffee|café/i, url: 'https://images.unsplash.com/photo-1447933601403-0c6688de566e?w=400&q=70' },
  { re: /甜品|烘焙|蛋糕|面包/, url: 'https://images.unsplash.com/photo-1509440159596-0249088772ff?w=400&q=70' },
  { re: /沙拉|轻食|减脂|健康/, url: 'https://images.unsplash.com/photo-1512621776951-a57141f2eefd?w=400&q=70' },
  { re: /日料|寿司|居酒屋/, url: 'https://images.unsplash.com/photo-1579584425555-c3ce17fd4351?w=400&q=70' },
  { re: /茶|奶茶|饮/, url: 'https://images.unsplash.com/photo-1558857563-b371033873b8?w=400&q=70' },
  { re: /公园|广场|绿道|湖/, url: 'https://images.unsplash.com/photo-1502602898657-3e91760cbb34?w=400&q=70' },
  { re: /博物馆|展|艺术|美术/, url: 'https://images.unsplash.com/photo-1499426600726-a950358acf16?w=400&q=70' },
  { re: /乐园|游乐|亲子|儿童|反斗/, url: 'https://images.unsplash.com/photo-1560807707-8cc77767d783?w=400&q=70' },
  { re: /电影|影院|剧|KTV|唱/, url: 'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=400&q=70' },
  { re: /海|沙滩|山|徒步|户外/, url: 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=400&q=70' }
]
const CAT_IMG: Record<string, string> = {
  dining: 'https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=400&q=70',
  activity: 'https://images.unsplash.com/photo-1533107862482-0e6974b06ec4?w=400&q=70'
}
function poiImage(title: string, tags: string[] | undefined, category: string): { url: string; real: boolean } {
  const blob = `${title}${(tags || []).join('')}`
  const t = THEME_IMG.find((x) => x.re.test(blob))
  if (t) return { url: t.url, real: false }
  return { url: CAT_IMG[category === 'dining' ? 'dining' : 'activity'], real: false }
}

export function OutcomeCanvas(): JSX.Element {
  const cards = useStore((s) => s.cards)
  const setView = useStore((s) => s.setView)
  const clearCards = useStore((s) => s.clearCards)
  const routeTarget = useStore((s) => s.routeTarget)
  const closeRoute = useStore((s) => s.closeRoute)
  return (
    <div className="h-full flex flex-col bg-neutral-50">
      {routeTarget && <RouteSheet target={routeTarget} onClose={closeRoute} />}
      <ShareModal />
      <div className="h-10 shrink-0 flex items-center gap-2 px-4 border-b border-neutral-200 bg-white">
        <ListChecks size={16} className="text-brand-ink" />
        <span className="text-sm font-semibold">成果区</span>
        <span className="text-xs text-neutral-400">行程 · 比价 · 点菜 · 排号 · 确认 · 执行回执</span>
        <div className="ml-auto flex items-center gap-1">
          {cards.length > 0 && (
            <button onClick={clearCards} className="text-xs flex items-center gap-1 px-2 py-1 rounded-lg text-neutral-500 hover:bg-neutral-100" title="清空成果卡片">
              <Trash2 size={13} /> 清空
            </button>
          )}
          <button onClick={() => setView('browser')} className="text-xs flex items-center gap-1 px-2 py-1 rounded-lg text-neutral-500 hover:bg-neutral-100">
            <Globe size={13} /> 回到浏览器
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {cards.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center text-neutral-400 gap-3">
            <div className="w-16 h-16 rounded-2xl bg-brand/10 flex items-center justify-center">
              <ListChecks size={30} className="text-brand-ink" />
            </div>
            <div className="text-sm max-w-xs">
              让小悠规划一下，行程、比价、点菜、排号、执行回执都会以精美卡片出现在这块大画布里。
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto space-y-4">
            {cards.map((c, i) => ({ c, i })).reverse().map(({ c, i }) => (
              <CardView key={`${c.kind}-${i}`} card={c} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function CardView({ card }: { card: OutcomeCard }): JSX.Element {
  switch (card.kind) {
    case 'plan':
      return <PlanCard plan={card.plan} />
    case 'plans':
      return <PlansCard variants={card.variants} city={card.city} budget={card.budget} />
    case 'deal':
      return <DealCard title={card.title} rows={card.rows} />
    case 'dishes':
      return <DishesCard shopName={card.shopName} dishes={card.dishes} />
    case 'takeout':
      return <TakeoutCard {...card} />
    case 'discover':
      return <DiscoverCard city={card.city} groups={card.groups} source={card.source} />
    case 'groupbuy':
      return <GroupBuyCard shopName={card.shopName} packages={card.packages} source={card.source} />
    case 'queue':
      return <QueueCard {...card} />
    case 'consensus':
      return <ConsensusCard question={card.question} options={card.options} />
    case 'receipt':
      return <ReceiptCard items={card.items} shareMessage={card.shareMessage} />
    case 'confirm':
      return <ConfirmCard token={card.token} title={card.title} detail={card.detail} danger={card.danger} />
    default:
      return <></>
  }
}

function Card({ children, accent }: { children: React.ReactNode; accent?: boolean }): JSX.Element {
  return <div className={`bg-white rounded-xl border ${accent ? 'border-brand shadow-md' : 'border-neutral-200 shadow-sm'} p-3.5 animate-in`}>{children}</div>
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
      <polygon points={dataPts} fill="rgba(255,184,0,.22)" stroke="#f5a623" strokeWidth={2} strokeLinejoin="round" />
      {vals.map((v, i) => {
        const [x, y] = pt(i, v)
        return <circle key={i} cx={x} cy={y} r={3} fill="#f5a623" />
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

// 三方案 Tab 卡：经济 / 均衡 / 特色（对齐 weplan/yoyu）
function PlansCard({
  variants,
  city,
  budget
}: {
  variants: { plan: Plan; styleLabel: string; per: number; overBudget?: number }[]
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
        <span className="font-semibold text-[15px]">{city}·周末规划 · 3 套方案</span>
      </div>

      {/* Tab 切换 */}
      <div className="grid grid-cols-3 gap-1.5 mb-3">
        {variants.map((v, i) => (
          <button
            key={i}
            onClick={() => setTab(i)}
            className={`rounded-xl border px-2 py-2 text-left transition-colors ${
              i === tab ? 'border-brand bg-brand/10' : 'border-neutral-200 hover:border-neutral-300'
            }`}
          >
            <div className="text-xs font-semibold flex items-center gap-1">
              {v.styleLabel}
              {v.overBudget ? <span className="text-[9px] text-amber-600">超¥{v.overBudget}</span> : budget ? <span className="text-[9px] text-green-600">达标</span> : null}
            </div>
            <div className="text-[11px] text-neutral-500 mt-0.5">人均¥{v.per}</div>
          </button>
        ))}
      </div>

      {/* 预算诚实横幅 */}
      {budget ? (
        <div className={`mb-3 flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs ${active.overBudget ? 'bg-amber-50 text-amber-700' : 'bg-green-50 text-green-700'}`}>
          <Wallet size={13} />
          {active.overBudget ? `本套人均¥${active.per}，超预算¥${budget}（+¥${active.overBudget}）。可切到「经济实惠」，或放宽预算。` : `本套人均¥${active.per}，在预算¥${budget}以内。`}
        </div>
      ) : null}

      <PlanCard plan={active.plan} embed />

      <div className="mt-3 flex flex-wrap gap-1.5">
        <button onClick={() => void send(`就选「${active.styleLabel}」这套，帮我看看单点vs团购比价`)} className="text-[11px] px-2.5 py-1 rounded-full bg-brand text-brand-ink font-medium">
          选这套 · 比价
        </button>
        <button onClick={() => openShare(active.plan)} className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-100 hover:bg-neutral-200 border border-neutral-200">
          发同行人确认
        </button>
        <button onClick={() => void send(`「${active.styleLabel}」这套里的正餐换一家更近的`)} className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-100 hover:bg-neutral-200 border border-neutral-200">
          换正餐
        </button>
      </div>

      {/* 针对当前方案的语音/文字修改框（仿 weplan plan-dock，绑定当前 Tab） */}
      <VariantEditBox label={active.styleLabel} />
    </Card>
  )
}

// 绑定当前方案的修改框：语音（Web Speech API）或输入文字，自动带上「只改这套」上下文。
function VariantEditBox({ label }: { label: string }): JSX.Element {
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
    void send(`【只改「${label}」这套方案，其它两套不动】${msg}`)
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
          className="flex-1 text-xs px-3 py-2 rounded-full border border-neutral-200 focus:border-brand outline-none bg-neutral-50"
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
  const per = plan.nodes.length ? Math.round(plan.total_cost / Math.max(2, 1)) : 0
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
        <span>合计约 ¥{plan.total_cost} · {plan.nodes.length} 站</span>
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

      {/* 地图（真实路网） */}
      <PlanMap plan={plan} />

      {/* 时间线 */}
      <div className="relative pl-4 space-y-3 mb-3">
        <div className="absolute left-[6px] top-1 bottom-1 w-px bg-neutral-200" />
        {plan.nodes.map((n) => (
          <div key={n.node_id} className="relative">
            <div className="absolute -left-[13px] top-1 w-3 h-3 rounded-full bg-brand border-2 border-white" />
            {n.route_from_prev && n.transit_from_prev_min > 0 && (
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
              {(() => {
                const real = !!n.poi?.image
                const src = n.poi?.image || poiImage(n.title, n.poi?.tags, n.category).url
                return (
                  <div className="relative w-16 h-16 shrink-0">
                    <img
                      src={src}
                      alt={n.title}
                      loading="lazy"
                      className="w-16 h-16 rounded-lg object-cover border border-neutral-200 bg-neutral-100"
                      onError={(e) => (e.currentTarget.src = CAT_IMG[n.category === 'dining' ? 'dining' : 'activity'])}
                    />
                    {!real && <span className="absolute bottom-0 left-0 right-0 text-[8px] text-center text-white bg-black/40 rounded-b-lg">示意图</span>}
                  </div>
                )
              })()}
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm">{n.title}</div>
                <div className="text-[11px] text-neutral-500 mt-0.5 flex items-center gap-1 flex-wrap">
                  {n.poi?.filtered_score != null && <span className="text-amber-600">★{n.poi.filtered_score}</span>}
                  {(n.poi?.raw_score && n.poi?.filtered_score == null) ? <span className="text-amber-600">★{n.poi.raw_score}</span> : null}
                  {n.poi?.price_per_person ? <span>人均¥{n.poi.price_per_person}</span> : null}
                  {n.poi && <SourceBadge source={n.poi.source} />}
              {n.poi?.lng && n.poi?.lat && (
                <button
                  onClick={() => openRoute({ origin: coords || undefined, dest: `${n.poi!.lng},${n.poi!.lat}`, destName: n.title, city })}
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
      {void per}
    </>
  )
  return embed ? body : <Card accent>{body}</Card>
}

function DealCard({ title, rows }: { title: string; rows: DealRow[] }): JSX.Element {
  const total = rows.reduce((s, r) => s + r.saved, 0)
  return (
    <Card>
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-sm">
        <Tag size={14} className="text-brand-ink" /> {title}
      </div>
      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="text-xs border border-neutral-100 rounded-lg p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium">{r.shop}</span>
              <span>
                <span className="text-neutral-400 line-through mr-1">¥{r.original}</span>
                <span className="text-red-500 font-semibold">¥{r.final}</span>
              </span>
            </div>
            <div className="text-neutral-500 mt-1">
              {r.reason}
              {r.used.length ? ` · 用券：${r.used.join('+')}` : ''}
            </div>
            {r.credentials?.length ? <div className="text-[11px] text-amber-600 mt-0.5">需出示：{r.credentials.join('、')}</div> : null}
            {r.filtered_fake?.length ? <div className="text-[11px] text-neutral-400 mt-0.5">已识别假低价：{r.filtered_fake.join('、')}</div> : null}
          </div>
        ))}
      </div>
      <div className="mt-2 text-xs text-right text-red-500 font-semibold">合计可省约 ¥{total}</div>
    </Card>
  )
}

function DishesCard({ shopName, dishes }: { shopName: string; dishes: DishReco[] }): JSX.Element {
  const send = useStore((s) => s.send)
  const recos = dishes.filter((d) => !d.excluded)
  const avoids = dishes.filter((d) => d.excluded)
  return (
    <Card>
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-sm">
        <Utensils size={14} className="text-brand-ink" /> AI 单点 · {shopName}
      </div>
      <div className="space-y-1.5">
        {recos.map((d, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <CheckCircle2 size={13} className="text-green-500 mt-0.5 shrink-0" />
            <div className="flex-1">
              <span className="font-medium">{d.name}</span>
              {d.signature ? <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-brand/15 text-brand-ink align-middle">招牌</span> : null}
              {d.price ? <span className="text-neutral-400 ml-1">¥{d.price}</span> : null}
              <span className="text-neutral-500 ml-1">— {d.reason}</span>
            </div>
          </div>
        ))}
      </div>
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
      <button onClick={() => void send(`「${shopName}」单点 vs 团购套餐，哪个更划算？`)} className="mt-2 w-full py-1.5 text-xs rounded-lg bg-neutral-100 hover:bg-brand/20 border border-neutral-200">
        对比单点 vs 团购套餐
      </button>
    </Card>
  )
}

function GroupBuyCard({ shopName, packages, source }: { shopName: string; packages: GroupBuyPackage[]; source: SourceTag }): JSX.Element {
  const send = useStore((s) => s.send)
  return (
    <Card accent>
      <div className="flex items-center gap-1.5 mb-2">
        <Ticket size={15} className="text-brand-ink" />
        <span className="font-semibold text-[15px]">{shopName} · 团购套餐</span>
        <SourceBadge source={source} />
      </div>
      <div className="space-y-2">
        {packages.map((p, i) => (
          <div key={i} className={`rounded-xl border p-2.5 ${p.recommended ? 'border-brand bg-brand/5' : 'border-neutral-200'}`}>
            <div className="flex items-center gap-1.5">
              <span className="font-medium text-sm">{p.name}</span>
              {p.recommended && <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-brand text-brand-ink">推荐</span>}
              {p.sold && <span className="ml-auto text-[10px] text-neutral-400">{p.sold}</span>}
            </div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span className="text-brand-ink font-bold text-lg">¥{p.price}</span>
              <span className="text-[11px] text-neutral-400 line-through">¥{p.originalPrice}</span>
              <span className="text-[10px] text-green-600">省¥{p.originalPrice - p.price}</span>
              <span className="ml-auto text-[11px] text-neutral-500">{p.fitPeople}</span>
            </div>
            {p.includes?.length ? <div className="mt-1 text-[11px] text-neutral-500">含：{p.includes.join('、')}</div> : null}
            <button
              onClick={() => void send(`买「${shopName}」的「${p.name}」团购套餐，帮我下单`)}
              className="mt-2 w-full py-1.5 rounded-lg bg-brand text-brand-ink text-xs font-medium hover:brightness-95"
            >
              买这个套餐
            </button>
          </div>
        ))}
      </div>
      <div className="mt-2 text-[10px] text-neutral-400">团购价含 AI 估算，最终以门店实际为准；下单走两步确认。</div>
    </Card>
  )
}

function DiscoverCard({ city, groups, source }: { city: string; groups: DiscoverGroup[]; source: SourceTag }): JSX.Element {
  const send = useStore((s) => s.send)
  const openRoute = useStore((s) => s.openRoute)
  const coords = useStore((s) => s.coords)
  return (
    <Card accent>
      <div className="flex items-center gap-1.5 mb-2">
        <Compass size={15} className="text-brand-ink" />
        <span className="font-semibold text-[15px]">{city} · 今日附近热点</span>
        <SourceBadge source={source} />
        <span className="ml-auto text-[11px] text-neutral-400">以你当前定位为圆心</span>
      </div>
      <div className="space-y-3">
        {groups.map((g) => (
          <div key={g.label}>
            <div className="text-xs font-semibold text-neutral-600 mb-1.5">
              {g.emoji} {g.label}
            </div>
            <div className="grid grid-cols-2 gap-2">
              {g.items.map((p) => {
                const img = p.image || poiImage(p.name, p.tags, /咖啡|甜品|美食|餐/.test(g.label) ? 'dining' : 'activity').url
                return (
                  <div key={p.poi_id} className="rounded-xl border border-neutral-200 overflow-hidden bg-white">
                    <div className="relative h-20 bg-neutral-100">
                      <img src={img} alt={p.name} loading="lazy" className="w-full h-full object-cover" onError={(e) => (e.currentTarget.src = CAT_IMG.dining)} />
                      {!p.image && <span className="absolute bottom-0 right-0 text-[8px] text-white bg-black/40 px-1 rounded-tl">示意图</span>}
                    </div>
                    <div className="p-2">
                      <div className="text-xs font-medium truncate">{p.name}</div>
                      <div className="text-[11px] text-neutral-500 mt-0.5 flex items-center gap-1.5 flex-wrap">
                        {(p.filtered_score ?? p.raw_score) ? <span className="text-amber-600">★{p.filtered_score ?? p.raw_score}</span> : null}
                        {p.price_per_person ? <span>¥{p.price_per_person}</span> : null}
                        {p.distance_m ? <span>{(p.distance_m / 1000).toFixed(1)}km</span> : null}
                      </div>
                      <div className="mt-1.5 flex gap-1">
                        <button
                          onClick={() => void send(`就以「${p.name}」为中心，帮我排一套附近的周末方案`)}
                          className="flex-1 text-[10px] py-1 rounded-lg bg-brand text-brand-ink font-medium"
                        >
                          去规划
                        </button>
                        {p.lng && p.lat && (
                          <button
                            onClick={() => openRoute({ origin: coords || undefined, dest: `${p.lng},${p.lat}`, destName: p.name, city })}
                            className="px-2 text-[10px] py-1 rounded-lg bg-neutral-100 hover:bg-neutral-200 border border-neutral-200"
                          >
                            路线
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function TakeoutCard({
  shopName,
  deliverTo,
  etaMin,
  deliveryFee,
  packFee,
  items,
  total,
  source
}: {
  shopName: string
  deliverTo: string
  etaMin: number
  deliveryFee: number
  packFee: number
  items: TakeoutItem[]
  total: number
  source: SourceTag
}): JSX.Element {
  const send = useStore((s) => s.send)
  return (
    <Card>
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-sm">
        <Utensils size={14} className="text-brand-ink" /> 外卖点单 · {shopName} <SourceBadge source={source} />
      </div>
      <div className="flex items-center gap-3 text-xs text-neutral-600 mb-1 flex-wrap">
        <span className="flex items-center gap-1"><MapPin size={12} /> 送到：{deliverTo}</span>
        <span className="flex items-center gap-1"><Clock size={12} /> 预计 {etaMin} 分钟送达</span>
      </div>
      <div className="flex items-center gap-1 mb-2 text-[10px] text-neutral-400">
        改送到：
        {['我的位置（当前定位）', '家', '公司'].map((addr) => (
          <button
            key={addr}
            onClick={() => void send(`外卖改送到「${addr}」，重新算下配送费和预计送达`)}
            className={`px-1.5 py-0.5 rounded ${deliverTo === addr ? 'bg-brand/20 text-brand-ink' : 'bg-neutral-100 hover:bg-neutral-200'}`}
          >
            {addr === '我的位置（当前定位）' ? '我的位置' : addr}
          </button>
        ))}
      </div>
      <div className="space-y-1.5 mb-2">
        {items.map((it, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <CheckCircle2 size={13} className="text-green-500 mt-0.5 shrink-0" />
            <div className="flex-1">
              <span className="font-medium">{it.name}</span>
              <span className="text-neutral-400 ml-1">×{it.qty}</span>
              <span className="text-neutral-500 ml-1">— {it.reason}</span>
            </div>
            <span className="text-neutral-600">¥{it.price * it.qty}</span>
          </div>
        ))}
      </div>
      <div className="text-[11px] text-neutral-500 border-t border-neutral-100 pt-1.5 flex justify-between">
        <span>配送 ¥{deliveryFee}{deliveryFee === 0 ? '（满减免）' : ''} · 打包 ¥{packFee}</span>
        <span className="text-red-500 font-semibold text-sm">合计 ¥{total}</span>
      </div>
      <button onClick={() => void send(`就按这份外卖清单下单，送到「${deliverTo}」`)} className="mt-2 w-full py-1.5 text-xs rounded-lg bg-brand text-brand-ink font-medium">
        确认下单（两步确认）
      </button>
    </Card>
  )
}

function QueueCard({ shopName, number, ahead, etaMin, source }: { shopName: string; number: string; ahead: number; etaMin: number; source: SourceTag }): JSX.Element {
  return (
    <Card>
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-sm">
        <ListChecks size={14} className="text-brand-ink" /> 排队取号 · {shopName} <SourceBadge source={source} />
      </div>
      <div className="flex items-center gap-4">
        <div className="text-center">
          <div className="text-3xl font-bold text-brand-ink">{number}</div>
          <div className="text-[10px] text-neutral-400">您的号码</div>
        </div>
        <div className="text-xs text-neutral-600 space-y-1">
          <div>前面还有 <b className="text-red-500">{ahead}</b> 桌</div>
          <div>预计等待 <b>{etaMin}</b> 分钟</div>
          <div className="text-neutral-400">建议先去逛，到点小悠提醒你</div>
        </div>
      </div>
    </Card>
  )
}

function ConsensusCard({ question, options }: { question: string; options: string[] }): JSX.Element {
  const send = useStore((s) => s.send)
  const opMap: Record<string, string> = { 想改正餐: '把正餐换一家', 想改玩乐: '把玩乐换一个', 换个时间: '整体推后30分钟' }
  return (
    <Card>
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-sm">
        <Users size={14} className="text-brand-ink" /> 群体确认
      </div>
      <div className="text-xs text-neutral-600 mb-2">{question}</div>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => (
          <button
            key={o}
            onClick={() => void send(o.includes('同意') ? '大家都同意，就这么定，帮我一键执行' : opMap[o] || o)}
            className="text-[11px] px-2.5 py-1 rounded-full bg-neutral-100 hover:bg-brand/20 border border-neutral-200"
          >
            {o}
          </button>
        ))}
      </div>
    </Card>
  )
}

function ReceiptCard({ items, shareMessage }: { items: ReceiptItem[]; shareMessage: string }): JSX.Element {
  const send = useStore((s) => s.send)
  return (
    <Card>
      <div className="flex items-center gap-1.5 mb-2 font-semibold text-sm">
        <CheckCircle2 size={14} className="text-green-500" /> 执行回执
        <span className="ml-auto text-[10px] text-neutral-400 font-normal">计划有变？每项都能改/取消</span>
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
              <SourceBadge source={it.source} />
              {it.status === 'ok' && (
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

function ConfirmCard({ token, title, detail, danger }: { token: string; title: string; detail: string; danger: boolean }): JSX.Element {
  const confirm = useStore((s) => s.confirm)
  const busy = useStore((s) => s.busy)
  return (
    <Card accent>
      <div className="flex items-center gap-1.5 mb-1 font-semibold text-sm">
        {danger ? <AlertTriangle size={15} className="text-amber-500" /> : <CheckCircle2 size={15} className="text-brand-ink" />}
        两步确认
      </div>
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs text-neutral-500 mt-0.5 mb-2.5">{detail}</div>
      <div className="text-[11px] text-neutral-400 mb-2">这是写操作，确认后小悠才会真正执行（幻觉下单率 0）。</div>
      <div className="flex gap-2">
        <button disabled={busy} onClick={() => void confirm(token, true)} className="flex-1 py-1.5 text-sm rounded-lg bg-brand text-brand-ink font-medium disabled:opacity-50">
          确认执行
        </button>
        <button disabled={busy} onClick={() => void confirm(token, false)} className="px-4 py-1.5 text-sm rounded-lg bg-neutral-100 text-neutral-600 disabled:opacity-50">
          取消
        </button>
      </div>
    </Card>
  )
}

function styleLabel(s: string): string {
  return { economic: '经济', balanced: '均衡', premium: '品质', special: '特别版' }[s] || s
}
