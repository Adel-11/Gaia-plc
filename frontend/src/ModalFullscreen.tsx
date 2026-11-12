import { useLayoutEffect, useRef, useState } from 'react'
import './modal-fullscreen.css'
import PanelIcon from './PanelIcon'

export default function ModalFullscreen() {
  const button = useRef<HTMLButtonElement>(null)
  const [expanded, setExpanded] = useState(false)
  useLayoutEffect(() => {
    const dialog = button.current?.closest('dialog, [aria-modal="true"]')
    if (!dialog || !expanded) return
    dialog.setAttribute('data-modal-fullscreen', 'true')
    return () => { dialog.removeAttribute('data-modal-fullscreen') }
  }, [expanded])
  const label = expanded ? 'Quitter le plein écran' : 'Plein écran'
  return <div className="modal-view-controls">
    <button ref={button} type="button" aria-label={label} title={label} aria-pressed={expanded} onClick={() => setExpanded(value => !value)}>
      <PanelIcon name={expanded ? 'shrink' : 'expand'}/>
    </button>
  </div>
}
