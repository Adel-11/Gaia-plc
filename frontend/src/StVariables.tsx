import { createContext } from 'react'
import type { VariableOption } from './FbMappingEditor'
import type { LocalVariable } from './LocalVariables'

export type StVariable = VariableOption & { internal?: boolean }
type Pin = { name: string; output: boolean }
export type LocalTypeCatalog = {
  groups: Record<string, string[]>
  blocks: Record<string, Pin[]>
  functions?: Record<string, { pins: Pin[]; result: string }>
}

export const StTypeCatalog = createContext<LocalTypeCatalog>({ groups: {}, blocks: {} })

export function blockCall(path: string, type: string, blocks: LocalTypeCatalog['blocks']): [string, string?] | undefined {
  const key = type.trim().replace(/\s*\.\s*/g, '.').toUpperCase()
  const name = Object.keys(blocks).find(name => name.toUpperCase() === key ||
    (key.startsWith('STANDARD.') && name.toUpperCase() === key.slice(9)))
  if (!name) return undefined
  return callable(path, blocks[name])
}

export function callable(path: string, parameters: Pin[], statement = true): [string, string?] {
  const pins = parameters.map(pin => {
    const placeholder = `__${pin.name}__`
    return { argument: `${pin.name} ${pin.output ? '=>' : ':='} ${placeholder}`, placeholder }
  })
  return [`${path}(${pins.map(pin => pin.argument).join(', ')})${statement ? ';' : ''}`, pins[0]?.placeholder]
}

export function visibleVariables(base: StVariable[], edits: StVariable[]): StVariable[] {
  const merged = new Map(base.map(row => [row.path.toUpperCase(), row]))
  for (const row of edits) {
    if (/^[A-Za-z_]\w*$/.test(row.path) && row.iec_type.trim()) {
      merged.set(row.path.toUpperCase(), { ...row, internal: true })
    }
  }
  return [...merged.values()]
}

export function localVariableOptions(rows: LocalVariable[]): StVariable[] {
  return rows.map(row => ({ path: row.name, iec_type: row.iec_type, internal: true }))
}

export function declarationVariables(declaration: string): StVariable[] {
  const code = declaration.replace(/\(\*[\s\S]*?\*\)|\/\/[^\r\n]*|'(?:\$[\s\S]|[^'])*'|"(?:\$[\s\S]|[^"])*"/g, match => ' '.repeat(match.length))
  const options: StVariable[] = []
  for (const section of code.matchAll(/^\s*VAR(?:_(?:INPUT|OUTPUT|IN_OUT|TEMP|EXTERNAL|STAT))?\b[^\r\n]*\r?\n([\s\S]*?)\bEND_VAR\b/gim)) {
    for (const entry of section[1].matchAll(/(?:^|;)\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*(?:AT\s+%[\w*.]+\s*)?:\s*([^;]+?)(?=\s*:=|\s*;|$)/g)) {
      for (const path of entry[1].split(',')) options.push({ path: path.trim(), iec_type: entry[2].trim(), internal: true })
    }
  }
  return options
}
