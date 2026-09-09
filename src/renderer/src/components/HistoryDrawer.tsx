import { useStore } from '../store'
import { X, Plus, MessageSquare, EyeOff, MapPin, Clock, ArrowUpRight } from 'lucide-react'
import { DialogShell } from './DialogShell'

export function HistoryDrawer(): JSX.Element | null {
  const open = useStore(state => state.historyOpen)
  const setOpen = useStore(state => state.setHistoryOpen)
  const sessions = useStore(state => state.sessions)
  const activeId = useStore(state => state.activeSessionId)
  const restore = useStore(state => state.restoreSession)
  const hide = useStore(state => state.deleteSession)
  const newSession = useStore(state => state.newSession)
  if (!open) return null
  const format = (time: number) => new Date(time).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  return <DialogShell label="历史会话" onClose={() => setOpen(false)} className="plango-dialog-history w-[400px]">
    <div className="plango-panel-header justify-between gap-3"><div><div className="plango-kicker">PICK UP WHERE YOU LEFT OFF</div><h2 className="mt-1 text-lg font-semibold">历史会话</h2></div><button onClick={() => setOpen(false)} className="plango-icon-button" aria-label="关闭历史会话"><X size={18} /></button></div>
    <div className="p-5 pb-3"><button onClick={newSession} className="plango-primary w-full"><Plus size={16} />开个新会话</button><p className="text-xs text-[var(--muted)] mt-3 leading-5">恢复已经保存的对话，接着查看资料与安排。</p></div>
    <div className="flex-1 overflow-y-auto px-5 pb-5 space-y-3">
      {!sessions.length ? <div className="flex flex-col items-center justify-center py-20 text-center"><span className="flex w-16 h-16 items-center justify-center rounded-2xl bg-brand-soft text-brand-strong"><MessageSquare size={25} strokeWidth={1.5} /></span><h3 className="mt-5 font-semibold text-sm">还没有保存的会话</h3><p className="mt-2 text-xs text-[var(--muted)] leading-6 max-w-[230px]">开始一次对话后，可以在这里找回任务与已有结果。</p></div>
        : sessions.map(session => <article key={session.id} className={`relative group rounded-2xl border transition-colors ${session.id === activeId ? 'bg-brand-soft border-brand' : 'bg-white border-[var(--line)] hover:border-brand'}`}>
          <button data-history-entry aria-label={`打开会话：${session.title}`} onClick={() => restore(session.id)} className="block w-full text-left p-4 pr-11">
            <div className="flex items-center gap-2"><MessageSquare size={14} className="text-brand-strong shrink-0" /><span className="text-[13px] font-medium truncate">{session.title}</span></div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-[10px] text-[var(--muted)]"><span className="flex items-center gap-1"><MapPin size={10} />{session.city || '地区未记录'}</span><span className="flex items-center gap-1"><Clock size={10} />{format(session.updatedAt)}</span></div>
            <span className="flex items-center gap-1 mt-3 text-[10px] text-brand-strong">继续查看<ArrowUpRight size={11} /></span>
          </button>
          <button onClick={() => hide(session.id)} className="absolute top-3 right-3 plango-icon-button opacity-40 group-hover:opacity-100 focus:opacity-100 hover:text-red-600" title="从历史列表隐藏（后台任务仍保留）" aria-label="从历史列表隐藏"><EyeOff size={14} /></button>
        </article>)}
    </div>
  </DialogShell>
}
