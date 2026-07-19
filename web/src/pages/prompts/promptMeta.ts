import type { PromptVarType, PromptVariable } from '../../lib/api'

// ── typed variable kinds (mirror prompt_providers/base.py VariableType) ──
export interface VarTypeMeta { key: PromptVarType; label: string }
export const VAR_TYPES: VarTypeMeta[] = [
  { key: 'text', label: 'Text (line)' },
  { key: 'textarea', label: 'Text (block)' },
  { key: 'number', label: 'Number' },
  { key: 'boolean', label: 'Yes / no' },
  { key: 'select', label: 'Choice' },
]

// ── source chip tone (user editable vs bundled/marketplace read-only) ──
export function isReadOnly(source?: string): boolean {
  return !!source && source !== 'user'
}
export function sourceTone(source?: string): string {
  if (!source || source === 'user') return 'var(--color-primary)'
  if (source === 'marketplace') return 'var(--color-info)'
  return 'var(--color-on-surface-low)'  // bundled / provider
}
export function sourceLabel(source?: string): string {
  return !source || source === 'user' ? 'user' : source
}

/** Variables a prompt declares (the typed `variables` list). */
export function promptVars(p: { variables?: PromptVariable[] }): PromptVariable[] {
  return p.variables ?? []
}

/** Extract {{placeholder}} names from a template body (deduped, in order).
 *  Excludes {{> snippet}} includes (those are surfaced via detectIncludes). */
export function detectPlaceholders(content: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  const re = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) !== null) {
    if (!seen.has(m[1])) { seen.add(m[1]); out.push(m[1]) }
  }
  return out
}

/** Extract {{> snippet-name}} include targets from a body (deduped, in order). */
export function detectIncludes(content: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  const re = /\{\{>\s*([a-zA-Z0-9_-]+)\s*\}\}/g
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) !== null) {
    if (!seen.has(m[1])) { seen.add(m[1]); out.push(m[1]) }
  }
  return out
}

/** Seed a render-input map from a prompt's variables (uses defaults). */
export function seedRenderValues(vars: PromptVariable[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const v of vars) {
    if (v.default !== undefined && v.default !== null) out[v.name] = v.default
    else if (v.type === 'boolean') out[v.name] = false
    else out[v.name] = ''
  }
  return out
}
