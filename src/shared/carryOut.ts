import type { Plan, PlanNode } from './types'

export const CARRY_OUT_FOOTER = '这不是预约或支付回执。出门前请再核对营业、优惠和交通。'
export const CARRY_OUT_FORBIDDEN = ['已预订', '已支付', '已下单', '履约成功', '业务已完成'] as const

export type CarryOutVerdict = 'go' | 'hold'

export interface CarryOutStop {
  time: string
  title: string
  address: string
  navUrl?: string
}

export interface CarryOut {
  verdict: CarryOutVerdict
  headline: string
  title: string
  date?: string
  timezone: string
  partyLabel: string
  costLabel: string
  stops: CarryOutStop[]
  pending: string[]
  footer: string
  planId?: string
  version?: number
  runId?: string
}

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/
const CLOCK = /^(\d{1,2}):(\d{2})$/

export function carryOutForbiddenHit(text: string): string | undefined {
  return CARRY_OUT_FORBIDDEN.find(word => text.includes(word))
}

export function carryOutFromPlan(plan: Plan): CarryOut {
  const stops = (plan.nodes || []).map(node => asStop(plan, node)).filter((stop): stop is CarryOutStop => !!stop)
  const date = DATE.test(plan.visit_date || '') ? plan.visit_date : undefined
  const timezone = plan.timezone?.trim() || 'Asia/Shanghai'
  const verdict: CarryOutVerdict = stops.length && date ? 'go' : 'hold'
  const headline = !stops.length ? '先别出门 · 还没有下一站' : !date ? '先别出门 · 还不知道哪天' : '可以带着走'
  const partyLabel = typeof plan.party_size === 'number' && plan.party_size > 0 ? `${plan.party_size} 人` : '人数待确认'
  const costLabel = plan.total_cost == null ? '费用待核验' : `已知估算小计 ¥${plan.total_cost}`
  return {
    verdict,
    headline,
    title: plan.title?.trim() || '当前方案',
    date,
    timezone,
    partyLabel,
    costLabel,
    stops,
    pending: pendingNotes(plan),
    footer: CARRY_OUT_FOOTER,
    planId: plan.plan_id,
    version: plan.version,
    runId: plan.run_id
  }
}

export function formatCarryOutText(carry: CarryOut): string {
  const when = [carry.date, timezoneLabel(carry.timezone)].filter(Boolean).join(' · ')
  const lines = [
    carry.headline,
    [carry.title, when].filter(Boolean).join(' · '),
    `${carry.partyLabel} · ${carry.costLabel}`,
    '',
    ...carry.stops.flatMap(stop => [
      stop.time ? `${stop.time}  ${stop.title}` : stop.title,
      ...(stop.address ? [`地址：${stop.address}`] : []),
      ...(stop.navUrl ? [`导航 ${stop.navUrl}`] : [])
    ]),
    ...(carry.pending.length ? ['', `仍要现场核对：${carry.pending.join('；')}`] : []),
    '',
    carry.footer
  ]
  const text = lines.join('\n').replace(/\n{3,}/g, '\n\n').trim()
  const hit = carryOutForbiddenHit(text)
  if (hit) throw new Error(`carry_out_forbidden:${hit}`)
  return text
}

