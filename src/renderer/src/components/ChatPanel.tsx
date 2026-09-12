import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { deliveryMessage, publicStatus, userMessage } from '@shared/userMessages'
import { phaseLabel, projectEvents, row } from '../lib/harnessProjection'
import { isImeComposing } from '@shared/ime'
import { StepFlow } from './StepFlow'
import { Markdown } from './Markdown'
import { Send, Bell, X, ImagePlus, Plus, History, Mic, MicOff, Sparkles, ArrowUpRight } from 'lucide-react'

const QUICK = [
  '这周六下午带老婆孩子出去玩4小时，孩子5岁，老婆减脂，预算人均120',
  '周末约4个朋友聚会，找个能玩能吃的安排，列出费用估算和待核验事项',
  '读取当前网页的门店和优惠，列出售价、适用人数及缺失规则，不下单',
  '整理这份行程草案，保留来源和待核验事项，方便我分享给同行人'
]
const IMAGE_READ = '请读取这张图片中的文字、地点和价格，保留图片来源，并注明无法实时核验的信息。'

export function ChatPanel(): JSX.Element {
  const messages = useStore((s) => s.messages)
  const steps = projectEvents(useStore((s) => s.events))
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
  const activeSessionId = useStore(s => s.activeSessionId)
  const draft = useStore(s => s.drafts[s.activeSessionId])
  const text = draft?.text || ''
  const setDraft = useStore(s => s.setDraft)
  const setText = (value: string) => setDraft({ text: value })
  const pending = useStore(s => s.pendingDelivery)
  const deliveryBusy = useStore(s => s.deliveryBusy)
  const recoverDelivery = useStore(s => s.recoverDelivery)
  const openPendingDelivery = useStore(s => s.openPendingDelivery)
  const storageError = useStore(s => s.storageError)
  const persistComposer = useStore(s => s.persistComposer)
  const composerReady = useStore(s => s.composerReady)
  const hydrateComposer = useStore(s => s.hydrateComposer)
  const discardPendingDelivery = useStore(s => s.discardPendingDelivery)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [inputHint, setInputHint] = useState('')
  const [listening, setListening] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const fileRef = useRef<HTMLInputElement>(null)
  const recRef = useRef<any>(null)
  const supportsSpeech = typeof window !== 'undefined' && ((window as any).webkitSpeechRecognition || (window as any).SpeechRecognition)

  // 选图只附到当前会话草稿；发送时若文本仍空，再用默认读图提示。
  const onPickImage = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f || busy || pending || !composerReady) return
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(f.type) || f.size > 8_000_000) {
      useStore.setState({ backendError: '请上传 8 MB 以内的 PNG、JPEG 或 WebP 图片。' })
      return
    }
    const sessionId = activeSessionId
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result || '')
      if (!dataUrl.startsWith('data:image')) return
      setDraft({ image: dataUrl }, sessionId)
    }
    reader.onerror = () => useStore.setState({ backendError: '图片读取失败，请重新选择文件。' })
    reader.readAsDataURL(f)
  }

  const lastMessage = messages.at(-1)
  const conversationChange = `${messages.length}:${lastMessage?.id || ''}:${lastMessage?.content || ''}`
  useEffect(() => { followLatest.current = true }, [activeSessionId])
  useEffect(() => {
    if (followLatest.current) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'auto' })
  }, [activeSessionId, conversationChange, steps.length, pending?.status])

  useEffect(() => {
    void window.plango.desktopReady().then(info => { if (info?.inputHint) setInputHint(info.inputHint) }).catch(() => {})
    return () => {
      try {
        recRef.current?.stop?.()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const submit = () => {
    const t = text.trim() || (draft?.image ? IMAGE_READ : '')
    if (!t || busy || pending || !composerReady) return
    void send(t, draft?.image)
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
      setDraft({ text: (finalText + interim).trim() }, activeSessionId)
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

  useEffect(() => {
    try { recRef.current?.stop?.() } catch { /* browser speech support varies */ }
    setListening(false)
  }, [activeSessionId])

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
      <div className="flex items-center justify-between gap-2 px-5 py-2.5 text-[11px] bg-[var(--surface-soft)] border-b border-[var(--line)]">
        <span role="status" className={backendReady ? 'text-neutral-600' : 'text-amber-700'}>{backendReady ? phaseLabel(run) : '未连接服务'}</span>
        {run && !run.outcome && <button onClick={() => void cancelRun()} className="text-neutral-500 hover:text-red-600">停止任务</button>}
        {!run && <span className="text-[var(--muted)]">从你的需求开始</span>}
      </div>

      {backendError && <div role="alert" className="mx-4 mt-3 rounded-xl bg-amber-50 border border-amber-200 p-2 text-xs text-amber-800">
        <div className="break-words">{userMessage(backendError)}</div>
        <button className="mt-1 underline" onClick={() => void refresh()}>重新连接并恢复任务</button>
      </div>}
      {run && (!!run.state.browser_wait || run.phase === 'WAITING_BROWSER') && <div className="mx-4 mt-3 rounded-xl bg-brand-soft border border-brand/30 p-2 text-xs">
        <div>{publicStatus(String(row(run.state.browser_wait).message || '请在浏览器中完成登录或接管操作，再继续。'), 'action')}</div>
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
              <button onClick={() => void send(latestProactive.text)} disabled={busy || !!pending || !composerReady} className="text-[11px] px-2 py-0.5 rounded bg-brand text-brand-ink font-medium mr-2 disabled:opacity-40">
                让PlanGo安排
              </button>
            </div>
          </div>
          <button onClick={() => setDismissed(latestProactive.id)} className="p-0.5 text-neutral-400 hover:text-neutral-700">
            <X size={13} />
          </button>
        </div>
      )}

      <div ref={scrollRef} onScroll={event => {
        const element = event.currentTarget
        followLatest.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80
      }} className="flex-1 overflow-y-auto px-5 py-6 space-y-5" aria-label="对话记录">
        {messages.map((m, i) => (
          <div key={m.id || i} data-chat-role={m.role} className={`flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'} animate-in`}>
            <span className="mb-1.5 px-1 text-[10px] font-medium text-[var(--muted)]">{m.role === 'user' ? '你' : 'PlanGo'}</span>
            <div
              className={`max-w-[94%] px-4 py-3.5 rounded-2xl text-[13px] leading-[1.75] ${
                m.role === 'user' ? 'bg-brand text-brand-ink rounded-br-md whitespace-pre-wrap shadow-card' : 'bg-[var(--surface-soft)] border border-[var(--line)] text-brand-ink rounded-tl-md'
              }`}
            >
              {m.role === 'user' ? m.content : <Markdown>{publicStatus(m.content)}</Markdown>}
            </div>
          </div>
        ))}
        {steps.length > 0 && <StepFlow steps={steps} />}
      </div>

      {messages.length <= 1 && (
        <div className="px-5 pb-4 grid grid-cols-2 gap-2">
          {QUICK.map((q, index) => (
            <button key={q} disabled={busy || !!pending || !composerReady} onClick={() => void send(q)} title={q} className="text-left text-[11px] p-3 rounded-xl bg-white hover:bg-brand-soft text-neutral-600 border border-[var(--line)] disabled:opacity-40">
              <span className="flex items-center justify-between text-xs font-medium text-brand-ink">{['家庭周末', '好友聚会', '门店与优惠', '分享草案'][index]}<ArrowUpRight size={12} /></span><span className="block mt-1 truncate">{q}</span>
            </button>
          ))}
        </div>
      )}

      <div className="p-4 pt-2">
        {pending && <div role="status" aria-label="消息发送状态" className="mb-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <div className="font-semibold">{deliveryBusy ? '正在核对原请求…' : pending.status === 'not_sent' ? '未送达 · 输入已保留' : pending.status === 'accepted' || pending.status === 'delivered' ? '已接收 · 待取回任务' : '送达待核实 · 原请求已保留'}</div>
          <p className="mt-1 whitespace-pre-wrap line-clamp-3">{pending.request.text}</p>
          {pending.request.image && <p className="mt-1">含 1 张图片</p>}
          {pending.request.selectedPoi && <p className="mt-1">已选门店：{pending.request.selectedPoi.name}</p>}
          {pending.error && <p className="mt-1 break-words text-[11px]">{deliveryMessage(pending.status, pending.error)}</p>}
          <p className="mt-1 text-[11px]">{pending.sessionId === activeSessionId ? '确认送达后会取回原任务；下方新编辑的草稿会保留。' : '此消息属于另一个会话，可回到原会话查看。'}</p>
          <div className="mt-2 flex flex-wrap gap-3">
            <button disabled={deliveryBusy} className="underline disabled:opacity-40" onClick={() => void recoverDelivery(pending.status === 'not_sent')}>{pending.status === 'not_sent' ? '继续发送原请求' : '核对送达并取回'}</button>
            {pending.status === 'not_sent' && <button disabled={deliveryBusy || !composerReady} className="underline disabled:opacity-40" onClick={() => void discardPendingDelivery()}>取消本次发送，保留草稿</button>}
            {pending.status === 'unconfirmed' && <button disabled={deliveryBusy} className="underline disabled:opacity-40" onClick={() => void recoverDelivery(true)}>按原请求重试</button>}
            {pending.sessionId !== activeSessionId && <button className="underline" onClick={openPendingDelivery}>回到原会话</button>}
          </div>
        </div>}
        {storageError && <div role="alert" className="mb-3 rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-800">{userMessage(storageError, 'storage')}<button className="ml-2 underline" onClick={() => void (composerReady ? persistComposer() : hydrateComposer())}>重试保存或恢复</button></div>}
        {draft?.image && <div className="mb-2 flex items-center gap-2 text-xs text-[var(--muted)]"><img src={draft.image} alt="待发送图片" className="h-12 w-12 rounded-lg object-cover" /><span>图片随本次要求一起发送</span><button aria-label="移除草稿图片" className="plango-icon-button ml-auto" onClick={() => setDraft({ image: undefined })}><X size={14} /></button></div>}
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface-soft)] p-3 shadow-card focus-within:border-brand-strong transition-colors">
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={onPickImage} />
          <textarea disabled={!composerReady} value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !isImeComposing(event)) { event.preventDefault(); submit() } }} rows={2} aria-label="任务输入" placeholder="说说你的安排，或粘贴网页链接…" className="w-full bg-transparent outline-none text-[13px] leading-6 resize-none max-h-36 text-brand-ink placeholder:text-neutral-400" />
          {inputHint && <p className="mt-1 text-[10px] leading-4 text-[var(--muted)]">{inputHint}</p>}
          <div className="flex items-center justify-between mt-2">
            <div className="flex items-center gap-1"><button onClick={() => fileRef.current?.click()} disabled={busy || !!pending || !composerReady} title="上传图片（可先输入要求）" aria-label="上传图片" className="plango-icon-button disabled:opacity-40"><ImagePlus size={17} /></button>
              {supportsSpeech && <button onClick={toggleMic} disabled={busy} title={listening ? '停止语音' : '语音输入'} className={`plango-icon-button disabled:opacity-40 ${listening ? 'bg-red-50 text-red-600 animate-pulse' : ''}`}>{listening ? <MicOff size={17} /> : <Mic size={17} />}</button>}
              <span className="text-[10px] text-[var(--muted)] ml-1">Shift + Enter 换行 · 可粘贴中文</span>
            </div>
            <button onClick={submit} disabled={busy || !!pending || !composerReady || (!text.trim() && !draft?.image)} aria-label="发送消息" title="发送消息" className="h-9 w-9 flex items-center justify-center rounded-xl bg-brand text-brand-ink disabled:opacity-40 hover:bg-brand-hover transition-colors"><Send size={16} /></button>
          </div>
        </div>
        <p className="text-center text-[10px] text-[var(--muted)] mt-2.5">重要操作需你确认，任务可随时停止</p>
      </div>
    </div>
  )
}
