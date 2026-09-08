import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { X, Wifi, Puzzle, MessageCircle, Bell, Database, Brain, MapPin } from 'lucide-react'
import { detectViaAMap, geocodeAddress } from '../lib/amap'

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
    window.xiaonian.getConfig().then((r) => {
      setConfig(r.config)
    })
    window.xiaonian.listSkills().then(setSkills)
    window.xiaonian.imStatus().then(setIm)
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setSettings(false)}>
      <div className="absolute inset-0 bg-black/20" />
      <div className="relative w-[380px] h-full bg-white shadow-2xl overflow-y-auto animate-in" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-white h-12 flex items-center justify-between px-4 border-b border-neutral-200">
          <span className="font-semibold">设置</span>
          <button onClick={() => setSettings(false)} className="p-1.5 rounded hover:bg-neutral-100">
            <X size={16} />
          </button>
        </div>

        <div className="p-4 space-y-5 text-sm">
          {/* 定位 */}
          <LocationSection />

          <Section icon={<Database size={15} />} title="运行服务与真实数据">
            <div className="text-xs text-neutral-500">{backendReady ? '运行服务已连接' : '运行服务未连接'} · 真实高德与浏览器页面</div>
            <div className="text-[11px] text-neutral-400 mt-1 break-all">{config?.harness?.baseURL}</div>
            <button onClick={() => void reconnect()} className="mt-2 px-3 py-1 text-xs rounded bg-neutral-100">检查连接</button>
          </Section>

          {/* LLM */}
          <Section icon={<Wifi size={15} />} title={`大模型 · ${config?.llm?.model || '未配置'}`}>
            <div className="text-xs text-neutral-500">{config?.hasLlmKey ? `已配置 Key（${config?.llm?.apiKey}）` : '未配置 Key，请在 .env 填写'}</div>
            <button
              onClick={async () => {
                setPing('测试中…')
                const r = await window.xiaonian.pingLlm()
                setPing(r.ok ? '连通 ✅ ' + r.message : '失败 ❌ ' + r.message)
              }}
              className="mt-2 text-xs px-3 py-1 rounded-lg bg-neutral-100 hover:bg-neutral-200"
            >
              测试连通
            </button>
            {ping && <span className="ml-2 text-xs text-neutral-500">{ping}</span>}
          </Section>

          {/* 技能 */}
          <Section icon={<Puzzle size={15} />} title="可安装技能（Agent Skills）">
            <div className="space-y-1.5">
              {skills.map((s) => (
                <label key={s.id} className="flex items-start gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={async (e) => {
                      const next = await window.xiaonian.toggleSkill(s.id, e.target.checked)
                      setSkills(next)
                    }}
                    className="mt-0.5"
                  />
                  <span>
                    <b>{s.name}</b> <span className="text-neutral-400">— {s.description}</span>
                  </span>
                </label>
              ))}
              {!skills.length && <div className="text-xs text-neutral-400">未发现技能。把 skills/&lt;id&gt;/SKILL.md 拖进 skills 目录即可安装。</div>}
            </div>
          </Section>

          <Section icon={<MessageCircle size={15} />} title="同行人协作">
            <div className="text-xs text-neutral-500">{im?.note || '微信和飞书尚未接入。可在行程卡中生成真实分享链接，由你发送给同行人。'}</div>
          </Section>

          <Section icon={<Bell size={15} />} title="主动提醒">
            <div className="text-xs text-neutral-500">有实际运行事件或已保存的提醒时显示通知。</div>
          </Section>

          {/* 记忆 */}
          <Section icon={<Brain size={15} />} title="记忆（越用越懂）">
            <MemoryView />
          </Section>
        </div>
      </div>
    </div>
  )
}

