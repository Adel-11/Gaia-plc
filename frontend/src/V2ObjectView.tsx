import ModalFullscreen from './ModalFullscreen'
import RawStEditor from './RawStEditor'
import LocalVariables, { type LocalVariable } from './LocalVariables'
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
import WorkspacePanel from './WorkspacePanel'
import StInsertMenu, { stSnippets, type InsertMode } from './StInsertMenu'
import { localVariableOptions, visibleVariables, type StVariable } from './StVariables'
import PanelIcon from './PanelIcon'
import DutMapping from './DutMapping'

type Member = { id?: string; name: string; iec_type: string; default_value?: string | number | boolean | null; section?: string; unit?: string; comment?: string; exchange?: string; position?: number }
type Dut = { manual_declaration?: boolean; name: string; role?: string; members: Member[]; st?: string }
type LinkMap = { tcp_points?: unknown[]; rtu_slaves?: unknown[]; rtu_channels?: unknown[]; io_channels?: unknown[]; pa_values?: unknown[]; exchange_calls?: unknown[] }
type ObjectData = { id: string; name: string; kind: string; type_id?: string; revision: number; duts: Dut[]; fb?: { local_declaration_base?: string; manual_body?: boolean; manual_declaration?: boolean; local_variables?: LocalVariable[]; declared_variables?: StVariable[]; name?: string; declaration?: string; body?: string; generated_body?: string; custom_code?: string; processing_code?: string; mappings?: FbMapping[]; preserved_sections?: Record<string, string>; variable_options?: VariableOption[]; flow?: any }; links?: LinkMap }
type FlowSection = 'inputs' | 'outputs'
type ManualSections = Partial<Record<FlowSection, string | null>>
type Preview = { token: string; errors?: unknown[]; warnings?: unknown[]; changes?: string[] }
type CatalogOptions = { sections: string[]; prefixes: string[]; revision?: number }
const BASE_SECTIONS = ['INPUT (field to plc)', 'OUTPUT (plc to field)', 'STATE', 'PARAMETERS', 'INTERNAL']
const BASE_PREFIXES = ['INPUT', 'OUTPUT', 'STATE', 'CMD', 'SPARE', 'COM', 'HMI']
type Props = { codeOnly?: boolean; kind: 'type' | 'instance'; objectId: string; csrf: string; projectRevision?: number; onSaved: () => void; onOpenType?: (typeId: string) => void; onDirtyChange?: (dirty: boolean) => void; initialStructure?: string; initialStructureVersion?: number }

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

export function StEditor({ value, onChange, readOnly = false, label, insertVariables }: { value: string; onChange?: (value: string) => void; readOnly?: boolean; label: string; insertVariables?: StVariable[] }) {
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange); onChangeRef.current = onChange
  const applyingValue = useRef(false)
  const [insertMenu, setInsertMenu] = useState<{ x: number; y: number; mode: InsertMode } | null>(null)
  const canInsert = !readOnly && !!onChange
  const closeInsert = (restoreFocus: boolean) => {
    setInsertMenu(null)
    if (restoreFocus) editor.current?.focus()
  }
  const insert = (text: string, placeholder?: string) => {
    const view = editor.current
    if (!view || !canInsert) return
    const { from, to } = view.state.selection.main
    const indent = view.state.doc.lineAt(from).text.match(/^\s*/)?.[0] || ''
    const code = text.replaceAll('\n', `\n${indent}`)
    const start = placeholder ? code.indexOf(placeholder) : -1
    view.dispatch({ changes: { from, to, insert: code },
      selection: start >= 0 ? { anchor: from + start, head: from + start + placeholder!.length } : { anchor: from + code.length },
      scrollIntoView: true, userEvent: 'input' })
    closeInsert(true)
  }
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
  return <div className="ov-editor-wrap" aria-label={label}>
    <div className="ov-editor-toolbar"><span>{label}</span><div className="st-insert-tools">
      {canInsert && <>
        {(['IF', 'FOR'] as const).map(key => <button key={key} type="button" title={`Insérer ${key}`} onClick={() => insert(...stSnippets[key])}>{key}</button>)}
        {(['variables', 'snippets'] as const).map(mode => <button key={mode} type="button" aria-haspopup="dialog" onClick={event => {
          const rect = event.currentTarget.getBoundingClientRect()
          setInsertMenu({ x: rect.left, y: rect.bottom + 4, mode })
        }}>{mode === 'variables' ? 'Variables' : 'Instructions'}</button>)}
      </>}
      <button type="button" onClick={() => editor.current && openSearchPanel(editor.current)} aria-label="Find in source">Find <kbd>Ctrl F</kbd></button>
    </div></div>
    <div ref={host} className={`ov-editor ${readOnly ? 'is-readonly' : ''}`} onContextMenu={event => {
      const view = editor.current
      if (!view || !canInsert) return
      event.preventDefault()
      const pos = view.posAtCoords({ x: event.clientX, y: event.clientY })
      const { from, to } = view.state.selection.main
      if (pos != null && (pos < from || pos > to)) view.dispatch({ selection: { anchor: pos } })
      setInsertMenu({ x: event.clientX, y: event.clientY, mode: 'choices' })
    }}/>
    {canInsert && insertMenu && <StInsertMenu key={`${insertMenu.mode}:${insertMenu.x}:${insertMenu.y}`} {...insertMenu}
      initialMode={insertMenu.mode} variables={insertVariables ?? []} onInsert={insert} onClose={closeInsert}/>}
  </div>
}

