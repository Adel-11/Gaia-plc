import { useEffect, useMemo, useRef, useState } from 'react'
import { EditorView, highlightActiveLine, lineNumbers } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { StreamLanguage, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { keymap } from '@codemirror/view'
import { openSearchPanel, searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import './object-view.css'
import ObjectCanvas from './ObjectCanvas'
import FbMappingEditor, { type FbMapping, type VariableOption } from './FbMappingEditor'

type Member = { name: string; iec_type: string; default_value?: string | number | boolean | null; section?: string; unit?: string; comment?: string; exchange?: string; position?: number }
type Dut = { name: string; role?: string; members: Member[]; st?: string }
type LinkMap = { tcp_points?: unknown[]; rtu_slaves?: unknown[]; rtu_channels?: unknown[]; io_channels?: unknown[]; pa_values?: unknown[]; exchange_calls?: unknown[] }
type ObjectData = { id: string; name: string; kind: string; type_id?: string; revision: number; duts: Dut[]; fb?: { name?: string; declaration?: string; body?: string; generated_body?: string; custom_code?: string; processing_code?: string; mappings?: FbMapping[]; preserved_sections?: Record<string, string>; variable_options?: VariableOption[]; flow?: any }; links?: LinkMap }
type Preview = { token: string; errors?: unknown[]; warnings?: unknown[]; changes?: string[] }
type CatalogOptions = { sections: string[]; prefixes: string[]; revision?: number }
const BASE_SECTIONS = ['INPUT (field to plc)', 'OUTPUT (plc to field)', 'STATE', 'PARAMETERS', 'INTERNAL']
const BASE_PREFIXES = ['INPUT', 'OUTPUT', 'STATE', 'CMD', 'SPARE', 'COM', 'HMI', 'SCADA']
type Props = { kind: 'type' | 'instance'; objectId: string; csrf: string; projectRevision?: number; onSaved: () => void; onOpenType?: (typeId: string) => void; onDirtyChange?: (dirty: boolean) => void }

async function request<T>(path: string, options: RequestInit = {}, csrf = ''): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v2${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const body = await response.json(); message = body.detail || body.message || body.error || message } catch { /* ignore non-JSON errors */ }; throw new Error(message) }
  return response.json() as Promise<T>
}

