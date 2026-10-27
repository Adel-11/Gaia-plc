import { useEffect, useMemo, useState } from 'react'
import './type-wizard.css'
import WorkspacePanel from './WorkspacePanel'

type Member = { name: string; iec_type: string; section?: string; default_value?: string | number | boolean | null; unit?: string; comment?: string }
type Dut = { role: string; members: Member[] }
type AnyRow = Record<string, any>
type CatalogOptions = { sections: string[]; prefixes: string[]; revision?: number }
type Preview = { token: string | null; errors?: string[]; generated_st?: string }
type Props = { project: any; csrf: string; onCreated: (typeId?: string) => void; onCancel: () => void; onChanged?: () => void }
const steps = ['Identity', 'Blocks', 'Review']
const roles: Record<string, string[]> = { Equipment: ['PA', 'COM', 'EXC', 'IO', 'INT'], 'Functional unit (UPI)': ['COM', 'EXC', 'INT'] }
const IEC_TYPES = ['BOOL','BYTE','WORD','DWORD','LWORD','SINT','INT','DINT','LINT','USINT','UINT','UDINT','ULINT','REAL','LREAL','TIME','DATE','TOD','DT','STRING','WSTRING']
const BASE_SECTIONS = ['INPUT (field to plc)', 'OUTPUT (plc to field)', 'STATE', 'PARAMETERS', 'INTERNAL']
const BASE_PREFIXES = ['INPUT', 'OUTPUT', 'STATE', 'CMD', 'SPARE', 'COM', 'HMI', 'SCADA']
const blankMember = (): Member => ({ name: '', iec_type: 'BOOL', section: 'INTERNAL', default_value: '', unit: '', comment: '' })
const blankDuts = (kind: string): Dut[] => (roles[kind] || roles.Equipment).map(role => ({ role, members: [{ ...blankMember(), name: 'SPARE' }] }))
const label = (key: string) => key.replaceAll('_', ' ')

async function request<T>(path: string, body: unknown, csrf: string): Promise<T> {
  const response = await fetch(`/api/v2${path}`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) }, body: JSON.stringify(body) })
  if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const data = await response.json(); const detail = data.detail || data.message || data.error; message = typeof detail === 'string' ? detail : JSON.stringify(detail || message) } catch { /* ignore non-JSON errors */ }; throw new Error(message) }
  return response.json() as Promise<T>
}

