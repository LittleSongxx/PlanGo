import { useEffect, useRef } from 'react'

// Native dialog supplies focus trapping and Escape; application state remains authoritative.
export function DialogShell({ label, onClose, children, className = '' }: { label: string; onClose: () => void; children: React.ReactNode; className?: string }): JSX.Element {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current!
    const previous = document.activeElement
    dialog.showModal()
    return () => { dialog.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus() }
  }, [])
  return <dialog ref={ref} aria-label={label} className={`plango-dialog ${className}`} onCancel={event => { event.preventDefault(); onClose() }} onClick={event => {
    const bounds = event.currentTarget.getBoundingClientRect()
    if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) onClose()
  }}>{children}</dialog>
}
