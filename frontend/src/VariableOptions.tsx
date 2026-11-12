type Variable = { path: string; iec_type: string }

export function variableFamily(path: string) {
  return path.includes('.') ? path.split('.')[0].toUpperCase() : 'VAR'
}

export default function VariableOptions({ variables }: { variables: Variable[] }) {
  const groups = new Map<string, Variable[]>()
  for (const variable of variables) {
    const family = variableFamily(variable.path)
    const options = groups.get(family) ?? []
    options.push(variable)
    groups.set(family, options)
  }
  return <>{[...groups].map(([family, options]) => <optgroup label={family} key={family} data-variable-family={family}>
    {options.map(variable => <option key={variable.path} value={variable.path} data-variable-family={family}>{variable.path} · {variable.iec_type}</option>)}
  </optgroup>)}</>
}
