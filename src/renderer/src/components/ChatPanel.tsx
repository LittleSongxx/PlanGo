import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { StepFlow } from './StepFlow'
import { Markdown } from './Markdown'
import { Send, Bell, X, ImagePlus, Plus, History, Mic, MicOff } from 'lucide-react'

const QUICK = [
  '这周六下午带老婆孩子出去玩4小时，孩子5岁，老婆减脂，预算人均120',
  '周末约4个朋友聚会，2男2女，找个能玩能吃的，热门店帮我取号',
  '帮我给这份行程比个价，再按减脂给餐厅点菜',
  '把方案发家庭群确认一下'
]

export function ChatPanel(): JSX.Element {
  const messages = useStore((s) => s.messages)
  const steps = useStore((s) => s.steps)
  const busy = useStore((s) => s.busy)
  const send = useStore((s) => s.send)
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
    const reader = new FileReader()
    reader.onload = async () => {
      const dataUrl = String(reader.result || '')
      if (!dataUrl.startsWith('data:image')) return
      await window.xiaonian.guideSetImage(dataUrl)
      void send('我上传了一张攻略截图，请用 import_guide 读一下，按里面的城市/人群/菜品在我这边规划 3 套方案')
    }
    reader.readAsDataURL(f)
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
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
    <div className="h-full flex flex-col bg-white">
      <div className="h-9 shrink-0 flex items-center px-3 border-b border-neutral-100">
        <span className="text-sm font-semibold">小悠 · 对话</span>
        {busy && <span className="ml-2 text-xs text-brand-ink/70">思考中…</span>}
        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => setHistoryOpen(true)} title="历史会话" className="p-1.5 rounded-lg text-neutral-500 hover:bg-neutral-100">
            <History size={15} />
          </button>
          <button onClick={newSession} title="新建对话" className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-brand-ink bg-brand/10 hover:bg-brand/20">
            <Plus size={13} /> 新对话
          </button>
        </div>
      </div>

      {showProactive && (
        <div className="mx-3 mt-2 p-2.5 rounded-lg bg-brand-soft border border-brand/40 flex items-start gap-2 animate-in">
          <Bell size={15} className="text-brand-ink mt-0.5 shrink-0" />
          <div className="flex-1 text-xs text-neutral-700">
            <div className="font-medium text-brand-ink mb-0.5">小悠主动关心</div>
            {latestProactive.text}
            <div className="mt-1.5">
              <button onClick={() => void send(latestProactive.text)} className="text-[11px] px-2 py-0.5 rounded bg-brand text-brand-ink font-medium mr-2">
                让小悠安排
              </button>
            </div>
          </div>
          <button onClick={() => setDismissed(latestProactive.id)} className="p-0.5 text-neutral-400 hover:text-neutral-700">
            <X size={13} />
          </button>
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'} animate-in`}>
            <div
              className={`max-w-[85%] px-3 py-2 rounded-2xl text-sm leading-relaxed ${
                m.role === 'user' ? 'bg-brand text-brand-ink rounded-br-sm whitespace-pre-wrap' : 'bg-neutral-100 text-neutral-800 rounded-bl-sm'
              }`}
            >
              {m.role === 'user' ? m.content : <Markdown>{m.content}</Markdown>}
            </div>
          </div>
        ))}
        {steps.length > 0 && <StepFlow steps={steps} />}
      </div>

      {messages.length <= 1 && (
        <div className="px-3 pb-2 flex flex-wrap gap-1.5">
          {QUICK.map((q) => (
            <button key={q} onClick={() => void send(q)} className="text-[11px] px-2 py-1 rounded-full bg-neutral-100 hover:bg-brand/20 text-neutral-600 border border-neutral-200">
              {q.length > 22 ? q.slice(0, 22) + '…' : q}
            </button>
          ))}
        </div>
      )}

      <div className="p-3 border-t border-neutral-100">
        <div className="flex items-end gap-2 bg-neutral-100 rounded-xl px-3 py-2">
          <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={onPickImage} />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            title="导入攻略截图（小红书/大众点评），小悠用视觉识别→规划"
            className="p-1.5 rounded-lg text-neutral-500 hover:bg-neutral-200 disabled:opacity-40"
          >
            <ImagePlus size={16} />
          </button>
          {supportsSpeech && (
            <button
              onClick={toggleMic}
              disabled={busy}
              title={listening ? '停止语音' : '语音输入'}
              className={`p-1.5 rounded-lg disabled:opacity-40 ${listening ? 'bg-red-500 text-white animate-pulse' : 'text-neutral-500 hover:bg-neutral-200'}`}
            >
              {listening ? <MicOff size={16} /> : <Mic size={16} />}
            </button>
          )}
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
            }}
            rows={1}
            placeholder="跟小悠说一句…也可贴小红书/点评攻略链接，或点左侧📷传攻略截图"
            className="flex-1 bg-transparent outline-none text-sm resize-none max-h-24"
          />
          <button onClick={submit} disabled={busy || !text.trim()} className="p-1.5 rounded-lg bg-brand text-brand-ink disabled:opacity-40">
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
