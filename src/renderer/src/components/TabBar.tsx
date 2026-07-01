import { useStore } from '../store'
import { DEFAULT_HOME } from '../lib/favorites'
import { X, Plus } from 'lucide-react'

export function TabBar(): JSX.Element {
  const tabs = useStore((s) => s.tabs)
  const activeTabId = useStore((s) => s.activeTabId)
  const setActiveTab = useStore((s) => s.setActiveTab)
  const closeTab = useStore((s) => s.closeTab)
  const addTab = useStore((s) => s.addTab)

  return (
    <div className="h-9 shrink-0 flex items-center gap-1 px-2 bg-neutral-100 border-b border-neutral-200 overflow-x-auto">
      {tabs.map((t) => (
        <div
          key={t.id}
          onClick={() => setActiveTab(t.id)}
          className={`group flex items-center gap-1.5 pl-3 pr-1.5 h-7 rounded-md cursor-pointer max-w-[180px] shrink-0 ${
            activeTabId === t.id ? 'bg-white shadow-sm' : 'hover:bg-neutral-200/60'
          }`}
        >
          <span className="text-xs truncate">{t.title || '新标签'}</span>
          <button
            onClick={(e) => {
              e.stopPropagation()
              closeTab(t.id)
            }}
            className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-neutral-300"
          >
            <X size={12} />
          </button>
        </div>
      ))}
      <button onClick={() => addTab(DEFAULT_HOME)} className="p-1 rounded hover:bg-neutral-200 shrink-0" title="新标签">
        <Plus size={15} />
      </button>
    </div>
  )
}
