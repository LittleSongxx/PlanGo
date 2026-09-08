import { useEffect, useState } from 'react'
import { useStore } from '../store'
import type { ReminderList } from '@shared/types'
import { X, Cpu, MessageCircle, Puzzle, Brain, Check, Loader2, Plug, Heart, Lock, MapPin, Bell } from 'lucide-react'

// 右侧面板：模型/API 切换 + 微信/飞书入口 + 技能 + 记忆呈现（多 Agent/越用越懂的"外部旋钮"）。
type Tab = 'model' | 'social' | 'skills' | 'memory' | 'reminders'

// 常见 OpenAI 兼容供应商预设（抄 yoyu .env 三件套：BASE_URL/KEY/MODEL），key 用户自填。
const PRESETS: { id: string; name: string; baseURL: string; model: string; note: string }[] = [
  { id: 'longcat', name: 'LongCat-2.0', baseURL: 'https://api.longcat.chat/openai/v1', model: 'LongCat-2', note: '美团 · 默认' },
  { id: 'minimax', name: 'MiniMax-M2', baseURL: 'https://api.minimaxi.com/v1', model: 'MiniMax-M2', note: '多模态·视觉攻略' },
  { id: 'deepseek', name: 'DeepSeek', baseURL: 'https://api.deepseek.com/v1', model: 'deepseek-chat', note: '性价比' },
  { id: 'kimi', name: 'Kimi (Moonshot)', baseURL: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k', note: '长上下文' }
]

export function SidePanel(): JSX.Element | null {
  const open = useStore((s) => s.sidePanelOpen)
  const setOpen = useStore((s) => s.setSidePanelOpen)
  const [tab, setTab] = useState<Tab>('model')

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setOpen(false)}>
      <div className="absolute inset-0 bg-black/20" />
      <div className="relative w-[400px] max-w-[92vw] h-full bg-white shadow-2xl flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="h-12 shrink-0 flex items-center px-4 border-b border-neutral-200">
          <Plug size={16} className="text-brand-ink" />
          <span className="font-semibold text-sm ml-2">连接与能力</span>
          <button onClick={() => setOpen(false)} className="ml-auto p-1.5 rounded hover:bg-neutral-100">
            <X size={16} />
          </button>
        </div>

        <div className="flex gap-1 px-3 py-2 border-b border-neutral-100">
          <TabBtn cur={tab} me="model" set={setTab} icon={<Cpu size={13} />} label="模型" />
          <TabBtn cur={tab} me="social" set={setTab} icon={<MessageCircle size={13} />} label="微信/飞书" />
          <TabBtn cur={tab} me="skills" set={setTab} icon={<Puzzle size={13} />} label="技能" />
          <TabBtn cur={tab} me="memory" set={setTab} icon={<Brain size={13} />} label="记忆" />
          <TabBtn cur={tab} me="reminders" set={setTab} icon={<Bell size={13} />} label="提醒" />
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {tab === 'model' && <ModelSection />}
          {tab === 'social' && <SocialSection />}
          {tab === 'skills' && <SkillsSection />}
          {tab === 'memory' && <MemorySection />}
          {tab === 'reminders' && <ReminderSection />}
        </div>
      </div>
    </div>
  )
}