export function formatCarryOutIcs(plan: Plan, now = new Date()): string | undefined {
  const carry = carryOutFromPlan(plan)
  if (!carry.date) return undefined
  const tz = carry.timezone || 'Asia/Shanghai'
  const start = firstClock(plan) || { h: 9, m: 0 }
  const rawEnd = lastClock(plan)
  const end = !rawEnd || minutes(rawEnd) <= minutes(start) ? addHours(start, 2) : rawEnd
  const summary = icsEscape(`${carry.headline} · ${carry.title}`)
  const description = icsEscape(formatCarryOutText(carry))
  const location = icsEscape(carry.stops.find(stop => stop.address)?.address || '')
  const uid = `${safeToken(plan.plan_id || 'plan')}-v${plan.version || 1}@plango.local`
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//PlanGo//CarryOut//ZH',
    'CALSCALE:GREGORIAN',
    'BEGIN:VEVENT',
    `UID:${uid}`,
    `DTSTAMP:${utcStamp(now)}`,
    `DTSTART;TZID=${tz}:${icsLocal(carry.date, start)}`,
    `DTEND;TZID=${tz}:${icsLocal(carry.date, end)}`,
    `SUMMARY:${summary}`,
    `DESCRIPTION:${description}`,
    ...(location ? [`LOCATION:${location}`] : []),
    'BEGIN:VALARM',
    'TRIGGER:-PT60M',
    'ACTION:DISPLAY',
    `DESCRIPTION:${icsEscape(carry.headline)}`,
    'END:VALARM',
    'END:VEVENT',
    'END:VCALENDAR'
  ]
  const ics = lines.map(foldIcs).join('\r\n') + '\r\n'
  const hit = carryOutForbiddenHit(ics)
  if (hit) throw new Error(`carry_out_forbidden:${hit}`)
  return ics
}