function LocationSection(): JSX.Element {
  const city = useStore((s) => s.city)
  const district = useStore((s) => s.district)
  const citySource = useStore((s) => s.citySource)
  const coords = useStore((s) => s.coords)
  const locAccuracy = useStore((s) => s.locAccuracy)
  const setLocationInfo = useStore((s) => s.setLocationInfo)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const precise = citySource === 'gps' || citySource === 'amap-gps'

  // 重新定位：客户端 GPS（会触发系统定位授权），精确到街道
  const redetect = async () => {
    setBusy(true)
    setMsg('正在请求 GPS 定位（首次可能弹出系统授权，请允许）…')
    try {
      const l = await detectViaAMap()
      if (l?.coords || l?.city) {
        setLocationInfo({ city: l.city, district: l.district, coords: l.coords, source: l.source, accuracy: l.accuracy })
        window.xiaonian.reportLocation({ city: l.city, coords: l.coords })
        setMsg(l.source === 'gps' || l.source === 'amap-gps' ? `已精确定位到 ${l.city}${l.district ? '·' + l.district : ''}` : `仅取到城市级：${l.city}（GPS 不可用，可在下方手动指定我的位置）`)
      } else setMsg('定位失败，请检查网络或在下方手动指定')
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
        setLocationInfo({ city: l.city || city, district: l.district, coords: l.coords, source: 'gps', accuracy: 30 })
        window.xiaonian.reportLocation({ city: l.city || city, coords: l.coords })
        setMsg(`已把「${c}」设为我的位置`)
        setInput('')
      } else {
        // 兜底：仅当城市名处理
        const r = await window.xiaonian.setCity(c)
        setLocationInfo({ city: r.city, source: 'manual' })
        setMsg(`已设为城市：${r.city}`)
        setInput('')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section icon={<MapPin size={15} />} title="定位（我的实时位置）">
      <div className="flex items-center gap-2">
        <span className="text-lg font-semibold text-brand-ink">{city}{district ? ' · ' + district : ''}</span>
        <span className={`text-[11px] px-1.5 py-0.5 rounded-full ${precise ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}`}>
          {precise ? `GPS精确${locAccuracy ? ' ±' + Math.round(locAccuracy) + 'm' : ''}` : 'IP城市级'}
        </span>
        <button onClick={redetect} disabled={busy} className="ml-auto text-xs px-2 py-1 rounded-lg bg-brand text-brand-ink disabled:opacity-50">
          {busy ? '定位中…' : '重新定位(GPS)'}
        </button>
      </div>
      {coords && <div className="mt-1 text-[11px] text-neutral-400">坐标：{coords}</div>}
      <div className="flex gap-1.5 mt-2">
        <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && apply()} placeholder="手动指定我的位置，如 深圳湾科技生态园 / 南山区" className="flex-1 text-xs border border-neutral-200 rounded px-2 py-1" />
        <button onClick={apply} disabled={busy} className="text-xs px-2 py-1 rounded bg-brand text-brand-ink disabled:opacity-50">设为我的位置</button>
      </div>
      {msg && <div className="mt-1 text-[11px] text-brand-ink">{msg}</div>}
      <div className="mt-1 text-[11px] text-neutral-400">桌面 GPS 需系统授权（系统设置→隐私与安全性→定位服务）。拿不到就手动指定地标，规划会以此为圆心搜"附近"。</div>
    </Section>
  )
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }): JSX.Element {
  return (
    <div>
      <div className="flex items-center gap-1.5 font-medium mb-2 text-neutral-700">
        {icon} {title}
      </div>
      <div className="pl-1">{children}</div>
    </div>
  )
}

function MemoryView(): JSX.Element {
  const [mem, setMem] = useState<any>(null)
  useEffect(() => {
    window.xiaonian.getMemory().then(setMem)
  }, [])
  if (!mem) return <div className="text-xs text-neutral-400">加载中…</div>
  return (
    <div className="text-xs text-neutral-600 space-y-1">
      {mem.summary ? <div>画像：{mem.summary}</div> : <div className="text-neutral-400">还没积累画像，多用几次小悠就懂你了。</div>}
      {mem.preferences?.length ? <div>偏好：{mem.preferences.map((p: any) => (p.polarity === 'negative' ? '不喜欢' : '喜欢') + p.text).join('；')}</div> : null}
      {mem.favorite_shops?.length ? <div>常去：{mem.favorite_shops.join('、')}</div> : null}
    </div>
  )
}
