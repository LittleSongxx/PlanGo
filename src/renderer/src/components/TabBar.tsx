import { useStore } from '../store'
import { DEFAULT_HOME } from '../lib/favorites'
import { X, Plus, Globe2 } from 'lucide-react'

export function TabBar(): JSX.Element {
  const tabs = useStore((s) => s.tabs)
  const activeTabId = useStore((s) => s.activeTabId)
  const setActiveTab = useStore((s) => s.setActiveTab)
  const closeTab = useStore((s) => s.closeTab)
  const addTab = useStore((s) => s.addTab)

  return <div className="h-11 shrink-0 flex items-center gap-1.5 px-4 bg-[var(--surface-soft)] border-b border-[var(--line)] overflow-x-auto" aria-label="浏览器标签">
    {tabs.map(tab => <div key={tab.id} className={`group flex items-center h-8 rounded-lg border pr-1 max-w-[210px] shrink-0 ${activeTabId === tab.id ? 'bg-white border-[var(--line)] shadow-card' : 'border-transparent hover:bg-white/70'}`}>
      <button onClick={() => setActiveTab(tab.id)} aria-current={activeTabId === tab.id ? 'page' : undefined} aria-label={`切换标签：${tab.title || '新标签'}`} className="flex items-center gap-2 min-w-0 pl-2.5 pr-2 h-full text-xs"><Globe2 size={12} className="text-brand-strong shrink-0" /><span className="truncate">{tab.title || '新标签'}</span></button>
      <button onClick={() => closeTab(tab.id)} aria-label={`关闭标签：${tab.title || '新标签'}`} className="flex h-6 w-6 items-center justify-center rounded-md text-neutral-400 hover:bg-brand-soft hover:text-brand-ink shrink-0"><X size={12} /></button>
    </div>)}
    <button onClick={() => addTab(DEFAULT_HOME)} className="plango-icon-button" title="新标签" aria-label="新标签"><Plus size={16} /></button>
    {!tabs.length && <span className="text-[11px] text-[var(--muted)]">还没有打开的网页</span>}
  </div>
}