export function draftVariables(duts: { role?: string; members: Member[] }[]) {
  const options: {path:string;iec_type:string}[] = []
  const add = (path:string, iec_type:string, depth=0) => {
    options.push({path,iec_type})
    if(depth>2)return
    const array=iec_type.replace(/\s/g,'').match(/^ARRAY\[(-?\d+)\.\.(-?\d+)\]OF(.+)$/i)
    if(array && Number(array[2])-Number(array[1])<128){for(let i=Number(array[1]);i<=Number(array[2]);i++)add(`${path}[${i}]`,array[3],depth+1)}
    else if(iec_type==='DUT_Union_Real_Word'){add(`${path}.Real_value`,'REAL',depth+1);add(`${path}.Word_value`,'ARRAY[0..1] OF WORD',depth+1)}
  }
  for(const dut of duts)for(const member of dut.members)if (/^[A-Za-z_]\w*$/.test(member.name)) add(`${dut.role==='INT'?'INTERN':dut.role}.${member.name}`,member.iec_type)
  return options
}

function uniqueMemberName(members: Member[]) {
  const names = new Set(members.map(member => member.name.toUpperCase()))
  let i = 1
  while (names.has(`VARIABLE_${i}`)) i += 1
  return `Variable_${i}`
}

const arr = <T,>(v?: T[]) => v || []
const printable = (v: unknown): string => typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v)
function renderMappingSection(flow: any, mappings: FbMapping[], section: FlowSection) {
    const key = section === 'inputs' ? 'input_segments' : 'output_segments'
    const segments: any[] = flow?.[key] || [], current = mappings.filter(item => item.section === section && item.source.trim() && item.destination.trim()), seen = new Set<string>()
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

export default function V2ObjectView({ kind, objectId, csrf, projectRevision, onSaved, onOpenType, onDirtyChange, codeOnly = false, initialStructure, initialStructureVersion }: Props) {
  const [data, setData] = useState<ObjectData | null>(null)
  const [dutMapping, setDutMapping] = useState<'IO' | 'COM' | null>(null)
  const [copyTypes, setCopyTypes] = useState<{ id: string; name: string; duts: Dut[] }[]>([])
  const [catalog, setCatalog] = useState<CatalogOptions>({ sections: BASE_SECTIONS, prefixes: BASE_PREFIXES })
  const [draftDuts, setDraftDuts] = useState<Dut[]>([])
  const [processingCode, setProcessingCode] = useState('')
  const [mappings, setMappings] = useState<FbMapping[]>([])
  const [customCode, setCustomCode] = useState('')
  const [localVars, setLocalVars] = useState<LocalVariable[]>([])
  const [active, setActive] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [reviewed, setReviewed] = useState(false)
  const [pane, setPane] = useState<'members' | 'st'>('members')
  const [prefixCode, setPrefixCode] = useState('')
  const [manualSections, setManualSections] = useState<ManualSections>({})
  const [rawName, setRawName] = useState('')
  const [fbFullscreen, setFbFullscreen] = useState(false)
  const [graphFullscreen, setGraphFullscreen] = useState(false)
  const [dragging, setDragging] = useState<number | null>(null)
  const lastAppliedStructure = useRef<string | undefined>(undefined)
  const originalManual = data?.fb?.flow?.section_overrides as ManualSections | undefined
  const dirty = useMemo(() => Boolean(data && (
    prefixCode !== (data.fb?.flow?.prefix || '') || Object.keys(manualSections).length > 0 || JSON.stringify(draftDuts) !== JSON.stringify(data.duts) || processingCode !== (data.fb?.processing_code || '') || JSON.stringify(mappings) !== JSON.stringify(data.fb?.mappings || []) || customCode !== (data.fb?.custom_code || '') || JSON.stringify(localVars) !== JSON.stringify(data.fb?.local_variables || []))), [data, draftDuts, processingCode, mappings, customCode, localVars, prefixCode, manualSections])
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  useEffect(() => {
    if (!data || !initialStructure) return
    const requestKey = `${initialStructureVersion ?? 0}:${initialStructure}`
    if (lastAppliedStructure.current === requestKey) return
    if (initialStructure === '__fb' ? !data.fb : !data.duts.some(dut => dut.name === initialStructure)) return
    setActive(initialStructure)
    lastAppliedStructure.current = requestKey
  }, [data, initialStructure, initialStructureVersion])
  useEffect(() => {
    if (!graphFullscreen) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape' && !rawName) { if (fbFullscreen) setFbFullscreen(false); else setGraphFullscreen(false) } }
    const priorOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)
    return () => { document.body.style.overflow = priorOverflow; window.removeEventListener('keydown', onKeyDown) }
  }, [graphFullscreen, fbFullscreen, rawName])
  const toggleCanvasFullscreen = () => setGraphFullscreen(value => !value)
  const invalidate = () => { setPreview(null); setReviewed(false) }
  const discardDraft = () => {
    if (!data) return
    setDraftDuts(data.duts || []); setPrefixCode(data.fb?.flow?.prefix || ''); setManualSections({})
    setProcessingCode(data.fb?.processing_code || '')
    setMappings(data.fb?.mappings || [])
    setCustomCode(data.fb?.custom_code || ''); setLocalVars(data.fb?.local_variables || [])
    setPreview(null); setReviewed(false); setError('')
  }
  useEffect(() => { let live = true; request<CatalogOptions>('/catalog/options').then(value => { if (live) setCatalog({ sections: [...new Set([...BASE_SECTIONS, ...(value.sections || [])])], prefixes: [...new Set([...BASE_PREFIXES, ...(value.prefixes || [])])] }) }).catch(() => {}); return () => { live = false } }, [])
  const saveCatalog = async (next: CatalogOptions) => { const previous = catalog; setCatalog(next); setError(''); try { const saved = await request<CatalogOptions>('/catalog/options', { method: 'POST', body: JSON.stringify(next) }, csrf); setCatalog({ sections: [...new Set([...BASE_SECTIONS, ...(saved.sections || [])])], prefixes: [...new Set([...BASE_PREFIXES, ...(saved.prefixes || [])])] }); if (typeof saved.revision === 'number') setData(old => old ? { ...old, revision: saved.revision! } : old); invalidate(); onSaved() } catch (e) { setCatalog(previous); setError(e instanceof Error ? e.message : 'Unable to save catalog options') } }
  const addCatalogOption = async (key: 'sections' | 'prefixes') => { const label = window.prompt(key === 'sections' ? 'New section' : 'New prefix'); if (!label?.trim()) return; const value = label.trim().replace(/_+$/, ''); if (!value) return; await saveCatalog({ ...catalog, [key]: [...new Set([...catalog[key], value])] }) }
  const knownPrefixes = [...new Set(catalog.prefixes)]
  const memberParts = (name: string) => { const prefix = knownPrefixes.sort((a,b) => b.length-a.length).find(item => name.startsWith(`${item}_`)); return { prefix: prefix || '', basename: prefix ? name.slice(prefix.length + 1) : name } }
  const setMemberParts = (index: number, prefix: string, basename: string) => updateMember(index, 'name', `${prefix ? `${prefix}_` : ''}${basename}`)
  useEffect(() => {
    if (dirty && data?.id === objectId) return
    let live = true; setError('')
    request<ObjectData>(`/objects/${kind}/${encodeURIComponent(objectId)}`).then(result => { if (!live) return; setData(result); setDraftDuts(result.duts || []); setPrefixCode(result.fb?.flow?.prefix || ''); setManualSections({}); setActive(previous => codeOnly || (previous === '__fb' && result.fb) ? '__fb' : result.duts.some(dut => dut.name === previous) ? previous : result.duts[0]?.name || ''); setProcessingCode(result.fb?.processing_code || ''); setMappings(result.fb?.mappings || []); setCustomCode(result.fb?.custom_code || ''); setLocalVars(result.fb?.local_variables || []); setPreview(null) }).catch(e => { if (live) setError(e instanceof Error ? e.message : 'Unable to load object') })
    return () => { live = false }
  }, [kind, objectId, projectRevision])
  const currentDut = draftDuts.find(d => d.name === active)
  useEffect(() => { if (kind !== 'type') { setCopyTypes([]); return }; let live = true; request<{ types?: { id: string; name: string; duts: Dut[] }[] }>('/project').then(value => { if (live) setCopyTypes((value.types || []).filter(type => String(type.id) !== String(objectId))) }).catch(() => {}); return () => { live = false } }, [kind, objectId])
  const sameRoleTypes = currentDut ? copyTypes.filter(type => type.duts?.some(dut => dut.role === currentDut.role)) : []
  const copyMembersFromType = (typeId: string) => { if (!typeId || !currentDut) return; const type = sameRoleTypes.find(item => String(item.id) === typeId); const source = type?.duts?.find(dut => dut.role === currentDut.role); if (!source) return; setDraftDuts(old => old.map(dut => dut.name === active ? { ...dut, members: source.members.map(member => ({ ...member, id: currentDut.members.find(m => m.name.toLowerCase() === member.name.toLowerCase())?.id })) } : dut)); invalidate() }
  const copyMembersSelect = currentDut && sameRoleTypes.length ? <label className="ov-copy-members">Copy members from<select value="" disabled={kind === 'instance'} onChange={event => copyMembersFromType(event.target.value)}><option value="">Choose a type with DUT role {currentDut.role}</option>{sameRoleTypes.map(type => <option key={type.id} value={type.id}>{type.name} · {currentDut.role}</option>)}</select></label> : null
  const liveDeclaration = currentDut ? `TYPE ${currentDut.name} :\nSTRUCT\n${currentDut.members.map((m, i) => `${i === 0 || currentDut.members[i - 1]?.section !== m.section ? `    //----------------------------${m.section || 'INTERNAL'}\n` : ''}    ${m.name} : ${m.iec_type}${m.default_value !== '' && m.default_value != null ? ` := ${m.default_value}` : ''};${m.comment ? ` // ${m.comment}` : ''}`).join('\n')}\nEND_STRUCT\nEND_TYPE` : ''
  const localDeclaration = `VAR
${localVars.map(v => `    ${v.name || 'Variable'} : ${v.iec_type || 'BOOL'}${v.default_value ? ` := ${v.default_value}` : ''};`).join('\n')}
END_VAR`
  const variables = draftVariables(draftDuts)
  const knownVariables = data?.fb?.variable_options || []
  const knownPaths = new Set(variables.map(variable => variable.path))
  for (const variable of knownVariables) {
    const root = variable.path.match(/^[^.]+\.[^.\[]+/)?.[0]
    if (root && knownPaths.has(root) && !knownPaths.has(variable.path) &&
        variables.find(item => item.path === root)?.iec_type === knownVariables.find(item => item.path === root)?.iec_type) {
      variables.push(variable)
      knownPaths.add(variable.path)
    }
  }
  const managedNames = new Set(data?.fb?.local_variables?.map(row => row.name.toUpperCase()))
  const declaredVariables = (data?.fb?.declared_variables ?? []).filter(row => !managedNames.has(row.path.toUpperCase()))
  const insertVariables = [...variables, ...visibleVariables([], [...declaredVariables, ...localVariableOptions(localVars)])]
  const openRaw = (name?: string) => {
    if (name && (!dirty || window.confirm('Ouvrir le ST complet ? Le brouillon de cet éditeur sera abandonné.'))) {
      discardDraft()
      setRawName(name)
    }
  }
  const fbPanel = (surface: string, id: string, title: string, children: React.ReactNode, height?: number, className = '') =>
    <WorkspacePanel id={`fb:${objectId}:${surface}:${id}`} title={title} initialHeight={height}
      minHeight={height ? 150 : 52} className={`ov-fb-part ${className}`} verticalOnly>{children}</WorkspacePanel>
  const renderMapping = (surface: string, section: FlowSection) => {
    const title = section === 'inputs' ? 'entrées' : 'sorties'
    const generated = renderMappingSection(data?.fb?.flow, mappings, section)
    const manual = section in manualSections ? manualSections[section] : originalManual?.[section]
    return <div className="ov-fb-mapping-pair">
      {fbPanel(surface, `${section}:mapping`, `Mapping des ${title}`, <FbMappingEditor section={section} mappings={mappings} variables={variables} preserved=""
        readOnly={kind === 'instance' || manual != null}
        onChange={next => { setMappings(next); setError(''); invalidate() }}/>)}
      {fbPanel(surface, `${section}:st`, `ST des ${title}`, <>
        {kind === 'type' && <div className="ov-fb-mode">
          <span>{manual == null ? 'ST issu du mapping' : 'ST manuel utilisé à l’export'}</span>
          <button type="button" className="ov-secondary" onClick={() => {
            setManualSections(previous => {
              const next = {...previous}
              if (manual != null && originalManual?.[section] == null) delete next[section]
              else next[section] = manual == null ? generated : null
              return next
            })
            invalidate()
          }}>{manual == null ? 'Modifier le ST' : 'Revenir au mapping'}</button>
        </div>}
        <StEditor value={manual ?? generated} readOnly={kind === 'instance' || manual == null} insertVariables={insertVariables}
          onChange={value => { setManualSections(previous => ({...previous, [section]: value})); invalidate() }}
          label={`ST des ${title}`}/>
      </>, 290, 'ov-fb-part-code')}
    </div>
  }
  const renderFbEditor = (surface: string) => <div className="ov-fb-workbench">
    {(data?.fb?.manual_body || data?.fb?.manual_declaration) && <p className="ov-alert" role="status">
      Ce bloc utilise du ST manuel à l’export. Ouvre le ST complet pour le modifier ou revenir au modèle.
    </p>}
    <WorkspacePanel id={`fb:${objectId}:${surface}:inputs`} title="Entrées" className="ov-fb-zone inputs" verticalOnly>
      {fbPanel(surface, 'inputs:local-preview', 'Déclaration VAR',
        <StEditor value={[data?.fb?.local_declaration_base, localVars.length || !data?.fb?.local_declaration_base ? localDeclaration : ''].filter(Boolean).join('\n\n')} readOnly label="Déclaration VAR · aperçu en direct"/>
      , 205, 'ov-fb-part-code')}
      {fbPanel(surface, 'inputs:locals', 'Variables internes', <>
        {data?.fb?.manual_declaration && <p>Déclaration en ST manuel. Modifie-la dans « ST complet ».</p>}
        <LocalVariables rows={localVars} disabled={busy || kind === 'instance' || data?.fb?.manual_declaration}
          onChange={rows => { setLocalVars(rows); invalidate() }}/>
      </>)}
      {renderMapping(surface, 'inputs')}
    </WorkspacePanel>
    <WorkspacePanel id={`fb:${objectId}:${surface}:processing`} title="Traitement" className="ov-fb-zone processing" verticalOnly>
      {fbPanel(surface, 'processing:st', 'ST du traitement',
        <StEditor value={processingCode} onChange={setSource(setProcessingCode)} readOnly={kind === 'instance'} label="ST du traitement" insertVariables={insertVariables}/>
      , 350, 'ov-fb-part-code')}
      <details className="ov-fb-extra"><summary><PanelIcon name="down"/>ST avant les entrées et après les sorties</summary>
        {fbPanel(surface, 'processing:prefix', 'ST avant les entrées',
          <StEditor value={prefixCode} onChange={setSource(setPrefixCode)} readOnly={kind === 'instance'} label="ST avant les entrées" insertVariables={insertVariables}/>
        , 225, 'ov-fb-part-code')}
        {fbPanel(surface, 'processing:suffix', 'ST après les sorties',
          <StEditor value={customCode} onChange={setSource(setCustomCode)} readOnly={kind === 'instance'} label="ST après les sorties" insertVariables={insertVariables}/>
        , 225, 'ov-fb-part-code')}
      </details>
    </WorkspacePanel>
    <WorkspacePanel id={`fb:${objectId}:${surface}:outputs`} title="Sorties" className="ov-fb-zone outputs" verticalOnly>
      {renderMapping(surface, 'outputs')}
    </WorkspacePanel>
  </div>
  const updateMember = (index: number, key: keyof Member, value: string) => { setDraftDuts(old => old.map(d => d.name !== active ? d : { ...d, members: d.members.map((m, i) => i === index ? { ...m, [key]: value } : m) })); invalidate() }
  const moveMember = (from: number, to: number) => { if (!currentDut || to < 0 || to >= currentDut.members.length) return; setDraftDuts(old => old.map(d => { if (d.name !== active) return d; const members = [...d.members]; const [m] = members.splice(from, 1); members.splice(to, 0, m); return { ...d, members: members.map((item, position) => ({ ...item, position })) } })); invalidate() }
  const addMember = () => { if (!currentDut) return; setDraftDuts(old => old.map(d => d.name === active ? { ...d, members: [...d.members, { name: uniqueMemberName(d.members), iec_type: 'BOOL', section: 'INTERNAL', default_value: '', unit: '', comment: '', exchange: '' }] } : d)); invalidate() }
  const removeMember = (index: number) => { setDraftDuts(old => old.map(d => d.name === active ? { ...d, members: d.members.filter((_, i) => i !== index) } : d)); invalidate() }
  const renderRemoveMember = (member: Member, index: number) => kind === 'type' &&
    <button className="ov-remove" type="button" aria-label={`Supprimer ${member.name}`} disabled={busy} onClick={() => removeMember(index)}>Supprimer</button>
  const emptyMembers = currentDut && !currentDut.members.length &&
    <p className="ov-empty-members">Le DUT n’a plus de membre. À l’enregistrement, le générateur ajoute un membre technique SPARE de type BOOL.</p>
  const setSource = (setter: (s: string) => void) => (s: string) => { setter(s); invalidate() }
  const makePreview = async () => {
    if (!data) return
    if (mappings.some(row => !row.source.trim() || !row.destination.trim())) {
      setError('Choisis la source et la destination de chaque mapping ou supprime les lignes incomplètes.')
      return
    }
    setBusy(true); setError(''); setPreview(null); setReviewed(false)
    try { const result = await request<Preview>(`/objects/type/${encodeURIComponent(objectId)}/preview`, { method: 'POST', body: JSON.stringify({ revision: data.revision, duts: JSON.stringify(draftDuts) === JSON.stringify(data.duts) ? undefined : draftDuts.map(d => ({ name: d.name, role: d.role, members: d.members })), prefix_code: prefixCode !== (data.fb?.flow?.prefix || '') ? prefixCode : undefined, section_overrides: Object.keys(manualSections).length ? manualSections : undefined, processing_code: processingCode !== (data.fb?.processing_code || '') ? processingCode : undefined, mappings: JSON.stringify(mappings) !== JSON.stringify(data.fb?.mappings || []) ? mappings : undefined, custom_code: customCode !== (data.fb?.custom_code || '') ? customCode : undefined, local_variables: JSON.stringify(localVars) !== JSON.stringify(data.fb?.local_variables || []) ? localVars : undefined }) }, csrf); setPreview(result) } catch (e) { setError(e instanceof Error ? e.message : 'Preview failed') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!preview?.token || !reviewed) return
    setBusy(true); setError('')
    try { await request('/objects/apply', { method: 'POST', body: JSON.stringify({ token: preview.token }) }, csrf); setPreview(null); setReviewed(false); onSaved(); const result = await request<ObjectData>(`/objects/type/${encodeURIComponent(objectId)}`); setData(result); setDraftDuts(result.duts || []); setPrefixCode(result.fb?.flow?.prefix || ''); setManualSections({}); setProcessingCode(result.fb?.processing_code || ''); setMappings(result.fb?.mappings || []); setCustomCode(result.fb?.custom_code || ''); setLocalVars(result.fb?.local_variables || []) } catch (e) { setError(e instanceof Error ? e.message : 'Apply failed') } finally { setBusy(false) }
  }
  const changeDut = (name: string) => { setActive(name); setFbFullscreen(true) }
  const mappingRole = currentDut?.role === 'IO' || currentDut?.role === 'COM' ? currentDut.role : undefined
  const mappingButton = mappingRole && <button type="button" className="ov-secondary" disabled={dirty || busy}
    title={dirty ? 'Enregistre le brouillon du DUT avant de mapper ses variables.' : undefined}
    onClick={() => setDutMapping(mappingRole)}>{mappingRole === 'IO' ? 'Créer ou mapper une IOMAP' : 'Mapper en Modbus'}</button>
  if (!data && !error) return <div className="ov-loading">Loading object…</div>
  return <section className={`ov-root ${graphFullscreen ? 'has-graph-fullscreen' : ''}`}>
    {error && !fbFullscreen && <div className="ov-alert" role="alert">{error}</div>}
    {data && <>
      <header className="ov-header"><div><div className="ov-breadcrumb">Objects <span>/</span> {kind === 'type' ? 'Type' : 'Instance'}</div><h2>{data.name}</h2><p>{data.kind} <span>·</span> revision {data.revision}{dirty && <em className="ov-dirty">Draft changes</em>}</p></div><div className="ov-actions">{kind === 'instance' && data.type_id && onOpenType && <button type="button" className="ov-secondary" onClick={() => onOpenType(data.type_id!)}>Modifier le type</button>}{kind === 'type' && dirty && <button type="button" className="ov-secondary" disabled={busy} onClick={discardDraft}>Annuler le brouillon</button>}{kind === 'type' && <button type="button" className="ov-primary" disabled={!dirty || busy} onClick={() => void makePreview()}>{busy ? 'Preparing…' : 'Enregistrer'}</button>}</div></header>
      {!codeOnly && <WorkspacePanel id={`object:${kind}:${objectId}:graph`} title="Object relationship graph" className={`ov-canvas-host ${graphFullscreen ? 'is-fullscreen' : ''}`} initialHeight={360}><ObjectCanvas key={`${kind}:${objectId}`} duts={draftDuts} fbName={data.fb?.name || data.name} active={active} onSelect={changeDut} objectKey={`${kind}:${objectId}`} /><button className="ov-canvas-fullscreen" type="button" aria-pressed={graphFullscreen} title={graphFullscreen ? 'Réduire le graphique' : 'Agrandir le graphique'} aria-label={graphFullscreen ? 'Réduire le graphique' : 'Agrandir le graphique'} onClick={toggleCanvasFullscreen}><PanelIcon name={graphFullscreen ? 'shrink' : 'expand'}/></button></WorkspacePanel>}
      <div className={`ov-layout ${kind === 'type' ? 'type-editor-layout' : ''} ${codeOnly ? 'code-only-layout' : ''}`}>{!codeOnly && <aside className="ov-object-rail"><div className="ov-rail-title">Structures</div>{data.duts.map(d => <button key={d.name} type="button" className={`ov-structure-item ${active === d.name ? 'active' : ''}`} onClick={() => changeDut(d.name)}><span className="ov-dut-glyph">▧</span><span>{d.name}</span><small>{d.role || 'DUT'}</small></button>)}{data.fb && <button type="button" className={`ov-structure-item ${active === '__fb' ? 'active' : ''}`} onClick={() => changeDut('__fb')}><span className="ov-fb-glyph">ƒ</span><span>{data.fb.name || data.name}</span><small>Function block</small></button>}</aside>}
      <WorkspacePanel id={`object:${kind}:${objectId}:editor`} title="Object declaration and editor" className="ov-editor-panel" contentClassName="ov-main">{active === '__fb' && data.fb ? <><div className="ov-content-heading"><div><span className="ov-eyebrow">FUNCTION BLOCK</span><h3>{data.fb.name || data.name}</h3><p>Les mappings génèrent le ST des entrées et sorties. Chaque zone peut passer en mode manuel; PROCESSING is editable.</p></div><button type="button" className="ov-secondary ov-expand-editor" title="Ouvrir l’éditeur" aria-label="Ouvrir l’éditeur" onClick={() => setFbFullscreen(true)}><PanelIcon name="expand"/></button></div>
        {kind === 'type' && <button className="ov-secondary ov-raw-button" type="button" onClick={() => openRaw(data.fb?.name)}>Ouvrir le ST complet</button>}
        {!fbFullscreen && renderFbEditor('inline')}</> : <>{currentDut ? <><div className="ov-content-heading"><div><span className="ov-eyebrow">DATA UNIT TYPE</span><h3>{currentDut.name}</h3><p>{currentDut.role || 'Structure'} · {currentDut.members.length} members</p></div><div className="ov-dut-heading-tools">{mappingButton}{kind === 'type' && <button type="button" className="ov-secondary" onClick={() => openRaw(currentDut.name)}>Ouvrir le ST complet</button>}{copyMembersSelect}<div className="ov-tabs"><button className={pane === 'members' ? 'active' : ''} onClick={() => setPane('members')}>Members</button><button className={pane === 'st' ? 'active' : ''} onClick={() => setPane('st')}>ST declaration</button></div></div></div>{pane === 'members' ? <div className="ov-table-wrap"><table className="ov-table"><thead><tr><th className="ov-order-col" title="Member order is preserved in the generated ST declaration.">↕</th><th title="IEC variable identifier. Prefix and basename are joined as PREFIX_basename in the canonical member name.">Name</th><th title="IEC 61131-3 data type used by Structured Text.">IEC type</th><th title="Initial ST value assigned at declaration.">Default</th><th title="Comment separator group inside this DUT declaration. This does not choose an ST VAR_INPUT or VAR_OUTPUT declaration section.">Comment group</th><th title="Engineering unit for this value.">Unit</th><th title="Documentation comment emitted with the ST member.">Comment</th><th title="External exchange identifier or tag reference.">Exchange</th></tr></thead><tbody>{currentDut.members.map((m, i) => <tr key={i} draggable={kind === 'type'} onDragStart={() => setDragging(i)} onDragOver={e => e.preventDefault()} onDrop={() => { if (dragging !== null) moveMember(dragging, i); setDragging(null) }} className={dragging === i ? 'dragging' : ''}><td className="ov-order-col"><button aria-label={`Move ${m.name} up`} disabled={kind === 'instance' || i === 0} onClick={() => moveMember(i, i - 1)}>↑</button><button aria-label={`Move ${m.name} down`} disabled={kind === 'instance' || i === currentDut.members.length - 1} onClick={() => moveMember(i, i + 1)}>↓</button>{renderRemoveMember(m, i)}</td><td><div className="ov-name-parts"><select title="Optional member prefix. The canonical ST name is PREFIX_basename." aria-label={`${m.name} prefix`} disabled={kind === 'instance'} value={memberParts(m.name).prefix} onChange={e => e.target.value === '__add' ? void addCatalogOption('prefixes') : setMemberParts(i, e.target.value, memberParts(m.name).basename)}><option value="">No prefix</option>{knownPrefixes.map(prefix => <option key={prefix}>{prefix}</option>)}<option value="__add">＋ Add prefix…</option></select><span>{memberParts(m.name).prefix ? '_' : ''}</span><input title={m.name} aria-label={`${m.name} basename`} disabled={kind === 'instance'} value={memberParts(m.name).basename} onChange={e => setMemberParts(i, memberParts(m.name).prefix, e.target.value)}/></div></td><td><select aria-label={`${m.name} IEC type`} disabled={kind === 'instance'} value={['BOOL','BYTE','WORD','DWORD','LWORD','SINT','INT','DINT','LINT','USINT','UINT','UDINT','ULINT','REAL','LREAL','TIME','DATE','TOD','DT','STRING','WSTRING','DUT_Union_Real_Word',...draftDuts.map(d => d.name)].includes(m.iec_type) ? m.iec_type : '__custom'} onChange={e => updateMember(i, 'iec_type', e.target.value === '__custom' ? '' : e.target.value)}><option value="__custom">Custom type…</option>{['BOOL','BYTE','WORD','DWORD','LWORD','SINT','INT','DINT','LINT','USINT','UINT','UDINT','ULINT','REAL','LREAL','TIME','DATE','TOD','DT','STRING','WSTRING','DUT_Union_Real_Word',...draftDuts.map(d => d.name)].map((type, ti) => <option key={`${type}-${ti}`} value={type}>{type}</option>)}</select>{!['BOOL','BYTE','WORD','DWORD','LWORD','SINT','INT','DINT','LINT','USINT','UINT','UDINT','ULINT','REAL','LREAL','TIME','DATE','TOD','DT','STRING','WSTRING','DUT_Union_Real_Word',...draftDuts.map(d => d.name)].includes(m.iec_type) && <input aria-label={`${m.name} custom IEC type`} disabled={kind === 'instance'} value={m.iec_type} onChange={e => updateMember(i, 'iec_type', e.target.value)} placeholder="e.g. ARRAY[0..3] OF INT"/>}</td>{(['default_value','section','unit','comment','exchange'] as const).map(key => <td key={key}>{key === 'section' ? <div className="ov-catalog-select"><select title="Comment separator group inside this DUT. It does not select an ST VAR_INPUT or VAR_OUTPUT declaration section." aria-label={`${m.name} comment group`} disabled={kind === 'instance'} value={m.section || 'INTERNAL'} onChange={e => e.target.value === '__add' ? void addCatalogOption('sections') : updateMember(i, 'section', e.target.value)}>{[...new Set([...catalog.sections, m.section || 'INTERNAL'])].map(value => <option key={value}>{value}</option>)}<option value="__add">＋ Add section…</option></select></div> : <input title={key === 'default_value' ? 'Initial value assigned in the ST declaration.' : key === 'unit' ? 'Engineering unit used to describe this value.' : key === 'comment' ? 'Human-readable documentation comment for this member.' : 'External exchange or tag identifier associated with this member.'} aria-label={`${m.name} ${key}`} disabled={kind === 'instance'} value={printable(m[key])} onChange={e => updateMember(i, key, e.target.value)} />}</td>)}</tr>)}</tbody></table>{emptyMembers}{kind === 'type' && <button className="ov-add" onClick={addMember}>＋ Add member</button>}</div> : <div className="ov-st-preview"><StEditor value={liveDeclaration} readOnly label={`${currentDut.name}.st · generated declaration`}/></div>}</> : <div className="ov-empty">Choose a structure or function block.</div>}</>}</WorkspacePanel>
      {kind === 'instance' && <aside className="ov-links"><div className="ov-links-head"><div><span className="ov-eyebrow">CONNECTED DATA</span><h3>Related tags</h3></div></div>{([['Communication','tcp_points','RTU slaves','rtu_slaves'],['Exchange','exchange_calls','PA values','pa_values'],['I/O channels','io_channels','RTU channels','rtu_channels']] as const).map(([label,key,label2,key2]) => <section className="ov-link-group" key={key}><h4>{label}</h4>{arr(data.links?.[key]).length ? arr(data.links?.[key]).map((item,i) => <LinkTag key={i} item={item}/> ) : <p className="ov-none">No linked tags</p>}<h4>{label2}</h4>{arr(data.links?.[key2]).length ? arr(data.links?.[key2]).map((item,i) => <LinkTag key={i} item={item}/> ) : <p className="ov-none">No linked tags</p>}</section>)}</aside>}</div>
      {fbFullscreen && <div className="ov-fb-modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) setFbFullscreen(false) }}><section className="ov-fb-modal" role="dialog" aria-modal="true" aria-labelledby="ov-fb-modal-title"><ModalFullscreen/>{error && <p className="ov-alert" role="alert">{error}</p>}<header><div><span className="ov-eyebrow">{active === '__fb' ? 'FUNCTION BLOCK EDITOR' : 'DUT EDITOR'}</span><h3 id="ov-fb-modal-title">{active === '__fb' ? (data.fb?.name || data.name) : currentDut?.name}</h3><p>{active === '__fb' ? 'Generated Structured Text and all function block sections.' : `${currentDut?.role || 'DUT'} · ${currentDut?.members.length || 0} members · edit the declaration members below.`}</p></div><div className="ov-dialog-tools">
          {mappingButton}
          {kind === 'type' && <button type="button" className="ov-secondary" onClick={() => openRaw(active === '__fb' ? data.fb?.name : currentDut?.name)}>Ouvrir le ST complet</button>}
          <button type="button" aria-label="Close full editor" onClick={() => setFbFullscreen(false)}>×</button>
        </div></header><div className={`ov-fb-modal-body ${active === '__fb' ? 'is-fb' : 'is-dut'}`}>{active === '__fb' && data.fb ? renderFbEditor('dialog') : currentDut ? <>{currentDut.manual_declaration && <p className="ov-alert">Ce DUT utilise sa déclaration ST manuelle à l’export.</p>}{copyMembersSelect}<WorkspacePanel id={`dut:${objectId}:${active}:members`} title="Membres du DUT" className="ov-dut-member-panel" initialHeight={420} minHeight={240}>
          {kind === 'type' && <button className="ov-add ov-dut-add" type="button" onClick={addMember}>＋ Ajouter un membre</button>}
          <div className="ov-table-wrap ov-modal-member-table"><table className="ov-table"><thead><tr><th className="ov-order-col" title="Member order in the DUT declaration.">↕</th><th title="Canonical IEC identifier; prefix_basename is stored as one member name.">Name</th><th title="IEC 61131-3 data type.">IEC type</th><th title="Initial value in the declaration.">Default</th><th title="Comment separator group inside this DUT; this does not choose VAR_INPUT or VAR_OUTPUT.">Comment group</th><th title="Engineering unit.">Unit</th><th title="Documentation comment.">Comment</th><th title="External exchange identifier or tag reference.">Exchange</th></tr></thead><tbody>{currentDut.members.map((m,i)=><tr key={i}><td className="ov-order-col"><button type="button" aria-label={`Move ${m.name} up`} disabled={kind==='instance'||i===0} onClick={()=>moveMember(i,i-1)}>↑</button><button type="button" aria-label={`Move ${m.name} down`} disabled={kind==='instance'||i===currentDut.members.length-1} onClick={()=>moveMember(i,i+1)}>↓</button>{renderRemoveMember(m, i)}</td><td><div className="ov-name-parts"><select title="Optional prefix; a selected prefix is joined to the basename with an underscore." aria-label={`${m.name} prefix`} disabled={kind==='instance'} value={memberParts(m.name).prefix} onChange={e=>e.target.value==='__add'?void addCatalogOption('prefixes'):setMemberParts(i,e.target.value,memberParts(m.name).basename)}><option value="">No prefix</option>{knownPrefixes.map(prefix=><option key={prefix}>{prefix}</option>)}<option value="__add">＋ Add prefix…</option></select><span>{memberParts(m.name).prefix?'_':''}</span><input title={m.name} aria-label={`${m.name} basename`} disabled={kind==='instance'} value={memberParts(m.name).basename} onChange={e=>setMemberParts(i,memberParts(m.name).prefix,e.target.value)}/></div></td><td><input aria-label={`${m.name} IEC type`} title="IEC 61131-3 data type." disabled={kind==='instance'} value={m.iec_type} onChange={e=>updateMember(i,'iec_type',e.target.value)}/></td>{(['default_value','section','unit','comment','exchange'] as const).map(key=><td key={key}>{key==='section'?<select title="Comment separator group inside this DUT; it does not select an ST declaration section." aria-label={`${m.name} comment group`} disabled={kind==='instance'} value={m.section||'INTERNAL'} onChange={e=>e.target.value==='__add'?void addCatalogOption('sections'):updateMember(i,key,e.target.value)}>{[...new Set([...catalog.sections,m.section||'INTERNAL'])].map(value=><option key={value}>{value}</option>)}<option value="__add">＋ Add group…</option></select>:<input title={key==='default_value'?'Initial value in the declaration.':key==='unit'?'Engineering unit.':key==='comment'?'Documentation comment.':'External exchange identifier or tag reference.'} aria-label={`${m.name} ${key}`} disabled={kind==='instance'} value={printable(m[key])} onChange={e=>updateMember(i,key,e.target.value)}/>}</td>)}</tr>)}</tbody></table>{emptyMembers}</div></WorkspacePanel><div className="ov-st-preview"><StEditor value={liveDeclaration} readOnly label={`${currentDut.name}.st · generated declaration`}/></div></> : <div className="ov-empty">No editor is available for this selection.</div>}</div><footer><button type="button" className="ov-secondary" onClick={() => setFbFullscreen(false)}>Fermer</button>{kind === 'type' && <button type="button" className="ov-primary" disabled={!dirty || busy} onClick={() => void makePreview()}>Enregistrer</button>}</footer></section></div>}
      {dutMapping && <DutMapping role={dutMapping} type={[data.id, data.name, data.type_id || '']} instance={kind === 'instance' ? data.name : undefined}
        csrf={csrf} onClose={() => setDutMapping(null)} onSaved={onSaved}/>}
      {rawName && <RawStEditor name={rawName} csrf={csrf} insertVariables={rawName === data?.fb?.name ? insertVariables : undefined} onClose={() => setRawName('')} onSaved={onSaved}/>}
      {preview && <div className="ov-review-backdrop"><section className="ov-review" role="dialog" aria-modal="true" aria-labelledby="ov-review-title"><ModalFullscreen/><header><div><span className="ov-eyebrow">CHANGE REVIEW</span><h3 id="ov-review-title">Vérifier les modifications</h3></div><button onClick={() => setPreview(null)} aria-label="Close review">×</button></header><div className="ov-review-body"><h4>Changes</h4>{preview.changes?.length ? <ul>{preview.changes.map((change,i)=><li key={i}>{change}</li>)}</ul> : <p>No change summary was returned.</p>}{arr(preview.errors).length > 0 && <div className="ov-review-errors"><b>Errors</b>{arr(preview.errors).map((item,i)=><p key={i}>{printable(item)}</p>)}</div>}{arr(preview.warnings).length > 0 && <div className="ov-review-warnings"><b>Warnings</b>{arr(preview.warnings).map((item,i)=><p key={i}>{printable(item)}</p>)}</div>}<label className="ov-review-check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={arr(preview.errors).length > 0}/> I reviewed these changes and want to apply them.</label></div><footer><button className="ov-secondary" onClick={() => setPreview(null)}>Retour au brouillon</button><button className="ov-primary" disabled={!reviewed || busy || arr(preview.errors).length > 0} onClick={() => void apply()}>{busy ? 'Applying…' : 'Confirmer l’enregistrement'}</button></footer></section></div>}
    </>}
  </section>
}

function LinkTag({ item }: { item: unknown }) {
  const obj = item && typeof item === 'object' ? item as Record<string, unknown> : null
  const name = obj ? String(obj.name || obj.tag || obj.id || 'Linked item') : String(item)
  const details = obj ? [...new Set(['table','address','instance_id','line_id','device','GVL_COM variable','GVL_MODBUS variable','GVL_IO member','GVL_IOMAP variable','FC','Instance A','Instance B','member_name','value'].map(key => obj[key]).filter(value => value !== undefined && value !== null && value !== '').map(String))].join(' · ') : ''
  return <div className="ov-link-tag" title={details}><span className="ov-tag-dot"/><div><b>{name}</b>{details && <small>{details}</small>}</div></div>
}