function TabBtn({ cur, me, set, icon, label }: { cur: Tab; me: Tab; set: (t: Tab) => void; icon: JSX.Element; label: string }): JSX.Element {
  return (
    <button
      onClick={() => set(me)}
      className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${
        cur === me ? 'bg-brand text-brand-ink' : 'text-neutral-500 hover:bg-neutral-100'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}

function ModelSection(): JSX.Element {
  const [cfg, setCfg] = useState<any>(null)
  const [baseURL, setBaseURL] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [ping, setPing] = useState('')

  useEffect(() => {
    window.xiaonian.getConfig().then((r) => {
      setCfg(r.config)
      setBaseURL(r.config?.llm?.baseURL || '')
      setModel(r.config?.llm?.model || '')
    })
  }, [])

  const applyPreset = (p: (typeof PRESETS)[number]): void => {
    setBaseURL(p.baseURL)
    setModel(p.model)
  }
  const save = async (): Promise<void> => {
    setSaving(true)
    setPing('')
    const patch: any = { llm: { baseURL, model } }
    if (apiKey.trim()) patch.llm.apiKey = apiKey.trim()
    try {
      await window.xiaonian.setConfig(patch)
      const r = await window.xiaonian.pingLlm()
      setPing(r.ok ? '连通正常 ✓ ' + r.message : '连接失败：' + r.message)
      setApiKey('')
      const config = await window.xiaonian.getConfig()
      setCfg(config.config)
      await useStore.getState().hydrateHarness()
    } catch (e) { setPing('保存失败：' + String(e)) }
    finally { setSaving(false) }
  }

  return (
    <div className="space-y-3">
      <div className="text-xs text-neutral-500">选一个大模型供应商，或手动填 BASE_URL / MODEL / KEY。保存后应用到本地运行服务，任务状态会保留。</div>
      <div className="grid grid-cols-2 gap-2">
        {PRESETS.map((p) => {
          const active = baseURL === p.baseURL
          return (
            <button
              key={p.id}
              onClick={() => applyPreset(p)}
              className={`text-left rounded-xl border p-2.5 transition-colors ${active ? 'border-brand bg-brand/10' : 'border-neutral-200 hover:border-neutral-300'}`}
            >
              <div className="text-sm font-medium flex items-center gap-1">
                {p.name}
                {active && <Check size={13} className="text-brand-ink" />}
              </div>
              <div className="text-[10px] text-neutral-400 mt-0.5">{p.note}</div>
            </button>
          )
        })}
      </div>

      <Field label="BASE_URL">
        <input value={baseURL} onChange={(e) => setBaseURL(e.target.value)} className="inp" placeholder="https://api.xxx.com/v1" />
      </Field>
      <Field label="MODEL">
        <input value={model} onChange={(e) => setModel(e.target.value)} className="inp" placeholder="模型名" />
      </Field>
      <Field label={`API KEY${cfg?.hasLlmKey ? '（已配置，留空则不改）' : ''}`}>
        <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" className="inp" placeholder={cfg?.llm?.apiKey || '粘贴 API Key'} />
      </Field>

      <button onClick={save} disabled={saving} className="w-full py-2 rounded-xl bg-brand text-brand-ink font-medium text-sm flex items-center justify-center gap-1.5 disabled:opacity-50">
        {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} 应用并测试连通
      </button>
      {ping && <div className={`text-xs ${ping.includes('✓') ? 'text-green-600' : 'text-red-500'}`}>{ping}</div>}

      <style>{`.inp{width:100%;border:1px solid #e5e5e5;border-radius:10px;padding:8px 10px;font-size:13px;outline:none}.inp:focus{border-color:#ffb800}`}</style>
    </div>
  )
}

function SocialSection(): JSX.Element {
  const [im, setIm] = useState<{ connected: boolean; note: string } | null>(null)
  useEffect(() => { window.xiaonian.imStatus().then(setIm).catch(() => setIm({ connected: false, note: '连接状态获取失败' })) }, [])
  return <div className="space-y-3 text-xs text-neutral-600">
    <div className="rounded-xl border border-neutral-200 p-3"><div className="font-semibold text-sm mb-2">微信 / 飞书 · 尚未接入</div>{im?.note || '当前未连接消息平台。'}</div>
    <div>打开行程卡的「分享给同行人」，复制链接或使用二维码，让同行人投票和留下意见；意见可以继续并入当前方案。</div>
  </div>
}

function SkillsSection(): JSX.Element {
  const [skills, setSkills] = useState<any[]>([])
  useEffect(() => {
    window.xiaonian.listSkills().then(setSkills)
  }, [])
  const toggle = async (id: string, enabled: boolean): Promise<void> => {
    const next = await window.xiaonian.toggleSkill(id, enabled)
    setSkills(next)
  }
  return (
    <div className="space-y-2">
      <div className="text-xs text-neutral-500">已装技能（命中意图时小悠自动展开）。企业/个人可加装自定义 Skill。</div>
      {skills.length === 0 ? (
        <div className="text-xs text-neutral-400 text-center py-6">暂无技能</div>
      ) : (
        skills.map((s) => (
          <div key={s.id} className="rounded-xl border border-neutral-200 p-2.5 flex items-start gap-2">
            <Puzzle size={15} className="text-brand-ink mt-0.5 shrink-0" />
            <div className="flex-1">
              <div className="text-sm font-medium">{s.name}</div>
              <div className="text-[11px] text-neutral-500 mt-0.5">{s.description}</div>
            </div>
            <button
              onClick={() => toggle(s.id, !s.enabled)}
              className={`shrink-0 w-10 h-5 rounded-full relative transition-colors ${s.enabled ? 'bg-brand' : 'bg-neutral-300'}`}
            >
              <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${s.enabled ? 'left-[22px]' : 'left-0.5'}`} />
            </button>
          </div>
        ))
      )}
      <div className="text-[10px] text-neutral-400 mt-2">安装入口：把技能包放进 skills/ 目录即可被加载（loader 自动发现）。</div>
    </div>
  )
}

function MemorySection(): JSX.Element {
  const [prof, setProf] = useState<any>(null)
  useEffect(() => {
    window.xiaonian.getMemory().then(setProf)
  }, [])
  const prefs = prof?.preferences || []
  const del = async (kind: 'pref' | 'fav', value: string): Promise<void> => {
    const next = await window.xiaonian.memoryDelete({ kind, value })
    setProf(next)
  }
  const clearAll = async (): Promise<void> => {
    if (!confirm('清空小悠记住的所有偏好？此操作不可撤销。')) return
    const next = await window.xiaonian.memoryClear()
    setProf(next)
  }
  const footprints = prof?.footprints || []
  const days = prof?.since ? Math.max(1, Math.round((Date.now() - prof.since) / 86400000)) : 0
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        <Heart size={14} className="text-red-400" /> 小悠记住的你
        {(prefs.length > 0 || (prof?.favorite_shops?.length || 0) > 0) && (
          <button onClick={clearAll} className="ml-auto text-[11px] text-neutral-400 hover:text-red-500">清空</button>
        )}
      </div>

      {/* 数据主权：本地隐私说明（这是"为什么是浏览器"的第六根支柱） */}
      <div className="rounded-xl bg-emerald-50 border border-emerald-200 p-2.5">
        <div className="flex items-center gap-1.5 text-[12px] font-medium text-emerald-700">
          <Lock size={12} /> 记忆可查看、可删除
        </div>
        <div className="text-[11px] text-emerald-600/90 mt-1 leading-relaxed">
          记忆保存在你配置的运行服务中，用于后续任务。浏览器登录信息留在本机；任务所需上下文会发送给你配置的模型。
        </div>
      </div>

      {(days > 0 || footprints.length > 0) && (
        <div className="text-[11px] text-neutral-500">
          小悠已经<span className="text-brand-ink font-medium">陪你 {days} 天</span>，一起去过 <span className="text-brand-ink font-medium">{footprints.length}</span> 个地方。
        </div>
      )}

      <div className="text-[11px] text-neutral-400">这些偏好会在规划/点菜时自动带入，你不用每次重复说。可随时删除（透明可控）。</div>
      {prof?.summary && <div className="text-xs text-neutral-600 bg-neutral-50 rounded-lg p-2.5 border border-neutral-200">{prof.summary}</div>}
      <div className="text-xs text-neutral-500">偏好（越用越懂，来自你的每次选择）：</div>
      {prefs.length === 0 ? (
        <div className="text-xs text-neutral-400 text-center py-6">还没攒下偏好，多聊几次小悠就懂你了。</div>
      ) : (
        <div className="space-y-1.5">
          {prefs.map((c: any, i: number) => (
            <div key={i} className="group flex items-center gap-2 text-xs rounded-lg border border-neutral-200 px-2.5 py-1.5">
              <span className={`px-1.5 py-0.5 rounded text-[10px] ${c.polarity === 'negative' ? 'bg-red-50 text-red-500' : 'bg-green-50 text-green-600'}`}>
                {c.polarity === 'negative' ? '不爱' : '偏好'}
              </span>
              <span className="flex-1">{c.text}</span>
              <span className="text-[10px] text-neutral-400">×{c.evidence_count}</span>
              <button onClick={() => del('pref', c.text)} className="opacity-0 group-hover:opacity-100 text-neutral-400 hover:text-red-500" title="删除">
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
      {(prof?.favorite_shops?.length || 0) > 0 && (
        <div>
          <div className="text-xs text-neutral-500 mb-1">常去/喜欢的店：</div>
          <div className="flex flex-wrap gap-1">
            {prof.favorite_shops.map((s: string) => (
              <span key={s} className="group flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-brand/10 text-brand-ink">
                {s}
                <button onClick={() => del('fav', s)} className="opacity-50 hover:opacity-100 hover:text-red-500" title="删除">
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {footprints.length > 0 && (
        <div>
          <div className="text-xs text-neutral-500 mb-1.5 flex items-center gap-1">
            <MapPin size={12} className="text-brand-ink" /> 周末足迹（小悠陪你走过的）
          </div>
          <div className="relative pl-3.5">
            <div className="absolute left-1 top-1 bottom-1 w-px bg-neutral-200" />
            {footprints.map((f: { date: string; place: string; scene: string; note?: string }, i: number) => (
              <div key={i} className="relative mb-2.5 last:mb-0">
                <span className="absolute -left-[9px] top-1 w-1.5 h-1.5 rounded-full bg-brand" />
                <div className="text-[11px] text-neutral-400">{f.date} · {f.scene}</div>
                <div className="text-xs text-neutral-700 font-medium">{f.place}</div>
                {f.note && <div className="text-[11px] text-neutral-500">{f.note}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <label className="block">
      <div className="text-[11px] text-neutral-500 mb-1">{label}</div>
      {children}
    </label>
  )
}

function ReminderSection(): JSX.Element {
  const [data, setData] = useState<ReminderList>({ reminders: [], history: [] })
  const [text, setText] = useState('')
  const [at, setAt] = useState(() => {
    const next = new Date(Date.now() + 3600000)
    return new Date(next.getTime() - next.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    const refresh = async (): Promise<void> => {
      try { const value = await window.xiaonian.reminders.list(); if (active) setData(value) }
      catch (e) { if (active) setError(String(e)) }
    }
    void refresh()
    const timer = setInterval(() => void refresh(), 5000)
    return () => { active = false; clearInterval(timer) }
  }, [])
  const create = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    const date = new Date(at)
    if (!text.trim() || !Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) { setError('请输入提醒内容和未来的时间。'); return }
    setBusy(true)
    setError('')
    try { setData(await window.xiaonian.reminders.create(text.trim(), date.toISOString())); setText('') }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  const remove = async (id: string): Promise<void> => {
    setBusy(true)
    setError('')
    try { setData(await window.xiaonian.reminders.remove(id)) }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  return <div className="space-y-4 text-xs">
    <div className="text-neutral-500">提醒在应用打开时显示。关闭期间到时的提醒，会在下次连接运行服务时补发。</div>
    <form onSubmit={event => void create(event)} className="space-y-2 rounded-xl border border-neutral-200 p-3">
      <label className="block">提醒内容<input value={text} onChange={event => setText(event.target.value)} required maxLength={2000} placeholder="如：出门前确认预约" className="mt-1 w-full border border-neutral-200 rounded-lg px-2 py-2" /></label>
      <label className="block">提醒时间（本机时区）<input type="datetime-local" value={at} onChange={event => setAt(event.target.value)} required className="mt-1 w-full border border-neutral-200 rounded-lg px-2 py-2" /></label>
      <button disabled={busy} className="w-full rounded-lg bg-brand py-2 text-brand-ink font-medium disabled:opacity-50">添加提醒</button>
    </form>
    {error && <div role="alert" className="rounded-lg bg-amber-50 p-2 text-amber-800 break-words">{error}</div>}
    <div><div className="font-semibold text-sm mb-2">待提醒</div>
      {data.reminders.filter(item => !item.fired).map(item => <div key={item.id} className="flex items-start gap-2 py-2 border-b border-neutral-100">
        <Bell size={13} className="mt-0.5 text-brand-ink shrink-0" /><div className="flex-1 min-w-0"><div className="break-words">{item.text}</div><div className="text-neutral-400 mt-1">{new Date(item.at).toLocaleString()}</div></div>
        <button disabled={busy} onClick={() => void remove(item.id)} title="删除提醒" className="p-1 text-neutral-400 hover:text-red-500"><X size={13} /></button>
      </div>)}
      {!data.reminders.some(item => !item.fired) && <div className="text-neutral-400">还没有待提醒事项。</div>}
    </div>
    {!!data.history.length && <div><div className="font-semibold text-sm mb-2">提醒记录</div>{data.history.map(item => <div key={item.id} className="py-2 border-b border-neutral-100"><div className="text-neutral-600 break-words">{item.text}</div><div className="text-neutral-400 mt-1">{new Date(item.ts).toLocaleString()}</div></div>)}</div>}
  </div>
}
