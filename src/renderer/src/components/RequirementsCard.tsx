import { useState } from 'react'
import type { HarnessSnapshot, RequirementFields } from '@shared/types'
import { row, rows } from '../lib/harnessProjection'
import { useStore } from '../store'

const fieldLabels: Partial<Record<keyof RequirementFields, string>> = {
  location_name: '出发地点', search_location_name: '搜索中心', search_radius_km: '搜索半径（公里）', route_distance_km: '单段路程上限（公里）',
  visit_date: '出行日期', time_window_start: '开始时间', party_size: '同行人数',
  budget: '总预算（元）', per_person_budget: '人均预算（元）', travel_mode: '交通方式'
}

function valuesOf(run: HarnessSnapshot): Record<keyof RequirementFields, string> {
  const spec = row(run.state.trip_spec || run.state.previous_spec || run.offer_comparison?.constraints)
  return Object.fromEntries(Object.keys(fieldLabels).map(key => [key,
    String(key === 'location_name' ? row(spec.location).name || '' : key === 'search_location_name' ? row(spec.search_location).name || '' : key === 'route_distance_km' ? spec.max_distance_km ?? (Array.isArray(spec.hard_constraints) && spec.hard_constraints.includes('距离优先') ? 5 : '') : key === 'search_radius_km' && spec.search_radius_km === undefined ? spec.max_distance_km ?? '' : spec[key] ?? '')
  ])) as Record<keyof RequirementFields, string>
}

export function RequirementsCard(): JSX.Element | null {
  const run = useStore(s => s.run)
  if (!run?.version || (!Object.keys(row(run.state.trip_spec || run.state.previous_spec)).length && !run.offer_comparison)) return null
  return <RequirementForm key={`${run.run_id}:${run.version}`} run={run} />
}