const stLanguage = StreamLanguage.define({
  startState: () => ({ inComment: false }),
  token(stream, state) {
    if (state.inComment) { while (!stream.eol()) { if (stream.match('*)')) { state.inComment = false; break }; stream.next() }; return 'comment' }
    if (stream.eatSpace()) return null
    if (stream.match('(*')) { state.inComment = true; while (!stream.eol()) { if (stream.match('*)')) { state.inComment = false; break }; stream.next() }; return 'comment' }
    if (stream.match(/\/\//)) { stream.skipToEnd(); return 'comment' }
    if (stream.match(/'([^']|'')*'?/)) return 'string'
    if (stream.match(/\b(TRUE|FALSE|AND|OR|XOR|NOT|MOD|IF|THEN|ELSE|ELSIF|END_IF|CASE|OF|END_CASE|FOR|TO|BY|DO|END_FOR|WHILE|REPEAT|UNTIL|END_WHILE|END_REPEAT|RETURN|EXIT|VAR|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP|END_VAR|FUNCTION_BLOCK|END_FUNCTION_BLOCK|FUNCTION|END_FUNCTION|PROGRAM|END_PROGRAM|TYPE|END_TYPE|STRUCT|END_STRUCT|ARRAY|OF|AT|CONSTANT|RETAIN|PERSISTENT)\b/i)) return 'keyword'
    if (stream.match(/\b(BOOL|BYTE|WORD|DWORD|LWORD|SINT|INT|DINT|LINT|USINT|UINT|UDINT|ULINT|REAL|LREAL|TIME|DATE|TOD|DT|STRING|WSTRING)\b/i)) return 'typeName'
    if (stream.match(/\b\d+(\.\d+)?([eE][+-]?\d+)?\b/)) return 'number'
    if (stream.match(/[A-Za-z_][\w]*/)) return 'variableName'
    stream.next(); return null
  },
})

export function StEditor({ value, onChange, readOnly = false, label }: { value: string; onChange?: (value: string) => void; readOnly?: boolean; label: string }) {
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange); onChangeRef.current = onChange
  const applyingValue = useRef(false)
  useEffect(() => {
    if (!host.current) return
    const state = EditorState.create({ doc: value, extensions: [lineNumbers(), highlightActiveLine(), highlightSelectionMatches(), history(), stLanguage, syntaxHighlighting(defaultHighlightStyle), keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]), EditorView.editable.of(!readOnly), EditorState.readOnly.of(readOnly), EditorView.contentAttributes.of({'aria-label': label}), EditorView.updateListener.of(update => { if (update.docChanged && !applyingValue.current) onChangeRef.current?.(update.state.doc.toString()) }), EditorView.theme({ '&': { height: '100%', fontSize: '16px' }, '.cm-scroller': { overflow: 'auto', fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace' }, '.cm-gutters': { backgroundColor: '#f6f8fa', borderRight: '1px solid #e1e5eb', color: '#7a8493' } })] })
    editor.current = new EditorView({ state, parent: host.current })
    return () => { editor.current?.destroy(); editor.current = null }
  }, [readOnly])
  useEffect(() => {
    const view = editor.current
    if (view && view.state.doc.toString() !== value) {
      applyingValue.current = true
      try { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } }) }
      finally { applyingValue.current = false }
    }
  }, [value])
  return <div className="ov-editor-wrap" aria-label={label}><div className="ov-editor-toolbar"><span>{label}</span><button type="button" onClick={() => editor.current && openSearchPanel(editor.current)} aria-label="Find in source">Find <kbd>Ctrl F</kbd></button></div><div ref={host} className={`ov-editor ${readOnly ? 'is-readonly' : ''}`} /></div>
}

function draftVariables(duts: Dut[]) {
  const options: {path:string;iec_type:string}[] = []
  const add = (path:string, iec_type:string, depth=0) => {
    options.push({path,iec_type})
    if(depth>2)return
    const array=iec_type.replace(/\s/g,'').match(/^ARRAY\[(-?\d+)\.\.(-?\d+)\]OF(.+)$/i)
    if(array && Number(array[2])-Number(array[1])<128){for(let i=Number(array[1]);i<=Number(array[2]);i++)add(`${path}[${i}]`,array[3],depth+1)}
    else if(iec_type==='DUT_Union_Real_Word'){add(`${path}.Real_value`,'REAL',depth+1);add(`${path}.Word_value`,'ARRAY[0..1] OF WORD',depth+1)}
  }
  for(const dut of duts)for(const member of dut.members)add(`${dut.role==='INT'?'INTERN':dut.role}.${member.name}`,member.iec_type)
  return options
}

const arr = <T,>(v?: T[]) => v || []
const printable = (v: unknown): string => typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v)
function renderFbBody(flow: any, mappings: FbMapping[], processingCode: string, fallback: string) {
  if (!flow) return fallback
  const renderSection = (section: 'inputs' | 'outputs') => {
    const key = section === 'inputs' ? 'input_segments' : 'output_segments'
    const segments: any[] = flow[key] || [], current = mappings.filter(item => item.section === section), seen = new Set<string>()
    let result = ''
    const assignment = (mapping: FbMapping) => `\t${mapping.destination} := ${mapping.source};\n`
    for (const segment of segments) {
      if (segment.kind === 'preserved') { result += segment.code || ''; continue }
      if (segment.kind !== 'mapping') continue
      const mapping = current.find(item => item.id === segment.id)
      if (!mapping) continue
      if (mapping.id) seen.add(mapping.id)
      result += mapping.source === segment.source && mapping.destination === segment.destination && segment.original ? segment.original : assignment(mapping)
    }
    for (const mapping of current) if (!mapping.id || !seen.has(mapping.id)) {
      if (result && !result.endsWith('\n')) result += '\n'
      result += assignment(mapping)
    }
    return result
  }
  const anyMappings = mappings.length > 0
  return (flow.prefix || '') + (flow.input_header || (anyMappings ? '// INPUTS\n' : '')) + renderSection('inputs') +
    (flow.processing_header || (anyMappings ? '\n// PROCESSING\n' : '')) + processingCode +
    (flow.output_header || (anyMappings ? '\n// OUTPUTS\n' : '')) + renderSection('outputs')
}

export default function V2ObjectView({ kind, objectId, csrf, projectRevision, onSaved, onOpenType, onDirtyChange }: Props) {
  const [data, setData] = useState<ObjectData | null>(null)
  const [copyTypes, setCopyTypes] = useState<{ id: string; name: string; duts: Dut[] }[]>([])
  const [catalog, setCatalog] = useState<CatalogOptions>({ sections: BASE_SECTIONS, prefixes: BASE_PREFIXES })
  const [draftDuts, setDraftDuts] = useState<Dut[]>([])
  const [processingCode, setProcessingCode] = useState('')
  const [mappings, setMappings] = useState<FbMapping[]>([])
  const [customCode, setCustomCode] = useState('')
  const [active, setActive] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [reviewed, setReviewed] = useState(false)
  const [pane, setPane] = useState<'members' | 'st'>('members')
  const [fbTab, setFbTab] = useState<'inputs' | 'processing' | 'outputs' | 'custom'>('inputs')
  const [fbFullscreen, setFbFullscreen] = useState(false)
  const [graphFullscreen, setGraphFullscreen] = useState(false)
  const [dragging, setDragging] = useState<number | null>(null)
  const dirty = useMemo(() => Boolean(data && (JSON.stringify(draftDuts) !== JSON.stringify(data.duts) || processingCode !== (data.fb?.processing_code || '') || JSON.stringify(mappings) !== JSON.stringify(data.fb?.mappings || []) || customCode !== (data.fb?.custom_code || ''))), [data, draftDuts, processingCode, mappings, customCode])
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  useEffect(() => {
    if (!graphFullscreen) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') { if (fbFullscreen) setFbFullscreen(false); else setGraphFullscreen(false) } }
    const priorOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)
    return () => { document.body.style.overflow = priorOverflow; window.removeEventListener('keydown', onKeyDown) }
  }, [graphFullscreen, fbFullscreen])
  const toggleCanvasFullscreen = () => setGraphFullscreen(value => !value)
  const invalidate = () => { setPreview(null); setReviewed(false) }
  useEffect(() => { let live = true; request<CatalogOptions>('/catalog/options').then(value => { if (live) setCatalog({ sections: [...new Set([...BASE_SECTIONS, ...(value.sections || [])])], prefixes: [...new Set([...BASE_PREFIXES, ...(value.prefixes || [])])] }) }).catch(() => {}); return () => { live = false } }, [])
  const saveCatalog = async (next: CatalogOptions) => { const previous = catalog; setCatalog(next); setError(''); try { const saved = await request<CatalogOptions>('/catalog/options', { method: 'POST', body: JSON.stringify(next) }, csrf); setCatalog({ sections: [...new Set([...BASE_SECTIONS, ...(saved.sections || [])])], prefixes: [...new Set([...BASE_PREFIXES, ...(saved.prefixes || [])])] }); if (typeof saved.revision === 'number') setData(old => old ? { ...old, revision: saved.revision! } : old); invalidate(); onSaved() } catch (e) { setCatalog(previous); setError(e instanceof Error ? e.message : 'Unable to save catalog options') } }
  const addCatalogOption = async (key: 'sections' | 'prefixes') => { const label = window.prompt(key === 'sections' ? 'New section' : 'New prefix'); if (!label?.trim()) return; const value = label.trim().replace(/_+$/, ''); if (!value) return; await saveCatalog({ ...catalog, [key]: [...new Set([...catalog[key], value])] }) }
  const knownPrefixes = [...new Set(catalog.prefixes)]
  const memberParts = (name: string) => { const prefix = knownPrefixes.sort((a,b) => b.length-a.length).find(item => name.startsWith(`${item}_`)); return { prefix: prefix || '', basename: prefix ? name.slice(prefix.length + 1) : name } }
  const setMemberParts = (index: number, prefix: string, basename: string) => updateMember(index, 'name', `${prefix ? `${prefix}_` : ''}${basename}`)
  useEffect(() => {
    if (dirty && data?.id === objectId) return
    let live = true; setError('')
    request<ObjectData>(`/objects/${kind}/${encodeURIComponent(objectId)}`).then(result => { if (!live) return; setData(result); setDraftDuts(result.duts || []); setActive(result.duts?.[0]?.name || ''); setProcessingCode(result.fb?.processing_code || ''); setMappings(result.fb?.mappings || []); setCustomCode(result.fb?.custom_code || ''); setPreview(null) }).catch(e => { if (live) setError(e instanceof Error ? e.message : 'Unable to load object') })
    return () => { live = false }
  }, [kind, objectId, projectRevision])
  const currentDut = draftDuts.find(d => d.name === active)
  useEffect(() => { if (kind !== 'type') { setCopyTypes([]); return }; let live = true; request<{ types?: { id: string; name: string; duts: Dut[] }[] }>('/project').then(value => { if (live) setCopyTypes((value.types || []).filter(type => String(type.id) !== String(objectId))) }).catch(() => {}); return () => { live = false } }, [kind, objectId])
  const sameRoleTypes = currentDut ? copyTypes.filter(type => type.duts?.some(dut => dut.role === currentDut.role)) : []
  const copyMembersFromType = (typeId: string) => { if (!typeId || !currentDut) return; const type = sameRoleTypes.find(item => String(item.id) === typeId); const source = type?.duts?.find(dut => dut.role === currentDut.role); if (!source) return; setDraftDuts(old => old.map(dut => dut.name === active ? { ...dut, members: source.members.map(member => ({ ...member })) } : dut)); invalidate() }
  const copyMembersSelect = currentDut && sameRoleTypes.length ? <label className="ov-copy-members">Copy members from<select value="" disabled={kind === 'instance'} onChange={event => copyMembersFromType(event.target.value)}><option value="">Choose a type with DUT role {currentDut.role}</option>{sameRoleTypes.map(type => <option key={type.id} value={type.id}>{type.name} · {currentDut.role}</option>)}</select></label> : null
  const liveDeclaration = currentDut ? `TYPE ${currentDut.name} :\nSTRUCT\n${currentDut.members.map((m, i) => `${i === 0 || currentDut.members[i - 1]?.section !== m.section ? `    //----------------------------${m.section || 'INTERNAL'}\n` : ''}    ${m.name} : ${m.iec_type}${m.default_value !== '' && m.default_value != null ? ` := ${m.default_value}` : ''};${m.comment ? ` // ${m.comment}` : ''}`).join('\n')}\nEND_STRUCT\nEND_TYPE` : ''
  const generatedBody = renderFbBody(data?.fb?.flow, mappings, processingCode, data?.fb?.generated_body || data?.fb?.body || '')
  const fbEditor = (tab: 'inputs' | 'processing' | 'outputs' | 'custom') => tab === 'inputs' ? <FbMappingEditor section="inputs" mappings={mappings} variables={draftVariables(draftDuts)} preserved="" readOnly={kind === 'instance'} onChange={next => { setMappings(next); invalidate() }}/> : tab === 'outputs' ? <FbMappingEditor section="outputs" mappings={mappings} variables={draftVariables(draftDuts)} preserved="" readOnly={kind === 'instance'} onChange={next => { setMappings(next); invalidate() }}/> : <section className="ov-fb-code"><h4>{tab === 'processing' ? 'PROCESSING source' : 'Custom code'}</h4><StEditor value={tab === 'processing' ? processingCode : customCode} onChange={setSource(tab === 'processing' ? setProcessingCode : setCustomCode)} readOnly={kind === 'instance'} label={`Function block ${tab === 'processing' ? 'PROCESSING' : 'custom'} source`}/></section>
  const updateMember = (index: number, key: keyof Member, value: string) => { setDraftDuts(old => old.map(d => d.name !== active ? d : { ...d, members: d.members.map((m, i) => i === index ? { ...m, [key]: value } : m) })); invalidate() }
  const moveMember = (from: number, to: number) => { if (!currentDut || to < 0 || to >= currentDut.members.length) return; setDraftDuts(old => old.map(d => { if (d.name !== active) return d; const members = [...d.members]; const [m] = members.splice(from, 1); members.splice(to, 0, m); return { ...d, members: members.map((item, position) => ({ ...item, position })) } })); invalidate() }
  const addMember = () => { if (!currentDut) return; setDraftDuts(old => old.map(d => d.name === active ? { ...d, members: [...d.members, { name: 'NewVariable', iec_type: 'BOOL', section: 'INTERNAL', default_value: '', unit: '', comment: '', exchange: '' }] } : d)); invalidate() }
  const removeMember = (index: number) => { setDraftDuts(old => old.map(d => d.name === active ? { ...d, members: d.members.filter((_, i) => i !== index) } : d)); invalidate() }
  const setSource = (setter: (s: string) => void) => (s: string) => { setter(s); invalidate() }
  const makePreview = async () => {
    if (!data) return
    setBusy(true); setError(''); setPreview(null); setReviewed(false)
    try { const result = await request<Preview>(`/objects/type/${encodeURIComponent(objectId)}/preview`, { method: 'POST', body: JSON.stringify({ revision: data.revision, duts: draftDuts.map(d => ({ name: d.name, members: d.members })), processing_code: processingCode, mappings, custom_code: customCode }) }, csrf); setPreview(result) } catch (e) { setError(e instanceof Error ? e.message : 'Preview failed') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!preview?.token || !reviewed) return
    setBusy(true); setError('')
    try { await request('/objects/apply', { method: 'POST', body: JSON.stringify({ token: preview.token }) }, csrf); setPreview(null); setReviewed(false); onSaved(); const result = await request<ObjectData>(`/objects/type/${encodeURIComponent(objectId)}`); setData(result); setDraftDuts(result.duts || []); setProcessingCode(result.fb?.processing_code || ''); setMappings(result.fb?.mappings || []); setCustomCode(result.fb?.custom_code || '') } catch (e) { setError(e instanceof Error ? e.message : 'Apply failed') } finally { setBusy(false) }
  }
  const changeDut = (name: string) => { setActive(name); setFbFullscreen(true) }
  if (!data && !error) return <div className="ov-loading">Loading object…</div>
  return <section className={`ov-root ${graphFullscreen ? 'has-graph-fullscreen' : ''}`}>
    {error && <div className="ov-alert" role="alert">{error}</div>}
    {data && <>
      <header className="ov-header"><div><div className="ov-breadcrumb">Objects <span>/</span> {kind === 'type' ? 'Type' : 'Instance'}</div><h2>{data.name}</h2><p>{data.kind} <span>·</span> revision {data.revision}{dirty && <em className="ov-dirty">Draft changes</em>}</p></div><div className="ov-actions">{kind === 'instance' && data.type_id && onOpenType && <button type="button" className="ov-secondary" onClick={() => onOpenType(data.type_id!)}>Edit type</button>}{kind === 'type' && <button type="button" className="ov-primary" disabled={!dirty || busy} onClick={() => void makePreview()}>{busy ? 'Preparing…' : 'Review changes'}</button>}</div></header>
      <div className={`ov-canvas-host ${graphFullscreen ? 'is-fullscreen' : ''}`}><ObjectCanvas key={`${kind}:${objectId}`} duts={draftDuts} fbName={data.fb?.name || data.name} active={active} onSelect={changeDut} objectKey={`${kind}:${objectId}`} /><button className="ov-canvas-fullscreen" type="button" aria-pressed={graphFullscreen} onClick={toggleCanvasFullscreen}>{graphFullscreen ? 'Reduce graph' : 'Fullscreen graph'}</button></div>
      <div className="ov-layout"><aside className="ov-object-rail"><div className="ov-rail-title">Structures</div>{data.duts.map(d => <button key={d.name} type="button" className={`ov-structure-item ${active === d.name ? 'active' : ''}`} onClick={() => changeDut(d.name)}><span className="ov-dut-glyph">▧</span><span>{d.name}</span><small>{d.role || 'DUT'}</small></button>)}{data.fb && <button type="button" className={`ov-structure-item ${active === '__fb' ? 'active' : ''}`} onClick={() => changeDut('__fb')}><span className="ov-fb-glyph">ƒ</span><span>{data.fb.name || data.name}</span><small>Function block</small></button>}</aside>
      <main className="ov-main">{active === '__fb' && data.fb ? <><div className="ov-content-heading"><div><span className="ov-eyebrow">FUNCTION BLOCK</span><h3>{data.fb.name || data.name}</h3><p>INPUTS and OUTPUTS are generated from mappings; PROCESSING is editable.</p></div><button type="button" className="ov-secondary" onClick={() => setFbFullscreen(true)}>Open full editor</button></div>
        <nav className="ov-fb-tabs" aria-label="Function block sections">{(['inputs','processing','outputs','custom'] as const).map(tab => <button type="button" key={tab} className={fbTab === tab ? 'active' : ''} onClick={() => setFbTab(tab)}>{tab === 'custom' ? 'Custom code' : tab === 'processing' ? 'PROCESSING' : tab.toUpperCase()}</button>)}</nav>
        <div className="ov-fb-panels">{fbEditor(fbTab)}</div></> : <>{currentDut ? <><div className="ov-content-heading"><div><span className="ov-eyebrow">DATA UNIT TYPE</span><h3>{currentDut.name}</h3><p>{currentDut.role || 'Structure'} · {currentDut.members.length} members</p></div><div className="ov-dut-heading-tools">{copyMembersSelect}<div className="ov-tabs"><button className={pane === 'members' ? 'active' : ''} onClick={() => setPane('members')}>Members</button><button className={pane === 'st' ? 'active' : ''} onClick={() => setPane('st')}>ST declaration</button></div></div></div>{pane === 'members' ? <div className="ov-table-wrap"><table className="ov-table"><thead><tr><th className="ov-order-col" title="Member order is preserved in the generated ST declaration.">↕</th><th title="IEC variable identifier. Prefix and basename are joined as PREFIX_basename in the canonical member name.">Name</th><th title="IEC 61131-3 data type used by Structured Text.">IEC type</th><th title="Initial ST value assigned at declaration.">Default</th><th title="Comment separator group inside this DUT declaration. This does not choose an ST VAR_INPUT or VAR_OUTPUT declaration section.">Comment group</th><th title="Engineering unit for this value.">Unit</th><th title="Documentation comment emitted with the ST member.">Comment</th><th title="External exchange identifier or tag reference.">Exchange</th><th title="Remove this member."></th></tr></thead><tbody>{currentDut.members.map((m, i) => <tr key={i} draggable={kind === 'type'} onDragStart={() => setDragging(i)} onDragOver={e => e.preventDefault()} onDrop={() => { if (dragging !== null) moveMember(dragging, i); setDragging(null) }} className={dragging === i ? 'dragging' : ''}><td className="ov-order-col"><button aria-label={`Move ${m.name} up`} disabled={kind === 'instance' || i === 0} onClick={() => moveMember(i, i - 1)}>↑</button><button aria-label={`Move ${m.name} down`} disabled={kind === 'instance' || i === currentDut.members.length - 1} onClick={() => moveMember(i, i + 1)}>↓</button></td><td><div className="ov-name-parts"><select title="Optional member prefix. The canonical ST name is PREFIX_basename." aria-label={`${m.name} prefix`} disabled={kind === 'instance'} value={memberParts(m.name).prefix} onChange={e => e.target.value === '__add' ? void addCatalogOption('prefixes') : setMemberParts(i, e.target.value, memberParts(m.name).basename)}><option value="">No prefix</option>{knownPrefixes.map(prefix => <option key={prefix}>{prefix}</option>)}<option value="__add">＋ Add prefix…</option></select><span>{memberParts(m.name).prefix ? '_' : ''}</span><input title="Member basename. Stored and generated with its selected prefix." aria-label={`${m.name} basename`} disabled={kind === 'instance'} value={memberParts(m.name).basename} onChange={e => setMemberParts(i, memberParts(m.name).prefix, e.target.value)}/></div></td><td><select aria-label={`${m.name} IEC type`} disabled={kind === 'instance'} value={['BOOL','BYTE','WORD','DWORD','LWORD','SINT','INT','DINT','LINT','USINT','UINT','UDINT','ULINT','REAL','LREAL','TIME','DATE','TOD','DT','STRING','WSTRING','DUT_Union_Real_Word',...draftDuts.map(d => d.name)].includes(m.iec_type) ? m.iec_type : '__custom'} onChange={e => updateMember(i, 'iec_type', e.target.value === '__custom' ? '' : e.target.value)}><option value="__custom">Custom type…</option>{['BOOL','BYTE','WORD','DWORD','LWORD','SINT','INT','DINT','LINT','USINT','UINT','UDINT','ULINT','REAL','LREAL','TIME','DATE','TOD','DT','STRING','WSTRING','DUT_Union_Real_Word',...draftDuts.map(d => d.name)].map((type, ti) => <option key={`${type}-${ti}`} value={type}>{type}</option>)}</select>{!['BOOL','BYTE','WORD','DWORD','LWORD','SINT','INT','DINT','LINT','USINT','UINT','UDINT','ULINT','REAL','LREAL','TIME','DATE','TOD','DT','STRING','WSTRING','DUT_Union_Real_Word',...draftDuts.map(d => d.name)].includes(m.iec_type) && <input aria-label={`${m.name} custom IEC type`} disabled={kind === 'instance'} value={m.iec_type} onChange={e => updateMember(i, 'iec_type', e.target.value)} placeholder="e.g. ARRAY[0..3] OF INT"/>}</td>{(['default_value','section','unit','comment','exchange'] as const).map(key => <td key={key}>{key === 'section' ? <div className="ov-catalog-select"><select title="Comment separator group inside this DUT. It does not select an ST VAR_INPUT or VAR_OUTPUT declaration section." aria-label={`${m.name} comment group`} disabled={kind === 'instance'} value={m.section || 'INTERNAL'} onChange={e => e.target.value === '__add' ? void addCatalogOption('sections') : updateMember(i, 'section', e.target.value)}>{[...new Set([...catalog.sections, m.section || 'INTERNAL'])].map(value => <option key={value}>{value}</option>)}<option value="__add">＋ Add section…</option></select></div> : <input title={key === 'default_value' ? 'Initial value assigned in the ST declaration.' : key === 'unit' ? 'Engineering unit used to describe this value.' : key === 'comment' ? 'Human-readable documentation comment for this member.' : 'External exchange or tag identifier associated with this member.'} aria-label={`${m.name} ${key}`} disabled={kind === 'instance'} value={printable(m[key])} onChange={e => updateMember(i, key, e.target.value)} />}</td>)}<td><button className="ov-remove" aria-label={`Remove ${m.name}`} disabled={kind === 'instance'} onClick={() => removeMember(i)}>×</button></td></tr>)}</tbody></table>{kind === 'type' && <button className="ov-add" onClick={addMember}>＋ Add member</button>}</div> : <div className="ov-st-preview"><StEditor value={liveDeclaration} readOnly label={`${currentDut.name}.st · generated declaration`}/></div>}</> : <div className="ov-empty">Choose a structure or function block.</div>}</>}</main>
      <aside className="ov-links"><div className="ov-links-head"><div><span className="ov-eyebrow">CONNECTED DATA</span><h3>Related tags</h3></div></div>{([['Communication','tcp_points','RTU slaves','rtu_slaves'],['Exchange','exchange_calls','PA values','pa_values'],['I/O channels','io_channels','RTU channels','rtu_channels']] as const).map(([label,key,label2,key2]) => <section className="ov-link-group" key={key}><h4>{label}</h4>{arr(data.links?.[key]).length ? arr(data.links?.[key]).map((item,i) => <LinkTag key={i} item={item}/> ) : <p className="ov-none">No linked tags</p>}<h4>{label2}</h4>{arr(data.links?.[key2]).length ? arr(data.links?.[key2]).map((item,i) => <LinkTag key={i} item={item}/> ) : <p className="ov-none">No linked tags</p>}</section>)}</aside></div>
      {fbFullscreen && <div className="ov-fb-modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) setFbFullscreen(false) }}><section className="ov-fb-modal" role="dialog" aria-modal="true" aria-labelledby="ov-fb-modal-title"><header><div><span className="ov-eyebrow">{active === '__fb' ? 'FUNCTION BLOCK EDITOR' : 'DUT EDITOR'}</span><h3 id="ov-fb-modal-title">{active === '__fb' ? (data.fb?.name || data.name) : currentDut?.name}</h3><p>{active === '__fb' ? 'Generated Structured Text and all function block sections.' : `${currentDut?.role || 'DUT'} · ${currentDut?.members.length || 0} members · edit the declaration members below.`}</p></div><button type="button" aria-label="Close full editor" onClick={() => setFbFullscreen(false)}>×</button></header><div className="ov-fb-modal-body">{active === '__fb' && data.fb ? <><section className="ov-fb-code"><h4>Generated Structured Text</h4><StEditor value={generatedBody} readOnly label="Generated function block body"/></section>{(['inputs','processing','outputs','custom'] as const).map(tab => <section className="ov-fb-modal-section" key={tab}><h4>{tab === 'custom' ? 'Custom code' : tab === 'processing' ? 'PROCESSING source' : tab.toUpperCase()}</h4>{fbEditor(tab)}</section>)}</> : currentDut ? <>{copyMembersSelect}<div className="ov-table-wrap ov-modal-member-table"><table className="ov-table"><thead><tr><th className="ov-order-col" title="Member order in the DUT declaration.">↕</th><th title="Canonical IEC identifier; prefix_basename is stored as one member name.">Name</th><th title="IEC 61131-3 data type.">IEC type</th><th title="Initial value in the declaration.">Default</th><th title="Comment separator group inside this DUT; this does not choose VAR_INPUT or VAR_OUTPUT.">Comment group</th><th title="Engineering unit.">Unit</th><th title="Documentation comment.">Comment</th><th title="External exchange identifier or tag reference.">Exchange</th><th title="Remove this member."></th></tr></thead><tbody>{currentDut.members.map((m,i)=><tr key={i}><td className="ov-order-col"><button type="button" aria-label={`Move ${m.name} up`} disabled={kind==='instance'||i===0} onClick={()=>moveMember(i,i-1)}>↑</button><button type="button" aria-label={`Move ${m.name} down`} disabled={kind==='instance'||i===currentDut.members.length-1} onClick={()=>moveMember(i,i+1)}>↓</button></td><td><div className="ov-name-parts"><select title="Optional prefix; a selected prefix is joined to the basename with an underscore." aria-label={`${m.name} prefix`} disabled={kind==='instance'} value={memberParts(m.name).prefix} onChange={e=>e.target.value==='__add'?void addCatalogOption('prefixes'):setMemberParts(i,e.target.value,memberParts(m.name).basename)}><option value="">No prefix</option>{knownPrefixes.map(prefix=><option key={prefix}>{prefix}</option>)}<option value="__add">＋ Add prefix…</option></select><span>{memberParts(m.name).prefix?'_':''}</span><input title="Member basename; saved with its optional prefix." aria-label={`${m.name} basename`} disabled={kind==='instance'} value={memberParts(m.name).basename} onChange={e=>setMemberParts(i,memberParts(m.name).prefix,e.target.value)}/></div></td><td><input aria-label={`${m.name} IEC type`} title="IEC 61131-3 data type." disabled={kind==='instance'} value={m.iec_type} onChange={e=>updateMember(i,'iec_type',e.target.value)}/></td>{(['default_value','section','unit','comment','exchange'] as const).map(key=><td key={key}>{key==='section'?<select title="Comment separator group inside this DUT; it does not select an ST declaration section." aria-label={`${m.name} comment group`} disabled={kind==='instance'} value={m.section||'INTERNAL'} onChange={e=>e.target.value==='__add'?void addCatalogOption('sections'):updateMember(i,key,e.target.value)}>{[...new Set([...catalog.sections,m.section||'INTERNAL'])].map(value=><option key={value}>{value}</option>)}<option value="__add">＋ Add group…</option></select>:<input title={key==='default_value'?'Initial value in the declaration.':key==='unit'?'Engineering unit.':key==='comment'?'Documentation comment.':'External exchange identifier or tag reference.'} aria-label={`${m.name} ${key}`} disabled={kind==='instance'} value={printable(m[key])} onChange={e=>updateMember(i,key,e.target.value)}/>}</td>)}<td><button className="ov-remove" type="button" aria-label={`Remove ${m.name}`} disabled={kind==='instance'} onClick={()=>removeMember(i)}>×</button></td></tr>)}</tbody></table>{kind==='type'&&<button className="ov-add" type="button" onClick={addMember}>＋ Add member</button>}</div><div className="ov-st-preview"><StEditor value={liveDeclaration} readOnly label={`${currentDut.name}.st · generated declaration`}/></div></> : <div className="ov-empty">No editor is available for this selection.</div>}</div><footer><button type="button" className="ov-secondary" onClick={() => setFbFullscreen(false)}>Close editor</button></footer></section></div>}
      {preview && <div className="ov-review-backdrop"><section className="ov-review" role="dialog" aria-modal="true" aria-labelledby="ov-review-title"><header><div><span className="ov-eyebrow">CHANGE REVIEW</span><h3 id="ov-review-title">Review draft</h3></div><button onClick={() => setPreview(null)} aria-label="Close review">×</button></header><div className="ov-review-body"><h4>Changes</h4>{preview.changes?.length ? <ul>{preview.changes.map((change,i)=><li key={i}>{change}</li>)}</ul> : <p>No change summary was returned.</p>}{arr(preview.errors).length > 0 && <div className="ov-review-errors"><b>Errors</b>{arr(preview.errors).map((item,i)=><p key={i}>{printable(item)}</p>)}</div>}{arr(preview.warnings).length > 0 && <div className="ov-review-warnings"><b>Warnings</b>{arr(preview.warnings).map((item,i)=><p key={i}>{printable(item)}</p>)}</div>}<label className="ov-review-check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={arr(preview.errors).length > 0}/> I reviewed these changes and want to apply them.</label></div><footer><button className="ov-secondary" onClick={() => setPreview(null)}>Back to draft</button><button className="ov-primary" disabled={!reviewed || busy || arr(preview.errors).length > 0} onClick={() => void apply()}>{busy ? 'Applying…' : 'Apply changes'}</button></footer></section></div>}
    </>}
  </section>
}

function LinkTag({ item }: { item: unknown }) {
  const obj = item && typeof item === 'object' ? item as Record<string, unknown> : null
  const name = obj ? String(obj.name || obj.tag || obj.id || 'Linked item') : String(item)
  const details = obj ? [...new Set(['table','address','instance_id','line_id','device','GVL_COM variable','GVL_MODBUS variable','GVL_IO member','GVL_IOMAP variable','FC','Instance A','Instance B','member_name','value'].map(key => obj[key]).filter(value => value !== undefined && value !== null && value !== '').map(String))].join(' · ') : ''
  return <div className="ov-link-tag" title={details}><span className="ov-tag-dot"/><div><b>{name}</b>{details && <small>{details}</small>}</div></div>
}
