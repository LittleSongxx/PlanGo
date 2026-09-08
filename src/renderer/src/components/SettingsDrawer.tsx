import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { X, Wifi, Puzzle, MessageCircle, Bell, Database, Brain, MapPin } from 'lucide-react'
import { detectViaAMap, geocodeAddress } from '../lib/amap'
import { locationLabel } from '@shared/location'
import { DialogShell } from './DialogShell'

export function SettingsDrawer(): JSX.Element | null {
  const open = useStore((s) => s.settingsOpen)
  const setSettings = useStore((s) => s.setSettings)
  const [config, setConfig] = useState<any>(null)
  const [skills, setSkills] = useState<any[]>([])
  const [im, setIm] = useState<{ connected: boolean; note: string } | null>(null)
  const [ping, setPing] = useState<string>('')
  const backendReady = useStore((s) => s.backendReady)
  const reconnect = useStore((s) => s.hydrateHarness)

  useEffect(() => {
    if (!open) return
    window.plango.getConfig().then((r) => {
      setConfig(r.config)
    })
    window.plango.listSkills().then(setSkills)
    window.plango.imStatus().then(setIm)
  }, [open])

  if (!open) return null

  return (
    <DialogShell label="设置" onClose={() => setSettings(false)} className="plango-dialog-drawer w-[460px]">
        <div className="plango-panel-header justify-between shrink-0">
          <div><div className="plango-kicker">YOUR PREFERENCES</div><h2 className="text-lg font-semibold mt-1">设置</h2></div>
          <button onClick={() => setSettings(false)} aria-label="关闭设置" className="plango-icon-button">
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-4 text-[13px] overflow-y-auto flex-1 bg-[#f7f9f6]">
          {/* 定位 */}
          <LocationSection />

          <Section icon={<Database size={15} />} title="运行服务与真实数据">
            <div className="text-xs text-neutral-500">{backendReady ? '运行服务已连接' : '运行服务未连接'} · 真实高德与浏览器页面</div>
            <div className="text-[11px] text-neutral-400 mt-1 break-all">{config?.harness?.baseURL}</div>
            <button onClick={() => void reconnect()} className="mt-3 px-3 py-2 text-xs rounded-lg border border-[var(--line)] bg-[#f7faf7]">检查连接</button>
          </Section>

          {/* LLM */}
          <Section icon={<Wifi size={15} />} title={`大模型 · ${config?.llm?.model || '未配置'}`}>
            <div className="text-xs text-neutral-500">{config?.hasLlmKey ? `已配置 Key（${config?.llm?.apiKey}）` : '尚未配置，可在连接与能力中填写' }</div>
            <button
              onClick={async () => {
                setPing('测试中…')
                const r = await window.plango.pingLlm()
                setPing(r.ok ? '连通 ✅ ' + r.message : '失败 ❌ ' + r.message)
              }}
              className="mt-2 text-xs px-3 py-1 rounded-lg bg-neutral-100 hover:bg-neutral-200"
            >
              测试连通
            </button>
            {ping && <span className="ml-2 text-xs text-neutral-500">{ping}</span>}
          </Section>

          {/* 技能 */}
          <Section icon={<Puzzle size={15} />} title="已安装技能">
            <div className="text-xs text-[var(--muted)] mb-3">开关对新任务生效，当前任务保持原设置。</div>
            <div className="space-y-1.5">
              {skills.map((s) => (
                <label key={s.id} className="flex items-start gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={async (e) => {
                      const next = await window.plango.toggleSkill(s.id, e.target.checked)
                      setSkills(next)
                    }}
                    className="mt-0.5"
                  />
                  <span>
                    <b>{s.name}</b> <span className="text-neutral-400">— {s.description}</span>
                  </span>
                </label>
              ))}
              {!skills.length && <div className="text-xs text-neutral-400">当前没有已安装技能。</div>}
            </div>
          </Section>

          <Section icon={<MessageCircle size={15} />} title="同行人协作">
            <div className="text-xs text-neutral-500">{im?.note || '微信和飞书尚未接入。可在行程卡中生成真实分享链接，由你发送给同行人。'}</div>
          </Section>

          <Section icon={<Bell size={15} />} title="主动提醒">
            <div className="text-xs text-neutral-500">有实际运行事件或已保存的提醒时显示通知。</div>
          </Section>

          {/* 记忆 */}
          <Section icon={<Brain size={15} />} title="已保存的偏好">
            <MemoryView />
          </Section>
        </div>
    </DialogShell>
  )
}