function RequirementForm({ run }: { run: HarnessSnapshot }): JSX.Element {
  const busy = useStore(s => s.busy)
  const ready = useStore(s => s.backendReady)
  const edit = useStore(s => s.editRequirements)
  const pendingDelivery = useStore(s => s.pendingDelivery)
  const planning = !!Object.keys(row(run.state.trip_spec || run.state.previous_spec)).length
  const visibleFields = (Object.keys(fieldLabels) as (keyof RequirementFields)[]).filter(key => planning || ['party_size', 'visit_date', 'budget', 'per_person_budget'].includes(key))
  const [initial] = useState(() => valuesOf(run))
  const [values, setValues] = useState(initial)
  const changed = (Object.keys(fieldLabels) as (keyof RequirementFields)[]).filter(key => values[key] !== initial[key])
  const unresolved = !!run.state.browser_receipt_pending || rows(run.state.action_results).some(action => ['RUNNING', 'UNKNOWN'].includes(String(action.status)))
  const disabled = busy || !ready || !!pendingDelivery || unresolved || !!run.command_pending || !!run.cancel_requested
  const spec = row(run.state.trip_spec || run.state.previous_spec || run.offer_comparison?.constraints)
  const plan = row(run.state.selected_plan || run.state.previous_plan)
  const setValue = (key: keyof RequirementFields, value: string): void => setValues(current => ({ ...current, [key]: value }))
  return <section aria-label="行程需求" className="plango-workspace-col plango-card p-5 mb-5">
    <div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold text-base text-brand-ink">{planning ? '行程需求' : '优惠比较条件'}</h2><p className="text-xs text-[var(--muted)] mt-1">{planning ? '改人数、日期、预算、地点请在此提交；对话里的补充不会自动覆盖已确认字段。' : '按同行人数、日期与预算重新核对当前门店优惠，保留原始资料。'}</p></div><span className="text-[11px] text-[var(--muted)]">{spec.timezone === 'Asia/Shanghai' ? '北京时间' : String(spec.timezone || '')}</span></div>
    <form onSubmit={event => {
      event.preventDefault()
      if (disabled || !changed.length) return
      const numeric = new Set(['party_size', 'budget', 'per_person_budget', 'search_radius_km', 'route_distance_km'])
      const fields = Object.fromEntries(changed.map(key => [key, values[key] === '' ? null : numeric.has(key) ? Number(values[key]) : values[key]])) as RequirementFields
      void edit({ expected_version: run.version!, fields, ...(!planning && run.offer_comparison ? { offer_source: run.offer_comparison.source_ref } : {}) })
    }}>
      <fieldset disabled={disabled} className="grid grid-cols-2 gap-3 mt-4 disabled:opacity-60">
        {visibleFields.map(key => <label key={key} className={`block text-xs text-neutral-600 ${['location_name', 'search_location_name'].includes(key) ? 'col-span-2 sm:col-span-1' : ''}`}>
          {fieldLabels[key]}
          {key === 'travel_mode' ? <select aria-label={fieldLabels[key]} className="plango-field mt-1" value={values[key]} onChange={event => setValue(key, event.target.value)}><option value="driving">驾车</option><option value="walking">步行</option><option value="transit">公共交通</option></select>
            : <input aria-label={fieldLabels[key]} className="plango-field mt-1" value={values[key]} onChange={event => setValue(key, event.target.value)}
              type={key === 'visit_date' ? 'date' : key === 'time_window_start' ? 'time' : ['party_size', 'budget', 'per_person_budget', 'search_radius_km', 'route_distance_km'].includes(key) ? 'number' : 'text'}
              required={changed.includes(key) && ['location_name', 'search_location_name', 'party_size'].includes(key)} maxLength={200}
              min={key === 'party_size' ? 1 : ['search_radius_km', 'route_distance_km'].includes(key) ? 0.1 : 0} max={key === 'party_size' ? 12 : key === 'search_radius_km' ? 50 : key === 'route_distance_km' ? 1000 : 1_000_000}
              step={key === 'party_size' ? 1 : ['search_radius_km', 'route_distance_km'].includes(key) ? 0.1 : key === 'time_window_start' ? 60 : 'any'}
              placeholder={['budget', 'per_person_budget', 'route_distance_km'].includes(key) ? '不限' : key === 'search_radius_km' ? '默认附近 5 公里' : '待确认'} />}
        </label>)}
      </fieldset>
      <p className="text-[11px] text-[var(--muted)] mt-3 leading-5">{planning ? '搜索半径围绕搜索中心；单段路程上限核对实际路线，两者独立。' : '售价与全部用餐费用分别核对。'}预算留空即取消对应上限；日期或时间留空表示待确认。</p>
      {planning && spec.max_distance_km == null && Array.isArray(spec.hard_constraints) && spec.hard_constraints.includes('距离优先') && <p className="text-[11px] text-[var(--muted)] mt-1">“就近”沿用每段 5 公里参考上限，可修改或清空。</p>}
      <div className="flex flex-wrap items-center gap-2 mt-3">
        <button type="submit" disabled={disabled || !changed.length} className="plango-primary disabled:opacity-40">{planning ? '保存需求并重新规划' : '保存条件并重新比较'}</button>
        <button type="button" disabled={disabled || (!values.budget && !values.per_person_budget)} onClick={() => setValues(current => ({ ...current, budget: '', per_person_budget: '' }))} className="text-xs px-3 py-2 rounded-lg border border-[var(--line)] disabled:opacity-40">取消全部预算上限</button>
        {!!changed.length && <button type="button" disabled={disabled} onClick={() => setValues(initial)} className="text-xs px-2 py-2 text-neutral-500">撤销修改</button>}
      </div>
    </form>
    {!!rows(plan.stops).length && <div className="border-t border-[var(--line)] mt-4 pt-3"><p className="text-xs font-medium">单站锁定 · 当前方案 v{String(plan.version)}</p><p className="text-[11px] text-[var(--muted)] mt-1">保留该站地点和时段，营业、价格等信息仍会重新核验。调整时段前可先解锁。</p>
      <div className="space-y-2 mt-3">{rows(plan.stops).map(stop => <div key={String(stop.place_id)} className="flex items-center gap-3 text-xs"><span className="flex-1">{String(stop.name)}</span><button type="button" aria-pressed={stop.locked === true} disabled={disabled || !!changed.length} onClick={() => void edit({ expected_version: run.version!, stop_lock: { plan_id: String(plan.plan_id), plan_version: Number(plan.version), place_id: String(stop.place_id), locked: stop.locked !== true } })} className={`rounded-lg border px-3 py-1.5 disabled:opacity-40 ${stop.locked ? 'border-brand-strong bg-brand-soft text-brand-ink' : 'border-[var(--line)]'}`}>{stop.locked ? '已锁定 · 解锁' : '锁定此站'}</button></div>)}</div>
    </div>}
    {unresolved && <p role="status" className="text-xs text-amber-700 mt-3">请先核对尚未确认的操作结果，再修改需求。</p>}
  </section>
}
