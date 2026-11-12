export type CopyReviewGroup = {
  instance: string
  members: { path: string; type: string }[]
  rows: { key: string; endpoints: string[]; automatic: string; member: string; choices: string[]; type: string }[]
}

export default function GfcCopyReview({ block, groups, choices, disabled, onChange }: {
  block: string
  groups: CopyReviewGroup[]
  choices: Record<string, string>
  disabled: boolean
  onChange: (key: string, target: string) => void
}) {
  const inbound = block.endsWith('_IN')
  return <section className="code-copy-review" aria-label="Recopies par instance">
    <h3>Recopies par instance</h3>
    {block === 'GFC_EXC' && <p>Vérifie les appels. Les liaisons à l’intérieur d’une fonction se règlent dans Échanges.</p>}
    {groups.map(group => {
      const mapped = new Set(group.rows.map(row => choices[row.key] ?? row.member))
      const remaining = group.members.filter(member => !mapped.has(member.path))
      return <details key={group.instance} open={groups.length < 4}>
        <summary>{group.instance} · {group.rows.length} {block === 'GFC_EXC' ? 'appel(s)' : 'recopie(s)'}</summary>
        {!!group.rows.length && <div className="code-copy-table"><table>
          <thead><tr><th>{inbound ? 'Source' : 'Destination'}</th><th>Variable d’instance</th><th>Type</th></tr></thead>
          <tbody>{group.rows.map(row => <tr key={row.key}>
            <td>{row.endpoints.map((endpoint, index) => <code key={index}>{endpoint}</code>)}</td>
            <td><select aria-label={`Variable pour ${row.endpoints.join(' / ')}`} value={choices[row.key] ?? row.member}
              disabled={disabled || row.choices.length < 2} onChange={event => onChange(row.key, event.target.value)}>
              {row.choices.map(path => <option value={path} key={path}>{path}{path === row.automatic ? ' (automatique)' : ''}</option>)}
            </select></td><td>{row.type}</td>
          </tr>)}</tbody>
        </table></div>}
        {!!remaining.length && <details><summary>{remaining.length} variable(s) sans recopie modifiable dans ce GFC</summary>
          <ul>{remaining.map(member => <li key={member.path}><code>{member.path}</code> · {member.type}</li>)}</ul>
        </details>}
      </details>
    })}
  </section>
}
