import { useLayoutEffect, useRef, useState } from 'react'
import './modal-fullscreen.css'

export default function ModalFullscreen() {
  const button = useRef<HTMLButtonElement>(null)
  const [expanded, setExpanded] = useState(false)
  useLayoutEffect(() => {
    const dialog = button.current?.closest('dialog, [aria-modal="true"]')
    if (!dialog || !expanded) return
    dialog.setAttribute('data-modal-fullscreen', 'true')
    return () => { dialog.removeAttribute('data-modal-fullscreen') }
  }, [expanded])
  return <div className="modal-view-controls">
    <button ref={button} type="button" aria-pressed={expanded} onClick={() => setExpanded(value => !value)}>
      {expanded ? 'Quitter le plein écran' : 'Plein écran'}
    </button>
  </div>
}