export function formatCarryOutHtml(carry: CarryOut): string {
  const stops = carry.stops.map(stop => `<div class="stop"><div class="t">${esc(stop.time)}</div><div class="n">${esc(stop.title)}</div>${
    stop.address ? `<div class="s">地址：${esc(stop.address)}</div>` : ''}${
    stop.navUrl ? `<div class="s">导航 ${esc(stop.navUrl)}</div>` : ''}</div>`).join('')
  const pending = carry.pending.length ? `<div class="pending"><b>仍要现场核对</b><div>${esc(carry.pending.join('；'))}</div></div>` : ''
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"/><style>
html,body{margin:0;background:#f8f7f3;color:#2c2924;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.card{padding:32px 28px 28px;width:664px}
.k{font-size:13px;font-weight:700;letter-spacing:.04em;color:${carry.verdict === 'go' ? '#1a6b3c' : '#8a5a00'}}
h1{font-size:26px;margin:10px 0 8px;line-height:1.35}
.m{font-size:14px;color:#78746a;line-height:1.7}
.stop{padding:12px 0;border-bottom:1px dashed #e8e5dd}
.t{font-size:13px;color:#78746a}
.n{font-size:18px;font-weight:650;margin:4px 0}
.s{font-size:13px;color:#78746a;line-height:1.6;word-break:break-all}
.pending{margin-top:16px;background:#fffbef;border:1px solid #ecdcb1;border-radius:12px;padding:12px 14px;font-size:13px;color:#816025;line-height:1.6}
.foot{margin-top:18px;font-size:12px;color:#78746a;line-height:1.6}
</style></head><body><div class="card">
<div class="k">${esc(carry.headline)}</div>
<h1>${esc(carry.title)}</h1>
<div class="m">${esc([carry.date, timezoneLabel(carry.timezone), carry.partyLabel, carry.costLabel].filter(Boolean).join(' · '))}</div>
${stops}${pending}
<div class="foot">${esc(carry.footer)}</div>
</div></body></html>`
  const hit = carryOutForbiddenHit(html)
  if (hit) throw new Error(`carry_out_forbidden:${hit}`)
  return html
}

export function amapNavUrl(input: {
  destLng: number
  destLat: number
  destName: string
  mode?: Plan['travel_mode']
  origin?: Plan['origin']
}): string | undefined {
  if (!Number.isFinite(input.destLng) || !Number.isFinite(input.destLat)) return undefined
  const navMode = { driving: 'car', walking: 'walk', transit: 'bus' }[input.mode || 'driving']
  const to = `${input.destLng},${input.destLat},${encodeURIComponent(input.destName)}`
  const from = input.origin && Number.isFinite(input.origin.longitude) && Number.isFinite(input.origin.latitude)
    ? `&from=${input.origin.longitude},${input.origin.latitude},${encodeURIComponent(input.origin.name || '设定起点')}` : ''
  return `https://uri.amap.com/navigation?to=${to}${from}&mode=${navMode}&policy=1&src=plango&coordinate=gaode&callnative=1`
}

export function carryOutReminderAt(plan: Plan, now = new Date()): string | undefined {
  const date = DATE.test(plan.visit_date || '') ? plan.visit_date! : undefined
  const clock = firstClock(plan)
  if (!date || !clock) return undefined
  const timezone = plan.timezone?.trim() || 'Asia/Shanghai'
  if (timezone !== 'Asia/Shanghai') return undefined
  const start = new Date(`${date}T${pad(clock.h)}:${pad(clock.m)}:00+08:00`)
  if (!Number.isFinite(start.getTime())) return undefined
  const at = new Date(start.getTime() - 60 * 60_000)
  if (at.getTime() <= now.getTime()) return undefined
  return shanghaiIso(at)
}

export function carryOutFileStem(plan: Plan): string {
  const carry = carryOutFromPlan(plan)
  const title = carry.title.replace(/[\\/:*?"<>|]+/g, '').replace(/\s+/g, '').slice(0, 40) || '安排'
  return `PlanGo-${carry.date || 'draft'}-${title}-v${plan.version || 1}`
}

function asStop(plan: Plan, node: PlanNode): CarryOutStop | undefined {
  const title = node.title?.trim()
  if (!title) return undefined
  const time = [node.time_start, node.time_end].filter(Boolean).join('–')
  const address = node.poi?.address?.trim() || ''
  const navUrl = node.poi && Number.isFinite(node.poi.lng) && Number.isFinite(node.poi.lat)
    ? amapNavUrl({ destLng: Number(node.poi.lng), destLat: Number(node.poi.lat), destName: title, mode: plan.travel_mode, origin: plan.origin })
    : undefined
  return { time, title, address, navUrl }
}

function pendingNotes(plan: Plan): string[] {
  return [...new Set([...(plan.validation_notes || []), ...(plan.cost_breakdown?.pending || [])]
    .map(note => note.trim()).filter(Boolean)
    .filter(note => !note.includes('尚不具备执行条件')))]
}

function timezoneLabel(timezone: string): string {
  return !timezone || timezone === 'Asia/Shanghai' ? '北京时间' : timezone
}

function firstClock(plan: Plan): { h: number; m: number } | undefined {
  for (const node of plan.nodes || []) {
    const clock = parseClock(node.time_start)
    if (clock) return clock
  }
  return undefined
}

function lastClock(plan: Plan): { h: number; m: number } | undefined {
  const nodes = plan.nodes || []
  for (let i = nodes.length - 1; i >= 0; i--) {
    const clock = parseClock(nodes[i].time_end) || parseClock(nodes[i].time_start)
    if (clock) return clock
  }
  return undefined
}

function parseClock(value?: string): { h: number; m: number } | undefined {
  const match = CLOCK.exec(value?.trim() || '')
  if (!match) return undefined
  const h = Number(match[1]), m = Number(match[2])
  return h <= 23 && m <= 59 ? { h, m } : undefined
}

function minutes(clock: { h: number; m: number }): number {
  return clock.h * 60 + clock.m
}

function addHours(clock: { h: number; m: number }, hours: number): { h: number; m: number } {
  const total = Math.min(23 * 60 + 59, minutes(clock) + hours * 60)
  return { h: Math.floor(total / 60), m: total % 60 }
}

function icsLocal(date: string, clock: { h: number; m: number }): string {
  return `${date.replace(/-/g, '')}T${pad(clock.h)}${pad(clock.m)}00`
}

function utcStamp(now: Date): string {
  return now.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
}

function shanghaiIso(value: Date): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
  }).formatToParts(value)
  const get = (type: string) => parts.find(part => part.type === type)?.value || '00'
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}+08:00`
}

function icsEscape(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/\r\n|\n|\r/g, '\\n').replace(/,/g, '\\,').replace(/;/g, '\\;')
}

function foldIcs(line: string): string {
  if (line.length <= 74) return line
  let out = line.slice(0, 74)
  let rest = line.slice(74)
  while (rest.length) {
    out += `\r\n ${rest.slice(0, 73)}`
    rest = rest.slice(73)
  }
  return out
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function safeToken(value: string): string {
  return value.replace(/[^A-Za-z0-9._-]+/g, '').slice(0, 64) || 'plan'
}

function esc(value: string): string {
  return value.replace(/[&<>"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]!))
}
