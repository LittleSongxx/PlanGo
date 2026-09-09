import { useEffect, useState } from 'react'
import { MapPin } from 'lucide-react'

export function PoiImage({ src, name, className = '' }: { src?: string; name: string; className?: string }): JSX.Element {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => { setFailed(false); setLoaded(false) }, [src])
  return <div className={`relative overflow-hidden rounded-xl bg-[var(--surface-soft)] ${className}`}>
    {src && !failed && !loaded && <div className="absolute inset-0 flex items-center justify-center text-neutral-400"><MapPin size={22} strokeWidth={1.3} /></div>}
    {src && !failed ? <img src={src} alt={name} loading="lazy" referrerPolicy="no-referrer" className={`h-full w-full object-cover transition-opacity ${loaded ? 'opacity-100' : 'opacity-0'}`} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
      : <div className="h-full w-full flex flex-col items-center justify-center gap-1.5 text-neutral-400" role="img" aria-label={`${name}的图片暂不可用`}><MapPin size={20} strokeWidth={1.4} /><span className="text-[9px]">图片暂不可用</span></div>}
  </div>
}
