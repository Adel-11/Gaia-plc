import { useEffect, useMemo, useRef, useState } from 'react'
import { EditorView, highlightActiveLine, lineNumbers } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { StreamLanguage, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { keymap } from '@codemirror/view'
import { openSearchPanel, searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import './object-view.css'

type Member = { name: string; iec_type: string; default_value?: string | number | boolean | null; section?: string; unit?: string; comment?: string; exchange?: string; position?: number }
type Dut = { name: string; role?: string; members: Member[]; st?: string }
type LinkMap = { tcp_points?: unknown[]; rtu_slaves?: unknown[]; rtu_channels?: unknown[]; io_channels?: unknown[]; pa_values?: unknown[]; exchange_calls?: unknown[] }
type ObjectData = { id: string; name: string; kind: string; type_id?: string; revision: number; duts: Dut[]; fb?: { name?: string; declaration?: string; body?: string; custom_code?: string }; links?: LinkMap }
type Preview = { token: string; errors?: unknown[]; warnings?: unknown[]; changes?: string[] }
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

function StEditor({ value, onChange, readOnly = false, label }: { value: string; onChange?: (value: string) => void; readOnly?: boolean; label: string }) {
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange); onChangeRef.current = onChange
  useEffect(() => {
    if (!host.current) return
    const state = EditorState.create({ doc: value, extensions: [lineNumbers(), highlightActiveLine(), highlightSelectionMatches(), history(), stLanguage, syntaxHighlighting(defaultHighlightStyle), keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]), EditorView.editable.of(!readOnly), EditorState.readOnly.of(readOnly), EditorView.contentAttributes.of({'aria-label': label}), EditorView.updateListener.of(update => { if (update.docChanged) onChangeRef.current?.(update.state.doc.toString()) }), EditorView.theme({ '&': { height: '100%', fontSize: '16px' }, '.cm-scroller': { overflow: 'auto', fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace' }, '.cm-gutters': { backgroundColor: '#f6f8fa', borderRight: '1px solid #e1e5eb', color: '#7a8493' } })] })
    editor.current = new EditorView({ state, parent: host.current })
    return () => { editor.current?.destroy(); editor.current = null }
  }, [readOnly])
  useEffect(() => { const view = editor.current; if (view && view.state.doc.toString() !== value) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } }) }, [value])
  return <div className="ov-editor-wrap" aria-label={label}><div className="ov-editor-toolbar"><span>{label}</span><button type="button" onClick={() => editor.current && openSearchPanel(editor.current)} aria-label="Find in source">Find <kbd>Ctrl F</kbd></button></div><div ref={host} className={`ov-editor ${readOnly ? 'is-readonly' : ''}`} /></div>
}

const arr = <T,>(v?: T[]) => v || []
const printable = (v: unknown): string => typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v)

export default function V2ObjectView({ kind, objectId, csrf, projectRevision, onSaved, onOpenType, onDirtyChange }: Props) {
  const [data, setData] = useState<ObjectData | null>(null)
  const [draftDuts, setDraftDuts] = useState<Dut[]>([])
  const [body, setBody] = useState('')
  const [customCode, setCustomCode] = useState('')
  const [active, setActive] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [reviewed, setReviewed] = useState(false)
  const [pane, setPane] = useState<'members' | 'st'>('members')
  const [dragging, setDragging] = useState<number | null>(null)
  const dirty = useMemo(() => Boolean(data && (JSON.stringify(draftDuts) !== JSON.stringify(data.duts) || body !== (data.fb?.body || '') || customCode !== (data.fb?.custom_code || ''))), [data, draftDuts, body, customCode])
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  const invalidate = () => { setPreview(null); setReviewed(false) }
  useEffect(() => {
    if (dirty && data?.id === objectId) return
    let live = true; setError('')
    request<ObjectData>(`/objects/${kind}/${encodeURIComponent(objectId)}`).then(result => { if (!live) return; setData(result); setDraftDuts(result.duts || []); setActive(result.duts?.[0]?.name || ''); setBody(result.fb?.body || ''); setCustomCode(result.fb?.custom_code || ''); setPreview(null) }).catch(e => { if (live) setError(e instanceof Error ? e.message : 'Unable to load object') })
    return () => { live = false }
  }, [kind, objectId, projectRevision])
  const currentDut = draftDuts.find(d => d.name === active)
  const liveDeclaration = currentDut ? `TYPE ${currentDut.name} :\nSTRUCT\n${currentDut.members.map(m => `    ${m.name} : ${m.iec_type}${m.default_value !== '' && m.default_value != null ? ` := ${m.default_value}` : ''};${m.comment ? ` // ${m.comment}` : ''}`).join('\n')}\nEND_STRUCT\nEND_TYPE` : ''
  const updateMember = (index: number, key: keyof Member, value: string) => { setDraftDuts(old => old.map(d => d.name !== active ? d : { ...d, members: d.members.map((m, i) => i === index ? { ...m, [key]: value } : m) })); invalidate() }
  const moveMember = (from: number, to: number) => { if (!currentDut || to < 0 || to >= currentDut.members.length) return; setDraftDuts(old => old.map(d => { if (d.name !== active) return d; const members = [...d.members]; const [m] = members.splice(from, 1); members.splice(to, 0, m); return { ...d, members: members.map((item, position) => ({ ...item, position })) } })); invalidate() }
  const addMember = () => { if (!currentDut) return; setDraftDuts(old => old.map(d => d.name === active ? { ...d, members: [...d.members, { name: 'NewVariable', iec_type: 'BOOL', section: 'VAR', default_value: '', unit: '', comment: '', exchange: '' }] } : d)); invalidate() }
  const removeMember = (index: number) => { setDraftDuts(old => old.map(d => d.name === active ? { ...d, members: d.members.filter((_, i) => i !== index) } : d)); invalidate() }
  const setSource = (setter: (s: string) => void) => (s: string) => { setter(s); invalidate() }
  const makePreview = async () => {
    if (!data) return
    setBusy(true); setError(''); setPreview(null); setReviewed(false)
    try { const result = await request<Preview>(`/objects/type/${encodeURIComponent(objectId)}/preview`, { method: 'POST', body: JSON.stringify({ revision: data.revision, duts: draftDuts.map(d => ({ name: d.name, members: d.members })), body, custom_code: customCode }) }, csrf); setPreview(result) } catch (e) { setError(e instanceof Error ? e.message : 'Preview failed') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!preview?.token || !reviewed) return
    setBusy(true); setError('')
    try { await request('/objects/apply', { method: 'POST', body: JSON.stringify({ token: preview.token }) }, csrf); setPreview(null); setReviewed(false); onSaved(); const result = await request<ObjectData>(`/objects/type/${encodeURIComponent(objectId)}`); setData(result); setDraftDuts(result.duts || []); setBody(result.fb?.body || ''); setCustomCode(result.fb?.custom_code || '') } catch (e) { setError(e instanceof Error ? e.message : 'Apply failed') } finally { setBusy(false) }
  }
  const changeDut = (name: string) => { setActive(name) }
  if (!data && !error) return <div className="ov-loading">Loading object…</div>
  return <section className="ov-root">
    {error && <div className="ov-alert" role="alert">{error}</div>}
    {data && <>
      <header className="ov-header"><div><div className="ov-breadcrumb">Objects <span>/</span> {kind === 'type' ? 'Type' : 'Instance'}</div><h2>{data.name}</h2><p>{data.kind} <span>·</span> revision {data.revision}{dirty && <em className="ov-dirty">Draft changes</em>}</p></div><div className="ov-actions">{kind === 'instance' && data.type_id && onOpenType && <button type="button" className="ov-secondary" onClick={() => onOpenType(data.type_id!)}>Edit type</button>}{kind === 'type' && <button type="button" className="ov-primary" disabled={!dirty || busy} onClick={() => void makePreview()}>{busy ? 'Preparing…' : 'Review changes'}</button>}</div></header>
      <div className="ov-topology" aria-label="PLC object topology"><div className="ov-topology-side ov-topology-left">{['PA','COM','EXC','IO'].flatMap(role => draftDuts.filter(d => d.role === role)).map(d => <button type="button" key={d.name} aria-pressed={active === d.name} className={`ov-node ${active === d.name ? 'active' : ''}`} onClick={() => changeDut(d.name)}><span className={`ov-rail-icon ov-icon-${d.role?.toLowerCase()}`}>{d.role?.slice(0,1)}</span><span><b>{d.role}</b><small>{d.members.length} members</small>{kind === 'instance' && <small>{`GVL_${d.role}.${d.role}_${data.name}`}</small>}</span></button>)}</div><svg className="ov-topology-lines" viewBox="0 0 1000 200" preserveAspectRatio="none" aria-hidden="true"><path d="M200 100 C320 100 320 100 420 100"/><path d="M580 100 C690 100 700 100 800 100"/></svg><button className={`ov-node ov-node-fb ${active === '__fb' ? 'active' : ''}`} type="button" onClick={() => changeDut('__fb')}><span className="ov-fb-glyph">ƒ</span><span><b>{data.fb?.name || data.name}</b><small>Function block</small></span></button><div className="ov-topology-side ov-topology-right">{data.duts.filter(d => d.role?.toUpperCase() === 'INT' || d.name.toUpperCase() === 'INT').map(d => <button type="button" key={d.name} className={`ov-node ov-node-int ${active === d.name ? 'active' : ''}`} onClick={() => changeDut(d.name)}><span className="ov-rail-icon ov-icon-com">I</span><span><b>{d.name}</b><small>{d.members.length} members · INT</small></span></button>)}</div></div>
      <div className="ov-layout"><aside className="ov-object-rail"><div className="ov-rail-title">Structures</div>{data.duts.map(d => <button key={d.name} type="button" className={`ov-structure-item ${active === d.name ? 'active' : ''}`} onClick={() => changeDut(d.name)}><span className="ov-dut-glyph">▧</span><span>{d.name}</span><small>{d.role || 'DUT'}</small></button>)}{data.fb && <button type="button" className={`ov-structure-item ${active === '__fb' ? 'active' : ''}`} onClick={() => changeDut('__fb')}><span className="ov-fb-glyph">ƒ</span><span>{data.fb.name || data.name}</span><small>Function block</small></button>}</aside>
      <main className="ov-main">{active === '__fb' && data.fb ? <><div className="ov-content-heading"><div><span className="ov-eyebrow">FUNCTION BLOCK</span><h3>{data.fb.name || data.name}</h3></div><span className="ov-readonly-badge">Declaration generated</span></div><div className="ov-editors-grid"><StEditor value={data.fb.declaration || ''} readOnly label="Generated declaration"/><StEditor value={body} onChange={setSource(setBody)} readOnly={kind === 'instance'} label="Function block source"/><StEditor value={customCode} onChange={setSource(setCustomCode)} readOnly={kind === 'instance'} label="Custom code"/></div></> : <>{currentDut ? <><div className="ov-content-heading"><div><span className="ov-eyebrow">DATA UNIT TYPE</span><h3>{currentDut.name}</h3><p>{currentDut.role || 'Structure'} · {currentDut.members.length} members</p></div><div className="ov-tabs"><button className={pane === 'members' ? 'active' : ''} onClick={() => setPane('members')}>Members</button><button className={pane === 'st' ? 'active' : ''} onClick={() => setPane('st')}>ST declaration</button></div></div>{pane === 'members' ? <div className="ov-table-wrap"><datalist id="ov-iec-types">{['BOOL','BYTE','WORD','DWORD','SINT','INT','DINT','UINT','UDINT','REAL','LREAL','TIME','STRING','DUT_Union_Real_Word',...draftDuts.map(d=>d.name)].map(type=><option key={type} value={type}/>)}</datalist><table className="ov-table"><thead><tr><th className="ov-order-col">↕</th><th>Name</th><th>IEC type</th><th>Default</th><th>Section</th><th>Unit</th><th>Comment</th><th>Exchange</th><th></th></tr></thead><tbody>{currentDut.members.map((m, i) => <tr key={i} draggable={kind === 'type'} onDragStart={() => setDragging(i)} onDragOver={e => e.preventDefault()} onDrop={() => { if (dragging !== null) moveMember(dragging, i); setDragging(null) }} className={dragging === i ? 'dragging' : ''}><td className="ov-order-col"><button aria-label={`Move ${m.name} up`} disabled={kind === 'instance' || i === 0} onClick={() => moveMember(i, i - 1)}>↑</button><button aria-label={`Move ${m.name} down`} disabled={kind === 'instance' || i === currentDut.members.length - 1} onClick={() => moveMember(i, i + 1)}>↓</button></td>{(['name','iec_type','default_value','section','unit','comment','exchange'] as const).map(key => <td key={key}><input list={key === 'iec_type' ? 'ov-iec-types' : undefined} aria-label={`${m.name} ${key}`} disabled={kind === 'instance'} value={printable(m[key])} onChange={e => updateMember(i, key, e.target.value)} /></td>)}<td><button className="ov-remove" aria-label={`Remove ${m.name}`} disabled={kind === 'instance'} onClick={() => removeMember(i)}>×</button></td></tr>)}</tbody></table>{kind === 'type' && <button className="ov-add" onClick={addMember}>＋ Add member</button>}</div> : <div className="ov-st-preview"><StEditor value={liveDeclaration} readOnly label={`${currentDut.name}.st · generated declaration`}/></div>}</> : <div className="ov-empty">Choose a structure or function block.</div>}</>}</main>
      <aside className="ov-links"><div className="ov-links-head"><div><span className="ov-eyebrow">CONNECTED DATA</span><h3>Related tags</h3></div></div>{([['Communication','tcp_points','RTU slaves','rtu_slaves'],['Exchange','exchange_calls','PA values','pa_values'],['I/O channels','io_channels','RTU channels','rtu_channels']] as const).map(([label,key,label2,key2]) => <section className="ov-link-group" key={key}><h4>{label}</h4>{arr(data.links?.[key]).length ? arr(data.links?.[key]).map((item,i) => <LinkTag key={i} item={item}/> ) : <p className="ov-none">No linked tags</p>}<h4>{label2}</h4>{arr(data.links?.[key2]).length ? arr(data.links?.[key2]).map((item,i) => <LinkTag key={i} item={item}/> ) : <p className="ov-none">No linked tags</p>}</section>)}</aside></div>
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