function LocationSection(): JSX.Element {
  const city = useStore((s) => s.city)
  const district = useStore((s) => s.district)
  const citySource = useStore((s) => s.citySource)
  const coords = useStore((s) => s.coords)
  const locAccuracy = useStore((s) => s.locAccuracy)
  const granularity = useStore((s) => s.locationGranularity)
  const setLocationInfo = useStore((s) => s.setLocationInfo)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const precise = citySource === 'gps' || citySource === 'amap-gps'

  // 重新定位：客户端 GPS（会触发系统定位授权），精确到街道
  const redetect = async () => {
    setBusy(true)
    setMsg('正在请求设备定位（首次可能弹出系统授权）…')
    try {
      const l = await detectViaAMap()
      if (l?.coords || l?.city) {
        const saved = await window.plango.reportLocation({ ...l, city: l.city || city, userInitiated: true })
        setLocationInfo(saved)
        setMsg(`${locationLabel(saved.source, saved.accuracy, saved.granularity)}：${saved.city}${saved.district ? '·' + saved.district : ''}`)
      } else setMsg('定位失败，请检查网络或在下方手动指定')
    } catch { setMsg('定位或保存失败，请重试；未确认的位置不会用于规划。')
    } finally {
      setBusy(false)
    }
  }
  // 手动指定"我在哪"：地址/地标 → 精确坐标（桌面无 GPS 时最可靠）
  const apply = async () => {
    const c = input.trim()
    if (!c) return
    setBusy(true)
    try {
      const l = await geocodeAddress(c, city && city !== '定位中…' ? city : undefined)
      if (l?.coords) {
        const saved = await window.plango.reportLocation({ ...l, city: l.city || city, userInitiated: true })
        setLocationInfo(saved)
        const coarse = ['city', 'district', 'unknown'].includes(l.granularity || 'unknown')
        const level = { city: '城市级', district: '地区级', address: '地址级', point: '位置点', unknown: '粒度未知' }[l.granularity || 'unknown']
        setMsg(`高德匹配为「${l.formattedAddress || l.city}」（${[l.district, level].filter(Boolean).join(' · ')}），已设为${coarse ? '地区参考点' : '规划起点'}。请核对是否为你期望的位置。`)
        setInput('')
      } else {
        // 兜底：仅当城市名处理
        const r = await window.plango.setCity(c)
        setLocationInfo(r)
        setMsg(`已设为城市：${r.city}`)
        setInput('')
      }
    } catch { setMsg('地址解析或保存失败，请检查网络后重试。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section icon={<MapPin size={15} />} title="规划起点">
      <div className="flex items-center gap-2">
        <span className="text-lg font-semibold text-brand-ink">{city}{district ? ' · ' + district : ''}</span>
        <span className={`text-[11px] px-1.5 py-0.5 rounded-full ${precise ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}`}>
          {locationLabel(citySource, locAccuracy, granularity)}
        </span>
        <button onClick={redetect} disabled={busy} className="ml-auto text-xs px-2 py-1 rounded-lg bg-brand text-brand-ink disabled:opacity-50">
          {busy ? '定位中…' : '重新定位'}
        </button>
      </div>
      {coords && <div className="mt-1 text-[11px] text-neutral-400">坐标：{coords}</div>}
      <div className="flex gap-1.5 mt-2">
        <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && apply()} placeholder="手动指定我的位置，如 重庆解放碑 / 观音桥" className="plango-field flex-1 min-w-0" />
        <button onClick={apply} disabled={busy} className="text-xs px-2 py-1 rounded bg-brand text-brand-ink disabled:opacity-50">设为我的位置</button>
      </div>
      {msg && <div role="status" className="mt-1 text-[11px] text-brand-ink">{msg}</div>}
      <div className="mt-1 text-[11px] text-neutral-400">设备定位需系统授权。也可手动指定地标作为规划起点；地址解析不代表设备位置或测量精度。</div>
    </Section>
  )
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }): JSX.Element {
  return (
    <section className="plango-section">
      <div className="flex items-center gap-2 font-semibold mb-3 text-brand-ink">
        {icon} {title}
      </div>
      <div>{children}</div>
    </section>
  )
}

function MemoryView(): JSX.Element {
  const [mem, setMem] = useState<any>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    window.plango.getMemory().then(setMem).catch(() => setFailed(true))
  }, [])
  if (failed) return <div role="status" className="text-xs text-amber-700">记忆暂时无法读取，请恢复连接后重新打开设置。</div>
  if (!mem) return <div className="text-xs text-neutral-400">加载中…</div>
  return (
    <div className="text-xs text-neutral-600 space-y-1">
      {mem.summary ? <div>近期任务记忆：{mem.summary}</div> : <div className="text-neutral-400">尚无任务记忆。你可以在「连接与能力 → 记忆」中明确保存偏好。</div>}
      {mem.preferences?.length ? <div>保存的偏好：{mem.preferences.map((p: any) => (p.explicit ? '' : '历史记录：') + (p.polarity === 'negative' ? '不喜欢' : '喜欢') + p.text).join('；')}</div> : null}
      {mem.favorite_shops?.length ? <div>保存的地点：{mem.favorite_shops.join('、')}</div> : null}
    </div>
  )
}
