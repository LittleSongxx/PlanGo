import { userMessage } from '@shared/userMessages'
import { useEffect, useRef, useState } from 'react'
import type { MerchantCandidates, OfferComparison } from '@shared/types'
import { sameOfferSource, rows } from '../lib/harnessProjection'
import { useStore } from '../store'
import { SourceBadge } from './SourceBadge'
import { AlertTriangle, CheckCircle2, MapPin, Ticket, XCircle } from 'lucide-react'

const kindLabels = { voucher: '代金券', package: '套餐', single_item: '单品优惠', unknown: '计价口径待核对' }
const priceUnits = { per_person: ' / 人', per_package: ' / 套餐', voucher: ' / 张券', single_item: ' / 单项', unknown: '' }
const statusLabels = { eligible: '当前条件适用', ineligible: '当前条件不适用', unknown: '规则缺失 · 待核对' }
const timestamp = (value: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN') : '时间未提供'

export function OfferComparisonCard({ comparison, runId, version }: { comparison: OfferComparison; runId: string; version: number }): JSX.Element {
  const busy = useStore(s => s.busy)
  const ready = useStore(s => s.backendReady)
  const run = useStore(s => s.run)
  const pendingDelivery = useStore(s => s.pendingDelivery)
  const selectOffer = useStore(s => s.selectOffer)
  const navigate = useStore(s => s.navigateInApp)
  const [choosing, setChoosing] = useState('')
  const [candidates, setCandidates] = useState<MerchantCandidates | null>(null)
  const [selectedPoi, setSelectedPoi] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const sequence = useRef(0)
  const [expired, setExpired] = useState(false)
  const current = run?.run_id === runId && run.version === version && sameOfferSource(run.offer_comparison?.source_ref, comparison.source_ref)
  const unresolved = !!run?.state.browser_receipt_pending || rows(run?.state.action_results).some(action => ['RUNNING', 'UNKNOWN'].includes(String(action.status)))
  const disabled = unresolved || !current || busy || !ready || !!pendingDelivery || !!run?.command_pending || !!run?.cancel_requested || expired || !comparison.source.valid
  useEffect(() => {
    sequence.current++; setChoosing(''); setCandidates(null); setSelectedPoi(''); setLoading(false); setError('')
    return () => { sequence.current++ }
  }, [runId, version, comparison.source_ref.command_id, comparison.source_ref.artifact_id])
  useEffect(() => {
    const remaining = Date.parse(comparison.source.expires_at || '') - Date.now()
    setExpired(Number.isFinite(remaining) && remaining <= 0)
    if (!Number.isFinite(remaining) || remaining <= 0) return
    const timer = setTimeout(() => setExpired(true), Math.min(remaining, 2_147_483_647))
    return () => clearTimeout(timer)
  }, [comparison.source.expires_at])

  const findMerchant = async (hash: string): Promise<void> => {
    if (disabled) return
    const request = ++sequence.current
    setChoosing(hash); setCandidates(null); setSelectedPoi(''); setError(''); setLoading(true)
    try {
      const result = await window.plango.harness.merchantCandidates(runId, comparison.source_ref)
      if (request !== sequence.current) return
      if (!sameOfferSource(result.source_ref, comparison.source_ref) || result.merchant.name !== comparison.merchant.name || result.merchant.address !== comparison.merchant.address) throw new Error('候选响应与原网页门店不一致，请刷新后核对。')
      setCandidates(result)
    } catch (reason) { if (request === sequence.current) setError(userMessage(reason, 'offer')) }
    finally { if (request === sequence.current) setLoading(false) }
  }

  return <section aria-label="门店优惠比较" className="plango-card border-brand/60 p-5">
    <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex items-center gap-2 text-xs text-[var(--muted)]"><Ticket size={15} />同店优惠比较 · {comparison.entries.length} 项</div><h3 className="mt-1 font-semibold text-lg leading-7 break-words">{comparison.merchant.name || '门店待核对'}</h3></div><SourceBadge source="browser" /></div>
    <p className="mt-2 flex items-start gap-2 text-sm leading-6 text-neutral-600"><MapPin size={15} className="mt-1 shrink-0" /><span className="break-words">{comparison.merchant.address || '网页门店地址尚未提供'}</span></p>
    <p className="mt-3 text-sm leading-6 text-brand-ink">{comparison.constraints.party_size === null ? '人数待确认' : `${comparison.constraints.party_size} 人`} · {comparison.constraints.visit_date || '日期待确认'} · {comparison.constraints.budget === null ? '未设总预算' : `总预算 ¥${comparison.constraints.budget}`}{comparison.constraints.per_person_budget === null ? '' : ` · 人均上限 ¥${comparison.constraints.per_person_budget}`}</p>
    <p className="mt-1 text-xs leading-5 text-[var(--muted)]">可在上方修改人数、日期和预算。售价、券面值与全部消费费用分别展示。</p>
    {(expired || !comparison.source.valid) && <p role="status" className="mt-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-800">原页面资料已过期或来源尚未核实，请重新读取并核对这家门店后再选用。</p>}
    {unresolved && <p role="status" className="mt-3 text-xs leading-6 text-amber-800">请先核对原任务尚未确认的操作结果，再选用门店优惠。</p>}
    <div className="mt-4 space-y-4">
      {comparison.entries.map(entry => {
        const status = (expired || !comparison.source.valid) && entry.status === 'eligible' ? 'unknown' : entry.status
        const selected = sameOfferSource(run?.selected_offer || undefined, comparison.source_ref) && run?.selected_offer?.offer_index === entry.offer_index && run?.selected_offer?.offer_hash === entry.offer_hash
        const Icon = status === 'eligible' ? CheckCircle2 : status === 'ineligible' ? XCircle : AlertTriangle
        return <article key={`${entry.offer_index}:${entry.offer_hash}`} aria-label={`优惠：${entry.name}`} className="rounded-xl border border-[var(--line)] bg-[var(--surface-soft)] p-4 min-w-0">
          <div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><span className="text-[11px] text-[var(--muted)]">{kindLabels[entry.kind]}</span><h4 className="mt-1 font-semibold text-base leading-6 break-words">{entry.name}</h4>{selected && <span className="mt-1 inline-block rounded bg-brand-soft px-2 py-0.5 text-xs font-medium text-brand-strong">本次已选</span>}</div><span className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs leading-5 ${status === 'eligible' ? 'bg-emerald-50 text-emerald-700' : status === 'ineligible' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-800'}`}><Icon size={14} className="shrink-0" />{statusLabels[status]}</span></div>
          <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1"><span className="text-xs text-[var(--muted)]">优惠售价</span><strong className={`tabular-nums ${entry.price === null ? 'text-sm text-amber-800' : 'text-2xl text-brand-ink'}`}>{entry.price === null ? '待核验' : `¥${entry.price}`}</strong>{entry.price !== null && <span className="text-xs text-neutral-600">{priceUnits[entry.price_basis]}</span>}{entry.face_value !== null && <span className="text-sm text-neutral-600">券面值 ¥{entry.face_value}</span>}{entry.original_price !== null && <span className="text-xs text-neutral-500">原文原价 ¥{entry.original_price}</span>}</div>
          <p className="mt-2 text-sm leading-6 text-neutral-600">{entry.people === null ? entry.kind === 'single_item' ? '单品份量不能证明全员够用' : '覆盖人数待核对' : `原文标注 ${entry.people} 人`}{entry.known_cost !== null ? ` · 已知费用部分 ¥${entry.known_cost}` : ''}</p>
          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">{entry.total_cost === null ? '完整消费费用待核对；未据此保证总预算够用。' : `本优惠覆盖的消费费用 ¥${entry.total_cost}${entry.within_budget === true ? ' · 在当前预算内' : entry.within_budget === false ? ' · 超出当前预算' : ' · 预算未核定'}；交通及其他支出另计。`}</p>
          {!!entry.reasons.length && <div className="mt-3 text-sm leading-6 text-neutral-700"><h5 className="font-medium">判断依据</h5><ul className="mt-1 space-y-1 list-disc pl-5">{entry.reasons.map((reason, index) => <li key={index} className="break-words">{reason}</li>)}</ul></div>}
          {!!entry.missing_rules.length && <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-6 text-amber-900"><h5 className="font-medium">仍需核对</h5><ul className="list-disc pl-4">{entry.missing_rules.map((rule, index) => <li key={index} className="break-words">{rule}</li>)}</ul></div>}
          <details className="mt-3 text-sm"><summary className="cursor-pointer py-1 text-[var(--muted)]">查看优惠原文与来源</summary><blockquote className="mt-2 border-l-2 border-brand/40 pl-3 leading-6 text-neutral-600 whitespace-pre-wrap break-words">{entry.quote || '原文未提供'}</blockquote><p className="mt-2 text-xs text-[var(--muted)]">读取：{timestamp(comparison.source.observed_at)}<br />页面资料有效至：{timestamp(comparison.source.expires_at)}</p>{/^https?:\/\//i.test(comparison.source.url) && <button onClick={() => navigate(comparison.source.url)} className="mt-2 text-xs text-brand-strong underline">打开来源页面</button>}</details>
          <button disabled={disabled || status === 'ineligible' || !entry.offer_hash} onClick={() => void findMerchant(entry.offer_hash)} className="mt-4 w-full rounded-xl bg-brand px-3 py-2.5 text-sm font-medium text-brand-ink disabled:opacity-40">{status === 'ineligible' ? '先调整不适用的条件' : '选用此优惠 · 先核对门店'}</button>
          {choosing === entry.offer_hash && <div aria-label="核对门店候选" className="mt-4 border-t border-[var(--line)] pt-4 text-sm">
            <h5 className="font-semibold">核对具体分店</h5><p className="mt-1 text-xs leading-6 text-[var(--muted)]">将所选优惠用于本次行程前，请核对门店名称和地址。</p>
            {loading && <p role="status" className="mt-2 text-xs">正在查找门店候选…</p>}
            {error && <p role="alert" className="mt-2 text-xs leading-6 text-red-700 break-words">{error}</p>}
            {candidates && <><p className="mt-2 text-xs text-[var(--muted)]">来源：{candidates.source === 'amap' ? '高德地图' : '来源未提供'} · 查询于 {timestamp(candidates.observed_at)}</p>
              {!candidates.candidates.length && <p className="mt-2 text-xs text-amber-800">尚未找到可核对的门店，不会自动合并其他分店。可先补全地址或重新读取门店页面。</p>}
              <fieldset disabled={disabled} className="mt-3 space-y-2">{candidates.candidates.map(poi => <label key={poi.poi_id} className={`flex items-start gap-3 rounded-xl border p-3 cursor-pointer ${selectedPoi === poi.poi_id ? 'border-brand-strong bg-white' : 'border-[var(--line)]'}`}><input type="radio" name={`merchant-${runId}-${entry.offer_hash}`} value={poi.poi_id} checked={selectedPoi === poi.poi_id} onChange={() => setSelectedPoi(poi.poi_id)} className="mt-1 shrink-0" /><span className="min-w-0"><b className="block text-sm leading-6 break-words">{poi.name}</b><span className="block mt-1 text-xs leading-5 text-neutral-600 break-words">{poi.address || '地址未提供，无法核对分店'}</span></span></label>)}</fieldset>
              {selectedPoi && <dl className="mt-3 grid gap-3 rounded-xl bg-white p-3 text-xs leading-6 sm:grid-cols-2"><div className="min-w-0"><dt className="font-semibold">网页门店</dt><dd className="break-words">{comparison.merchant.name}<br />{comparison.merchant.address}</dd></div><div className="min-w-0"><dt className="font-semibold">地图候选</dt><dd className="break-words">{candidates.candidates.find(poi => poi.poi_id === selectedPoi)?.name}<br />{candidates.candidates.find(poi => poi.poi_id === selectedPoi)?.address}</dd></div></dl>}
              {!!candidates.candidates.length && <button disabled={disabled || !selectedPoi || !candidates.candidates.some(poi => poi.poi_id === selectedPoi && poi.address)} onClick={() => void selectOffer(runId, { expected_version: version, source_ref: comparison.source_ref, offer_index: entry.offer_index, offer_hash: entry.offer_hash, poi_id: selectedPoi, identity_confirmed: true })} className="plango-primary mt-3 w-full disabled:opacity-40">确认两处资料是同一家门店，加入行程</button>}<p className="mt-2 text-xs leading-6 text-[var(--muted)]">此确认记录你的同店判断；优惠规则与可预约状态仍需核对。</p></>}
          </div>}
        </article>
      })}
    </div>
    {!!comparison.limitations.length && <div className="mt-4 text-xs leading-6 text-[var(--muted)]">{comparison.limitations.map((note, index) => <p key={index}>{note}</p>)}</div>}
    <p className="mt-3 text-xs leading-6 text-[var(--muted)]">选用后会继续当前行程，保留缺失规则与费用。优惠尚未购买，也未提交预约或订单。</p>
  </section>
}
