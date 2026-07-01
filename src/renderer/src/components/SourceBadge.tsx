import type { SourceTag } from '@shared/types'

const MAP: Record<string, { label: string; cls: string }> = {
  real: { label: '真实', cls: 'bg-green-100 text-green-700' },
  dataset: { label: '数据集', cls: 'bg-blue-100 text-blue-700' },
  simulated: { label: '模拟', cls: 'bg-amber-100 text-amber-700' },
  cache: { label: '缓存', cls: 'bg-neutral-100 text-neutral-600' },
  fallback: { label: '兜底', cls: 'bg-red-100 text-red-600' }
}

export function SourceBadge({ source }: { source: SourceTag }): JSX.Element {
  const m = MAP[source] || MAP.dataset
  return <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0 ${m.cls}`}>{m.label}</span>
}
