import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { phaseLabel, row } from '../lib/harnessProjection'
import { StepFlow } from './StepFlow'
import { Markdown } from './Markdown'
import { Send, Bell, X, ImagePlus, Plus, History, Mic, MicOff, Sparkles, ArrowUpRight } from 'lucide-react'

const QUICK = [
  '这周六下午带老婆孩子出去玩4小时，孩子5岁，老婆减脂，预算人均120',
  '周末约4个朋友聚会，2男2女，找个能玩能吃的，热门店帮我取号',
  '帮我给这份行程比个价，再按减脂给餐厅点菜',
  '整理这份方案，方便我分享给同行人确认'
]

export function ChatPanel(): JSX.Element {
  const messages = useStore((s) => s.messages)
  const steps = useStore((s) => s.steps)
  const busy = useStore((s) => s.busy)
  const send = useStore((s) => s.send)
  const run = useStore((s) => s.run)
  const backendError = useStore((s) => s.backendError)
  const backendReady = useStore((s) => s.backendReady)
  const refresh = useStore((s) => s.hydrateHarness)
  const cancelRun = useStore((s) => s.cancelRun)
  const resumeBrowser = useStore((s) => s.resumeBrowser)
  const setView = useStore((s) => s.setView)
  const proactive = useStore((s) => s.proactive)
  const newSession = useStore((s) => s.newSession)
  const setHistoryOpen = useStore((s) => s.setHistoryOpen)
  const [text, setText] = useState('')
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [listening, setListening] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const recRef = useRef<any>(null)
  const supportsSpeech = typeof window !== 'undefined' && ((window as any).webkitSpeechRecognition || (window as any).SpeechRecognition)

  // 攻略截图导入：读成 dataURL 暂存到主进程，再让 import_guide 视觉抽取 → plan_outing
  const onPickImage = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f || busy) return
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(f.type) || f.size > 8_000_000) {
      useStore.setState({ backendError: '请上传 8 MB 以内的 PNG、JPEG 或 WebP 图片。' })
      return
    }
    const reader = new FileReader()
    reader.onload = async () => {
      const dataUrl = String(reader.result || '')
      if (!dataUrl.startsWith('data:image')) return
      const request = text.trim() || '请读取这张图片中的文字、地点和价格，保留图片来源，并注明无法实时核验的信息。'
      setText('')
      void send(request, dataUrl)
    }
    reader.onerror = () => useStore.setState({ backendError: '图片读取失败，请重新选择文件。' })
    reader.readAsDataURL(f)
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }, [messages, steps])

  useEffect(() => {
    return () => {
      try {
        recRef.current?.stop?.()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const submit = () => {
    const t = text.trim()
    if (!t || busy) return
    setText('')
    void send(t)
  }

  // 语音输入（Web Speech API），识别结果填进输入框
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
    const SR = (window as any).webkitSpeechRecognition || (window as any).SpeechRecognition
    const rec = new SR()
    rec.lang = 'zh-CN'
    rec.interimResults = true
    rec.continuous = false
    let finalText = ''
    rec.onresult = (e: any) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const seg = e.results[i][0].transcript
        if (e.results[i].isFinal) finalText += seg
        else interim += seg
      }
      setText((finalText + interim).trim())
    }
    rec.onend = () => setListening(false)
    rec.onerror = () => setListening(false)
    recRef.current = rec
    setListening(true)
    try {
      rec.start()
    } catch {
      setListening(false)
    }
  }

  const latestProactive = proactive[0]
  const showProactive = latestProactive && latestProactive.id !== dismissed

  return (
    <div className="h-full flex flex-col bg-white" aria-label="PlanGo 对话">
      <div className="plango-panel-header h-[74px] shrink-0 justify-between gap-2">
        <div><div className="plango-kicker">YOUR ASSISTANT</div><div className="mt-1 flex items-center gap-2 font-semibold text-[15px]"><Sparkles size={16} className="text-brand-strong" />对话助手</div></div>
        <div className="flex items-center gap-1">
          <button onClick={() => setHistoryOpen(true)} title="历史会话" className="plango-icon-button"><History size={17} /></button>
          <button onClick={newSession} title="新建对话" className="plango-icon-button bg-brand-soft text-brand-strong"><Plus size={18} /></button>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 px-5 py-2.5 text-[11px] bg-[#f7faf7] border-b border-[var(--line)]">
        <span role="status" className={backendReady ? 'text-[#607369]' : 'text-amber-700'}>{backendReady ? phaseLabel(run) : '未连接服务'}</span>
        {run && !run.outcome && <button onClick={() => void cancelRun()} className="text-neutral-500 hover:text-red-600">停止任务</button>}
        {!run && <span className="text-[var(--muted)]">从你的需求开始</span>}
      </div>

      {backendError && <div role="alert" className="mx-4 mt-3 rounded-xl bg-amber-50 border border-amber-200 p-2 text-xs text-amber-800">
        <div className="break-words">{backendError}</div>
        <button className="mt-1 underline" onClick={() => void refresh()}>重新连接并恢复任务</button>
      </div>}
      {run && (!!run.state.browser_wait || run.phase === 'WAITING_BROWSER') && <div className="mx-4 mt-3 rounded-xl bg-brand-soft border border-brand/30 p-2 text-xs">
        <div>{String(row(run.state.browser_wait).message || '请在浏览器中完成登录或接管操作，再继续。')}</div>
        <div className="mt-2 flex gap-2">
          <button onClick={() => setView('browser')} className="px-2 py-1 bg-white rounded">打开浏览器</button>
          <button disabled={busy || !backendReady} onClick={() => void resumeBrowser()} className="px-2 py-1 bg-brand rounded disabled:opacity-40">已处理，继续</button>
        </div>
      </div>}

      {showProactive && (
        <div className="mx-4 mt-3 p-3.5 rounded-xl bg-brand-soft border border-brand/40 flex items-start gap-2 animate-in">
          <Bell size={15} className="text-brand-ink mt-0.5 shrink-0" />
          <div className="flex-1 text-xs text-neutral-700">
            <div className="font-medium text-brand-ink mb-0.5">PlanGo主动关心</div>
            {latestProactive.text}
            <div className="mt-1.5">
              <button onClick={() => void send(latestProactive.text)} className="text-[11px] px-2 py-0.5 rounded bg-brand text-brand-ink font-medium mr-2">
                让PlanGo安排
              </button>
            </div>
          </div>
          <button onClick={() => setDismissed(latestProactive.id)} className="p-0.5 text-neutral-400 hover:text-neutral-700">
            <X size={13} />
          </button>
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-6 space-y-5" aria-label="对话记录">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'} animate-in`}>
            <div
              className={`max-w-[94%] px-4 py-3.5 rounded-2xl text-[13px] leading-[1.75] ${
                m.role === 'user' ? 'bg-brand-strong text-white rounded-br-md whitespace-pre-wrap shadow-card' : 'bg-[#f5f8f5] border border-[#e5ece6] text-[#34493c] rounded-tl-md'
              }`}
            >
              {m.role === 'user' ? m.content : <Markdown>{m.content}</Markdown>}
            </div>
          </div>
        ))}
        {steps.length > 0 && <StepFlow steps={steps} />}
      </div>

      {messages.length <= 1 && (
        <div className="px-5 pb-4 grid grid-cols-2 gap-2">
          {QUICK.map((q, index) => (
            <button key={q} disabled={busy || !backendReady} onClick={() => void send(q)} title={q} className="text-left text-[11px] p-3 rounded-xl bg-white hover:bg-brand-soft text-[#63756a] border border-[var(--line)] disabled:opacity-40">
              <span className="flex items-center justify-between text-xs font-medium text-brand-ink">{['家庭周末', '好友聚会', '菜单与比价', '同行人确认'][index]}<ArrowUpRight size={12} /></span><span className="block mt-1 truncate">{q}</span>
            </button>
          ))}
        </div>
      )}

      <div className="p-4 pt-2">
        <div className="rounded-2xl border border-[#dce7df] bg-[#f7faf7] p-3 shadow-card focus-within:border-[#6a9d81] transition-colors">
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={onPickImage} />
          <textarea value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }} rows={2} aria-label="任务输入" placeholder="说说你的安排，或粘贴网页链接…" className="w-full bg-transparent outline-none text-[13px] leading-6 resize-none max-h-36 text-brand-ink placeholder:text-[#8a978e]" />
          <div className="flex items-center justify-between mt-2">
            <div className="flex items-center gap-1"><button onClick={() => fileRef.current?.click()} disabled={busy} title="上传图片（可先输入要求）" aria-label="上传图片" className="plango-icon-button disabled:opacity-40"><ImagePlus size={17} /></button>
              {supportsSpeech && <button onClick={toggleMic} disabled={busy} title={listening ? '停止语音' : '语音输入'} className={`plango-icon-button disabled:opacity-40 ${listening ? 'bg-red-50 text-red-600 animate-pulse' : ''}`}>{listening ? <MicOff size={17} /> : <Mic size={17} />}</button>}
              <span className="text-[10px] text-[var(--muted)] ml-1">Shift + Enter 换行</span>
            </div>
            <button onClick={submit} disabled={busy || !backendReady || !text.trim()} aria-label="发送消息" title="发送消息" className="h-9 w-9 flex items-center justify-center rounded-xl bg-brand-strong text-white disabled:opacity-40 hover:bg-brand-ink transition-colors"><Send size={16} /></button>
          </div>
        </div>
        <p className="text-center text-[10px] text-[var(--muted)] mt-2.5">重要操作需你确认，任务可随时停止</p>
      </div>
    </div>
  )
}
