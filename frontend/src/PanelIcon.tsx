const paths = {
  expand: 'M9 4H4v5m11-5h5v5m0 6v5h-5M4 15v5h5',
  shrink: 'M4 9h5V4m11 5h-5V4m0 16v-5h5M9 20v-5H4',
  down: 'm6 9 6 6 6-6',
  up: 'm6 15 6-6 6 6',
  reset: 'M4 11a8 8 0 1 1 2 6m-2-6V5m0 6h6',
  rename: 'm15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15v5Z',
  delete: 'M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7',
}

export default function PanelIcon({ name }: { name: keyof typeof paths }) {
  return <svg className="panel-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={paths[name]}/>
  </svg>
}