export default function V2TypeWizard({ project, csrf, onCreated, onCancel, onChanged }: Props) {
  const types: AnyRow[] = project?.types || []
  const [step, setStep] = useState(0), [name, setName] = useState(''), [kind, setKind] = useState('Equipment')
  const [description, setDescription] = useState(''), [copyFrom, setCopyFrom] = useState('')
  const [catalog, setCatalog] = useState<CatalogOptions>({ sections: BASE_SECTIONS, prefixes: BASE_PREFIXES })
  const [projectRevision, setProjectRevision] = useState<number>(Number(project?.revision || 0))
  const [duts, setDuts] = useState<Dut[]>(blankDuts('Equipment')), [selectedRole, setSelectedRole] = useState('PA')
  const [processingCode, setProcessingCode] = useState(''), [customCode, setCustomCode] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null), [reviewed, setReviewed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const activeDut = duts.find(d => d.role === selectedRole) || duts[0]
  useEffect(() => { setProjectRevision(Number(project?.revision || 0)) }, [project?.revision])
  useEffect(() => { let live = true; fetch('/api/v2/catalog/options', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).then(value => { if (live && value) setCatalog({ sections: [...new Set([...BASE_SECTIONS, ...(value.sections || [])])], prefixes: [...new Set([...BASE_PREFIXES, ...(value.prefixes || [])])] }) }).catch(() => {}); return () => { live = false } }, [])
  const duplicate = useMemo(() => types.some(t => String(t.name || '').toLowerCase() === name.trim().toLowerCase()), [types, name])
  const validIdentity = Boolean(name.trim()) && !duplicate && /^[A-Za-z_][A-Za-z0-9_]*$/.test(name.trim())
  const replaceDuts = (next: Dut[]) => { setDuts(next); setPreview(null); setReviewed(false) }
  const prefixes = [...new Set(catalog.prefixes)]
  const memberParts = (memberName: string) => { const prefix = [...prefixes].sort((a, b) => b.length - a.length).find(item => memberName.startsWith(`${item}_`)); return { prefix: prefix || '', basename: prefix ? memberName.slice(prefix.length + 1) : memberName } }
  const updateMember = (index: number, key: keyof Member, value: any) => replaceDuts(duts.map(d => d.role !== activeDut?.role ? d : { ...d, members: d.members.map((m, i) => i === index ? { ...m, [key]: value } : m) }))
  const updateMemberName = (index: number, prefix: string, basename: string) => updateMember(index, 'name', `${prefix ? `${prefix}_` : ''}${basename}`)
  const addCatalog = async (key: 'sections' | 'prefixes') => {
    const value = window.prompt(key === 'sections' ? 'New comment group' : 'New member prefix')?.trim().replace(/_+$/, '')
    if (!value) return
    const previous = catalog, next = { ...catalog, [key]: [...new Set([...catalog[key], value])] }
    setCatalog(next); setError('')
    try { const saved = await request<CatalogOptions>('/catalog/options', next, csrf); setCatalog({ sections: [...new Set([...BASE_SECTIONS, ...(saved.sections || [])])], prefixes: [...new Set([...BASE_PREFIXES, ...(saved.prefixes || [])])] }); if (typeof saved.revision === 'number') setProjectRevision(saved.revision); setPreview(null); setReviewed(false); onChanged?.() }
    catch (e) { setCatalog(previous); setError(e instanceof Error ? e.message : 'Unable to save catalog options') }
  }
  const changeKind = (next: string) => { setKind(next); replaceDuts(blankDuts(next)); setSelectedRole((roles[next] || roles.Equipment)[0]); setCopyFrom(''); setProcessingCode(''); setCustomCode('') }
  const copyType = (id: string) => {
    setCopyFrom(id)
    const selected = types.find(t => String(t.id) === id || t.name === id)
    if (!selected) { replaceDuts(blankDuts(kind)); setProcessingCode(''); setCustomCode(''); return }
    // Copy the type's reusable DUT/FB blocks only. Instance addresses and communication links are created later.
    replaceDuts((roles[kind] || roles.Equipment).map(role => {
      const dut = (selected.duts || []).find((item: AnyRow) => item.role === role)
      return { role, members: (dut?.members || []).map((m: AnyRow) => ({ name: m.name || '', iec_type: m.iec_type || 'BOOL', section: m.section || 'INTERNAL', default_value: m.default_value ?? '', unit: m.unit || '', comment: m.comment || '' })) }
    }))
    setProcessingCode(selected.fb?.flow?.processing_code || ''); setCustomCode(selected.fb?.custom_code || '')
  }
  const payload = () => ({ revision: projectRevision, name: name.trim(), kind, description, copy_from: copyFrom || null,
    duts: duts.map(d => ({ role: d.role, members: d.members.map(m => ({ ...m })) })), processing_code: processingCode, custom_code: customCode })
  const prepare = async () => { setBusy(true); setError(''); setPreview(null); setReviewed(false); try { setPreview(await request<Preview>('/types/preview', payload(), csrf)) } catch (e) { setError(e instanceof Error ? e.message : 'Preview failed') } finally { setBusy(false) } }
  const apply = async () => { if (!preview?.token || !reviewed) return; setBusy(true); setError(''); try { const result = await request<{ type?: AnyRow }>('/types/apply', { token: preview.token }, csrf); onCreated(result.type?.id || name.trim()) } catch (e) { setError(e instanceof Error ? e.message : 'Unable to create type') } finally { setBusy(false) } }
  const next = () => { setError(''); setStep(s => Math.min(2, s + 1)) }
  return <section className="type-wizard" aria-label="Create type">
    <header className="tw-header"><div><span className="tw-eyebrow">TYPE CREATION</span><h2>Create a type</h2><p>Define reusable structure and function-block logic.</p></div><div className="tw-header-actions"><button type="button" className="tw-secondary" disabled={busy} onClick={step === 0 ? onCancel : () => { setStep(s => Math.max(0, s - 1)); setError('') }}>← Retour</button><button type="button" className="tw-secondary" disabled={busy} onClick={onCancel}>Annuler la création</button></div></header>
    <nav className="tw-steps" aria-label="Creation steps">{steps.map((stepName, i) => <button type="button" key={stepName} className={i === step ? 'current' : i < step ? 'complete' : ''} aria-current={i === step ? 'step' : undefined} onClick={() => { if (i <= step) setStep(i) }}><span>{i + 1}</span>{stepName}</button>)}</nav>
    <div className="tw-content">
      {step === 0 && <WorkspacePanel id="wizard-identity" title="Identité du type"><div className="tw-fields">
        <label>Type name<input autoFocus value={name} onChange={e => { setName(e.target.value); setPreview(null); setReviewed(false) }} placeholder="e.g. PumpUnit" aria-invalid={Boolean(name && !validIdentity)}/>{name && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) && <small>Use an IEC identifier: letters, numbers, underscores; start with a letter or underscore.</small>}{duplicate && <small className="tw-error-text">A type with this name already exists.</small>}</label>
        <label>Kind<select value={kind} onChange={e => changeKind(e.target.value)}><option>Equipment</option><option>Functional unit (UPI)</option></select></label>
        <label>Description<textarea rows={2} value={description} onChange={e => { setDescription(e.target.value); setPreview(null) }}/></label>
        <label>Copy from an existing type<select value={copyFrom} onChange={e => copyType(e.target.value)}><option value="">Start with blank blocks</option>{types.filter(t => (t.kind || 'Equipment') === kind).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select><small>Copies the DUT blocks and PROCESSING code only. Add instance addresses, communication settings, and exchange links later on their respective pages.</small></label>
        <aside className="tw-flow-note"><b>Three steps:</b> create the type here, declare an instance, then configure communications and exchanges from the instance pages.</aside>
      </div></WorkspacePanel>}

      {step === 1 && <div className="tw-editor-step"><div className="tw-role-tabs" role="tablist" aria-label="DUT role">{duts.map(d => <button type="button" role="tab" aria-selected={d.role === activeDut?.role} className={d.role === activeDut?.role ? 'selected' : ''} key={d.role} onClick={() => setSelectedRole(d.role)}>{d.role}<small>{d.members.length}</small></button>)}</div>
        {activeDut && <><div className="tw-section-head"><div><h3>DUT_{activeDut.role}_{name || 'Type'}</h3><p>{activeDut.members.length} members in this block</p></div><label className="tw-existing">Copy members from<select value="" onChange={e => { const [typeId, role] = e.target.value.split('|'); const t = types.find(item => String(item.id) === typeId); const source = t?.duts?.find((d: AnyRow) => d.role === role); if (source) replaceDuts(duts.map(d => d.role === activeDut.role ? { ...d, members: source.members.map((m: AnyRow) => ({ name: m.name || '', iec_type: m.iec_type || 'BOOL', section: m.section || 'INTERNAL', default_value: m.default_value ?? '', unit: m.unit || '', comment: m.comment || '' })) } : d)) }}><option value="">Choose a type and block</option>{types.flatMap(t => (t.duts || []).filter((d: AnyRow) => d.role === activeDut.role).map((d: AnyRow) => <option key={`${t.id}|${d.role}`} value={`${t.id}|${d.role}`}>{t.name} · {d.role}</option>))}</select></label></div>
          <WorkspacePanel id={`wizard-members-${activeDut.role}`} title={`Membres ${activeDut.role}`}><div className="tw-table-wrap"><table className="tw-table"><thead><tr>{['Order','Member','IEC type','Comment group','Default','Unit','Comment',''].map(h => <th key={h} title={({Order:'Member order is preserved in the generated declaration.',Member:'Canonical member name is the selected prefix joined to the basename.', 'IEC type':'IEC 61131-3 Structured Text data type.','Comment group':'Comment separator grouping inside this DUT, not an ST VAR_INPUT/VAR_OUTPUT section.',Default:'Initial value in the ST declaration.',Unit:'Engineering unit for this member.',Comment:'Documentation comment emitted with this member.'} as Record<string,string>)[h]}>{h}</th>)}</tr></thead><tbody>{activeDut.members.map((m, i) => { const parts = memberParts(m.name); return <tr key={i}>
            <td className="tw-order"><button type="button" aria-label={`Move ${m.name || i + 1} up`} disabled={i === 0} onClick={() => replaceDuts(duts.map(d => { if (d.role !== activeDut.role) return d; const members = [...d.members]; const [item] = members.splice(i, 1); members.splice(i - 1, 0, item); return { ...d, members } }))}>↑</button><button type="button" aria-label={`Move ${m.name || i + 1} down`} disabled={i === activeDut.members.length - 1} onClick={() => replaceDuts(duts.map(d => { if (d.role !== activeDut.role) return d; const members = [...d.members]; const [item] = members.splice(i, 1); members.splice(i + 1, 0, item); return { ...d, members } }))}>↓</button></td>
            <td><div className="tw-name-parts"><select aria-label={`Member ${i + 1} prefix`} title="Prefix is stored as part of the canonical name, PREFIX_basename." value={parts.prefix} onChange={e => e.target.value === '__add' ? void addCatalog('prefixes') : updateMemberName(i, e.target.value, parts.basename)}><option value="">No prefix</option>{prefixes.map(prefix => <option key={prefix}>{prefix}</option>)}<option value="__add">＋ Add prefix…</option></select><span>{parts.prefix ? '_' : ''}</span><input aria-label={`Member ${i + 1} basename`} value={parts.basename} onChange={e => updateMemberName(i, parts.prefix, e.target.value)}/></div></td>
            <td><select aria-label={`Member ${i + 1} IEC type`} value={IEC_TYPES.includes(m.iec_type) ? m.iec_type : 'Custom…'} onChange={e => updateMember(i, 'iec_type', e.target.value === 'Custom…' ? '' : e.target.value)}><option value="Custom…">Custom…</option>{IEC_TYPES.map(t => <option key={t}>{t}</option>)}</select>{!IEC_TYPES.includes(m.iec_type) && <input aria-label={`Member ${i + 1} custom IEC type`} value={m.iec_type} onChange={e => updateMember(i, 'iec_type', e.target.value)} placeholder="e.g. ARRAY[0..3] OF INT"/>}</td>
            {(['section','default_value','unit','comment'] as const).map(key => <td key={key}>{key === 'section' ? <select title="Comment separator grouping inside this DUT, not an ST VAR_INPUT/VAR_OUTPUT section." aria-label={`Member ${i + 1} comment group`} value={m.section || 'INTERNAL'} onChange={e => e.target.value === '__add' ? void addCatalog('sections') : updateMember(i, key, e.target.value)}>{[...new Set([...catalog.sections, m.section || 'INTERNAL'])].map(value => <option key={value}>{value}</option>)}<option value="__add">＋ Add group…</option></select> : <input title={key === 'default_value' ? 'Initial value assigned at declaration.' : key === 'unit' ? 'Engineering unit.' : 'Documentation comment for this member.'} aria-label={`Member ${i + 1} ${label(key)}`} value={m[key] == null ? '' : String(m[key])} onChange={e => updateMember(i, key, e.target.value)}/>}</td>)}
            <td><button type="button" aria-label={`Remove member ${m.name || i + 1}`} disabled={activeDut.members.length <= 1} onClick={() => replaceDuts(duts.map(d => d.role === activeDut.role ? { ...d, members: d.members.filter((_, j) => j !== i) } : d))}>×</button></td></tr> })}</tbody></table></div></WorkspacePanel>
          <button className="tw-add" type="button" onClick={() => replaceDuts(duts.map(d => d.role === activeDut.role ? { ...d, members: [...d.members, blankMember()] } : d))}>＋ Add member</button>
        </>}
        <div className="tw-processing"><WorkspacePanel id="wizard-processing" title="PROCESSING"><label>PROCESSING code <small>Optional Structured Text for this function block.</small><textarea spellCheck={false} value={processingCode} onChange={e => { setProcessingCode(e.target.value); setPreview(null); setReviewed(false) }} placeholder="(* Add Structured Text processing here *)"/></label></WorkspacePanel><WorkspacePanel id="wizard-custom" title="Code personnalisé"><label>Custom code <small>Optional custom Structured Text, preserved separately.</small><textarea spellCheck={false} value={customCode} onChange={e => { setCustomCode(e.target.value); setPreview(null); setReviewed(false) }} placeholder="Optional custom Structured Text"/></label></WorkspacePanel></div>
      </div>}

      {step === 2 && <WorkspacePanel id="wizard-review" title="Vérification du type"><div className="tw-review-step"><p className="tw-intro">Review the reusable type blocks before creating the type. Instances, addresses, communication settings, and exchanges are configured later.</p><div className="tw-summary"><div><small>TYPE</small><b>{name || '—'}</b></div><div><small>KIND</small><b>{kind}</b></div><div><small>DUT BLOCKS</small><b>{duts.length}</b></div><div><small>MEMBERS</small><b>{duts.reduce((count, dut) => count + dut.members.length, 0)}</b></div></div>
        {!preview && <button type="button" className="tw-primary" disabled={!validIdentity || busy} onClick={() => void prepare()}>{busy ? 'Checking type…' : 'Check type'}</button>}
        {error && <p className="tw-error" role="alert">{error}</p>}
        {preview && <><div className={preview.errors?.length ? 'tw-error-list' : 'tw-success'} role={preview.errors?.length ? 'alert' : 'status'}>{preview.errors?.length ? <><b>Resolve these issues before creating the type</b>{preview.errors.map((item, i) => <p key={i}>{item}</p>)}</> : 'Type is ready to create.'}</div>{preview.generated_st && <details className="tw-generated"><summary>Generated DUT declaration</summary><pre>{preview.generated_st}</pre></details>}<label className="tw-confirm"><input type="checkbox" checked={reviewed} disabled={Boolean(preview.errors?.length)} onChange={e => setReviewed(e.target.checked)}/>I reviewed the type structure and want to create it.</label></>}
      </div></WorkspacePanel>}
      {error && step !== 2 && <p className="tw-error" role="alert">{error}</p>}
    </div>
    <footer className="tw-footer"><button type="button" className="tw-secondary" disabled={busy} onClick={step === 0 ? onCancel : () => { setStep(s => Math.max(0, s - 1)); setError('') }}>{step === 0 ? 'Cancel' : 'Back'}</button><span>Step {step + 1} of {steps.length}</span>{step < 2 ? <button type="button" className="tw-primary" disabled={step === 0 && !validIdentity} onClick={next}>Continue</button> : <button type="button" className="tw-primary" disabled={!preview?.token || !reviewed || busy || Boolean(preview.errors?.length)} onClick={() => void apply()}>{busy ? 'Creating…' : 'Create type'}</button>}</footer>
  </section>
}
