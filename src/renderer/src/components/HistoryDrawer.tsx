import { useStore } from '../store'
import { X, Plus, MessageSquare, EyeOff, MapPin, Clock } from 'lucide-react'

// 左侧滑入的历史抽屉（仿 weplan openDrawer）：会话列表，点开可恢复消息+成果卡。
export function HistoryDrawer(): JSX.Element | null {
  const open = useStore((s) => s.historyOpen)
  const setOpen = useStore((s) => s.setHistoryOpen)
  const sessions = useStore((s) => s.sessions)
  const activeId = useStore((s) => s.activeSessionId)
  const restore = useStore((s) => s.restoreSession)
  const del = useStore((s) => s.deleteSession)
  const newSession = useStore((s) => s.newSession)

  const fmt = (ts: number): string => {
    const d = new Date(ts)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }

  return (
    <div
      className={`fixed inset-0 z-50 flex transition-colors duration-200 ${open ? 'bg-black/40 pointer-events-auto' : 'bg-black/0 pointer-events-none'}`}
      onClick={() => setOpen(false)}
    >
      <div
        className={`w-[340px] max-w-[86vw] h-full bg-white shadow-2xl flex flex-col transition-transform duration-200 ${open ? 'translate-x-0' : '-translate-x-full'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="h-12 shrink-0 flex items-center px-4 border-b border-neutral-200">
          <MessageSquare size={16} className="text-brand-ink" />
          <span className="font-semibold text-sm ml-2">历史会话</span>
          <button onClick={() => setOpen(false)} className="ml-auto p-1.5 rounded hover:bg-neutral-100">
            <X size={16} />
          </button>
        </div>

        <button
          onClick={newSession}
          className="mx-3 mt-3 mb-1 flex items-center justify-center gap-1.5 py-2 rounded-xl bg-brand text-brand-ink font-medium text-sm hover:brightness-95"
        >
          <Plus size={15} /> 开个新会话
        </button>

        <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
          {sessions.length === 0 ? (
            <div className="text-center text-neutral-400 text-xs mt-10">还没有历史，跟小悠聊一句就会自动存这儿。</div>
          ) : (
            sessions.map((s) => (
              <div
                key={s.id}
                onClick={() => restore(s.id)}
                className={`group relative rounded-xl border px-3 py-2 cursor-pointer transition-colors ${
                  s.id === activeId ? 'border-brand bg-brand/10' : 'border-neutral-200 hover:border-neutral-300'
                }`}
              >
                <div className="text-sm font-medium truncate pr-6">{s.title}</div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-neutral-400">
                  <span className="flex items-center gap-0.5">
                    <MapPin size={10} /> {s.city}
                  </span>
                  <span className="flex items-center gap-0.5">
                    <Clock size={10} /> {fmt(s.updatedAt)}
                  </span>
                  {s.cards.length > 0 && <span className="text-brand-ink">{s.cards.length} 张成果卡</span>}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    del(s.id)
                  }}
                  className="absolute top-2 right-2 p-1 rounded opacity-0 group-hover:opacity-100 text-neutral-400 hover:text-red-500 hover:bg-neutral-100"
                  title="从历史列表隐藏（后台任务仍保留）"
                  aria-label="从历史列表隐藏"
                >
                  <EyeOff size={13} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
