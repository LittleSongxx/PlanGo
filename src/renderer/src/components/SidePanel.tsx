import { useEffect, useState } from 'react'
import { useStore } from '../store'
import type { ReminderList } from '@shared/types'
import { DialogShell } from './DialogShell'
import { ExecutionServiceCard } from './ExecutionServiceCard'
import { X, Cpu, MessageCircle, Puzzle, Brain, Check, Loader2, Plug, Heart, Lock, MapPin, Bell } from 'lucide-react'

// 右侧面板：模型/API 切换 + 微信/飞书入口 + 技能 + 记忆呈现（多 Agent/越用越懂的"外部旋钮"）。
type Tab = 'model' | 'social' | 'skills' | 'memory' | 'reminders'

// Existing OpenAI-compatible provider presets; credentials are supplied by the user.
const PRESETS: { id: string; name: string; baseURL: string; model: string; note: string }[] = [
  { id: 'longcat', name: 'LongCat-2.0', baseURL: 'https://api.longcat.chat/openai/v1', model: 'LongCat-2', note: '预设配置' },
  { id: 'minimax', name: 'MiniMax-M2', baseURL: 'https://api.minimaxi.com/v1', model: 'MiniMax-M2', note: '预设配置' },
  { id: 'deepseek', name: 'DeepSeek', baseURL: 'https://api.deepseek.com/v1', model: 'deepseek-chat', note: '预设配置' },
  { id: 'kimi', name: 'Kimi (Moonshot)', baseURL: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k', note: '预设配置' }
]

export function SidePanel(): JSX.Element | null {
  const open = useStore((s) => s.sidePanelOpen)
  const setOpen = useStore((s) => s.setSidePanelOpen)
  const [tab, setTab] = useState<Tab>('model')

  if (!open) return null
  return (
    <DialogShell label="连接与能力" onClose={() => setOpen(false)} className="plango-dialog-drawer w-[500px]">
        <div className="plango-panel-header shrink-0">
          <Plug size={16} className="text-brand-ink" />
          <div className="ml-3"><div className="plango-kicker">MAKE IT YOURS</div><h2 className="font-semibold text-lg mt-1">连接与能力</h2></div>
          <button onClick={() => setOpen(false)} aria-label="关闭连接与能力" className="ml-auto plango-icon-button">
            <X size={16} />
          </button>
        </div>

        <div className="flex gap-1 m-5 mb-0 p-1 rounded-xl bg-[#f0f5f1]" role="tablist" aria-label="能力类型">
          <TabBtn cur={tab} me="model" set={setTab} icon={<Cpu size={13} />} label="模型" />
          <TabBtn cur={tab} me="social" set={setTab} icon={<MessageCircle size={13} />} label="微信/飞书" />
          <TabBtn cur={tab} me="skills" set={setTab} icon={<Puzzle size={13} />} label="技能" />
          <TabBtn cur={tab} me="memory" set={setTab} icon={<Brain size={13} />} label="记忆" />
          <TabBtn cur={tab} me="reminders" set={setTab} icon={<Bell size={13} />} label="提醒" />
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {tab === 'model' && <ModelSection />}
          {tab === 'social' && <SocialSection />}
          {tab === 'skills' && <SkillsSection />}
          {tab === 'memory' && <MemorySection />}
          {tab === 'reminders' && <ReminderSection />}
        </div>
    </DialogShell>
  )
}

function TabBtn({ cur, me, set, icon, label }: { cur: Tab; me: Tab; set: (t: Tab) => void; icon: JSX.Element; label: string }): JSX.Element {
  return (
    <button
      onClick={() => set(me)} role="tab" aria-selected={cur === me}
      className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium transition-colors min-h-9 ${
        cur === me ? 'bg-white text-brand-strong shadow-card' : 'text-[#748378] hover:text-brand-ink'
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
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    window.plango.getConfig().then((r) => {
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
    const patch: any = { llm: { model, ...(baseURL.trim() ? { baseURL: baseURL.trim() } : {}) } }
    if (apiKey.trim()) patch.llm.apiKey = apiKey.trim()
    try {
      await window.plango.setConfig(patch)
      const r = await window.plango.pingLlm()
      setPing(r.ok ? '桌面配置已保存，桌面文本测试通过 ✓ ' + r.message : '桌面配置已保存，桌面测试失败：' + r.message)
      setApiKey('')
      const config = await window.plango.getConfig()
      setCfg(config.config)
      setRevision(value => value + 1)
      await useStore.getState().hydrateHarness()
    } catch { setPing('保存或重新连接未完成，请刷新服务状态并核对桌面配置。') }
    finally { setSaving(false) }
  }

  return (
    <div className="space-y-3">
      <ExecutionServiceCard revision={revision} />
      <h3 className="font-semibold text-sm pt-2">桌面模型配置</h3>
      <div className="text-xs text-neutral-500">预设只填接口与模型名称，不保证账户可用。保存与下方测试只使用桌面配置；独立 Docker 服务不会被覆盖。</div>
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

      <Field label="服务地址（BASE_URL）">
        <input value={baseURL} onChange={(e) => setBaseURL(e.target.value)} className="plango-field" placeholder="https://api.xxx.com/v1（留空保留原地址）" />
      </Field>
      <Field label="模型名称（MODEL）">
        <input value={model} onChange={(e) => setModel(e.target.value)} className="plango-field" placeholder="模型名" />
      </Field>
      <Field label={`API KEY${cfg?.hasLlmKey ? '（已配置，留空则不改）' : ''}`}>
        <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" className="plango-field" placeholder={cfg?.llm?.apiKey || '粘贴 API Key'} />
      </Field>

      <button onClick={save} disabled={saving} className="w-full py-2 rounded-xl bg-brand-strong text-white font-medium text-sm flex items-center justify-center gap-1.5 disabled:opacity-50">
        {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} 保存桌面配置并测试
      </button>
      {ping && <div className={`text-xs ${ping.includes('✓') ? 'text-green-600' : 'text-red-500'}`}>{ping}</div>}

    </div>
  )
}

function SocialSection(): JSX.Element {
  const [im, setIm] = useState<{ connected: boolean; note: string } | null>(null)
  useEffect(() => { window.plango.imStatus().then(setIm).catch(() => setIm({ connected: false, note: '连接状态获取失败' })) }, [])
  return <div className="space-y-3 text-xs text-neutral-600">
    <div className="rounded-xl border border-neutral-200 p-3"><div className="font-semibold text-sm mb-2">微信 / 飞书 · 尚未接入</div>{im?.note || '当前未连接消息平台。'}</div>
    <div>打开行程卡的「分享给同行人」，复制链接或使用二维码，让同行人投票和留下意见；意见可以继续并入当前方案。</div>
  </div>
}

function SkillsSection(): JSX.Element {
  const [skills, setSkills] = useState<any[]>([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState<string | null>(null)
  useEffect(() => {
    window.plango.listSkills().then(setSkills).catch(() => setError('技能列表读取失败，请重新打开面板重试。'))
  }, [])
  const toggle = async (id: string, enabled: boolean): Promise<void> => {
    setSaving(id)
    setError('')
    try { setSkills(await window.plango.toggleSkill(id, enabled)) }
    catch { setError('技能设置未保存，请重试。') }
    finally { setSaving(null) }
  }
  return (
    <div className="space-y-2">
      <div className="text-xs text-neutral-500">按任务需要使用已启用的技能。开关对新任务生效，当前任务保持原设置。</div>
      {error && <p role="alert" className="text-xs text-amber-700 bg-amber-50 rounded-lg p-3">{error}</p>}
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
              onClick={() => toggle(s.id, !s.enabled)} disabled={saving !== null} role="switch" aria-checked={s.enabled} aria-label={s.name}
              className={`shrink-0 w-10 h-5 rounded-full relative transition-colors ${s.enabled ? 'bg-brand-strong' : 'bg-neutral-300'}`}
            >
              <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${s.enabled ? 'left-[22px]' : 'left-0.5'}`} />
            </button>
          </div>
        ))
      )}
      <div className="text-[10px] text-neutral-400 mt-2">自定义技能需安装到本机后才会出现在此列表。</div>
    </div>
  )
}

function MemorySection(): JSX.Element {
  const [prof, setProf] = useState<any>(null)
  const [preference, setPreference] = useState('')
  const [polarity, setPolarity] = useState<'like' | 'dislike'>('like')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    window.plango.getMemory().then(setProf).catch(() => setError('偏好读取失败，请重新打开面板重试。'))
  }, [])
  const prefs = prof?.preferences || []
  const mutate = async (action: () => Promise<any>): Promise<void> => {
    setSaving(true); setError('')
    try { setProf(await action()); await useStore.getState().refreshRun() } catch { setError('记忆变更尚未确认保存，请重试。') } finally { setSaving(false) }
  }
  const add = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    if (!preference.trim() || saving) return
    await mutate(async () => { const value = await window.plango.memorySave(preference.trim(), polarity); setPreference(''); return value })
  }
  const del = async (kind: 'pref' | 'fav' | 'episode', value: string): Promise<void> => {
    await mutate(() => window.plango.memoryDelete({ kind, value }))
  }
  const clearAll = async (): Promise<void> => {
    if (!confirm('清空PlanGo保存的偏好、收藏和任务记忆？原始任务仍保留，此操作不可撤销。')) return
    await mutate(() => window.plango.memoryClear())
  }
  const footprints = prof?.footprints || []
  const episodes: { id: string; text: string; scope?: string; createdAt?: string }[] = prof?.episodes || []
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        <Heart size={14} className="text-red-400" /> PlanGo记住的你
        {(prefs.length > 0 || episodes.length > 0 || (prof?.favorite_shops?.length || 0) > 0) && (
          <button onClick={clearAll} disabled={saving} className="ml-auto text-[11px] text-neutral-400 hover:text-red-500">清空</button>
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

      <form onSubmit={event => void add(event)} className="plango-section space-y-3">
        <label className="block text-xs font-medium">明确记住一个偏好<input value={preference} onChange={event => setPreference(event.target.value)} maxLength={1000} required placeholder="如：聚餐时优先选择清淡口味" className="plango-field mt-2" /></label>
        <div className="flex gap-2"><select aria-label="偏好类型" value={polarity} onChange={event => setPolarity(event.target.value as 'like' | 'dislike')} className="plango-field flex-1"><option value="like">我偏好</option><option value="dislike">我希望避开</option></select><button disabled={saving || !preference.trim()} className="plango-primary disabled:opacity-40">保存偏好</button></div>
      </form>
      {error && <p role="alert" className="text-xs text-amber-700 rounded-lg bg-amber-50 p-3">{error}</p>}
      {prof?.since && <div className="text-[11px] text-[var(--muted)]">首次记录于 {new Date(prof.since).toLocaleDateString('zh-CN')}</div>}

      <div className="text-[11px] text-neutral-400">保存的偏好会用于后续规划；历史记录保留原来源，可随时查看和删除。</div>
      {!episodes.length && prof?.summary && <div className="text-xs text-neutral-600 bg-neutral-50 rounded-lg p-2.5 border border-neutral-200">{prof.summary}</div>}
      <div className="text-xs text-neutral-500">保存的偏好：</div>
      {prefs.length === 0 ? (
        <div className="text-xs text-neutral-400 text-center py-6">尚未保存偏好。可以在上方添加你希望后续规划考虑的事项。</div>
      ) : (
        <div className="space-y-1.5">
          {prefs.map((c: any, i: number) => (
            <div key={i} className="group flex items-center gap-2 text-xs rounded-lg border border-neutral-200 px-2.5 py-1.5">
              <span className={`px-1.5 py-0.5 rounded text-[10px] ${c.polarity === 'negative' ? 'bg-red-50 text-red-500' : 'bg-green-50 text-green-600'}`}>
                {c.polarity === 'negative' ? '不爱' : '偏好'}
              </span>
              <span className="flex-1">{c.text}<span className="block text-[10px] text-[var(--muted)] mt-1">{c.explicit ? '你明确保存' : '历史记录 · 未标注明确确认'}</span></span>
              <span className="text-[10px] text-neutral-400">×{c.evidence_count}</span>
              <button onClick={() => del('pref', c.text)} disabled={saving} className="opacity-0 group-hover:opacity-100 text-neutral-400 hover:text-red-500" title="删除">
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
                {s}<span className="text-[10px] text-[var(--muted)]">{prof.favorite_provenance?.[s]?.explicit ? '明确保存' : '历史记录'}</span>
                <button onClick={() => del('fav', s)} disabled={saving} className="opacity-50 hover:opacity-100 hover:text-red-500" title="删除">
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {episodes.length > 0 && <section className="space-y-2"><h3 className="text-xs font-semibold">任务记忆与反馈</h3><p className="text-[11px] text-[var(--muted)]">用于后续任务参考，历史记录不证明当前营业、价格或业务完成。</p>{episodes.map(item => <div key={item.id} className="plango-card p-3 text-xs flex items-start gap-3"><div className="flex-1 min-w-0"><div className="text-[10px] text-[var(--muted)] mb-1">{({ user_feedback: '明确反馈', read_only: '页面读取', image_text: '图片读取', ready_to_review: '准备待核对', price_comparison: '价格比较', plan_draft: '规划草案' } as Record<string, string>)[item.scope || ''] || '历史任务记录'}{item.createdAt ? ' · ' + new Date(item.createdAt).toLocaleString('zh-CN') : ''}</div><p className="whitespace-pre-wrap break-words leading-5">{item.text}</p></div><button disabled={saving} onClick={() => void del('episode', item.id)} className="plango-icon-button" aria-label="删除这条任务记忆"><X size={13} /></button></div>)}</section>}
      {footprints.length > 0 && (
        <div>
          <div className="text-xs text-neutral-500 mb-1.5 flex items-center gap-1">
            <MapPin size={12} className="text-brand-ink" /> 已保存的地点记录
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
      try { const value = await window.plango.reminders.list(); if (active) setData(value) }
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
    try { setData(await window.plango.reminders.create(text.trim(), date.toISOString())); setText('') }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  const remove = async (id: string): Promise<void> => {
    setBusy(true)
    setError('')
    try { setData(await window.plango.reminders.remove(id)) }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  return <div className="space-y-4 text-xs">
    <div className="text-neutral-500">提醒在应用打开时显示。关闭期间到时的提醒，会在下次连接运行服务时补发。</div>
    <form onSubmit={event => void create(event)} className="space-y-4 plango-section">
      <label className="block">提醒内容<input value={text} onChange={event => setText(event.target.value)} required maxLength={2000} placeholder="如：出门前确认预约" className="plango-field mt-2" /></label>
      <label className="block">提醒时间（本机时区）<input type="datetime-local" value={at} onChange={event => setAt(event.target.value)} required className="plango-field mt-2" /></label>
      <button disabled={busy} className="w-full rounded-lg bg-brand py-2 text-brand-ink font-medium disabled:opacity-50">添加提醒</button>
    </form>
    {error && <div role="alert" className="rounded-lg bg-amber-50 p-2 text-amber-800 break-words">{error}</div>}
    <div><div className="font-semibold text-sm mb-2">待提醒</div>
      {data.reminders.filter(item => !item.fired).map(item => <div key={item.id} className="flex items-start gap-2 py-2 border-b border-neutral-100">
        <Bell size={13} className="mt-0.5 text-brand-ink shrink-0" /><div className="flex-1 min-w-0"><div className="break-words">{item.text}</div><div className="text-neutral-400 mt-1">{new Date(item.at).toLocaleString('zh-CN')}</div></div>
        <button disabled={busy} onClick={() => void remove(item.id)} title="删除提醒" className="p-1 text-neutral-400 hover:text-red-500"><X size={13} /></button>
      </div>)}
      {!data.reminders.some(item => !item.fired) && <div className="text-neutral-400">还没有待提醒事项。</div>}
    </div>
    {!!data.history.length && <div><div className="font-semibold text-sm mb-2">提醒记录</div>{data.history.map(item => <div key={item.id} className="py-2 border-b border-neutral-100"><div className="text-neutral-600 break-words">{item.text}</div><div className="text-neutral-400 mt-1">{new Date(item.ts).toLocaleString('zh-CN')}</div></div>)}</div>}
  </div>
}
