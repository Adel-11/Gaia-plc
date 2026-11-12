const paths = {
  expand: 'M9 4H4v5m11-5h5v5m0 6v5h-5M4 15v5h5',
  shrink: 'M4 9h5V4m11 5h-5V4m0 16v-5h5M9 20v-5H4',
  down: 'm6 9 6 6 6-6',
  up: 'm6 15 6-6 6 6',
  reset: 'M4 11a8 8 0 1 1 2 6m-2-6V5m0 6h6',
}

export default function PanelIcon({ name }: { name: keyof typeof paths }) {
  return <svg className="panel-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={paths[name]}/>
  </svg>
}
