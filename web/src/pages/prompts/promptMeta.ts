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
/** What the source pill SAYS. A shipped prompt is seeded to disk as an editable
 *  native prompt, and the native provider stamps every on-disk prompt
 *  `source = 'user'` — that is what keeps it editable, so the tone and
 *  `isReadOnly` above are right to treat it as the user's. The LABEL was not:
 *  it told you that you wrote PersonalClaw's own shipped prompt, while the tag
 *  chips on the same panel said `bundled`. Provenance survives in the tags, so
 *  the label reads it from there and leaves editability alone. */
export function sourceLabel(source?: string, tags?: string[]): string {
  if ((!source || source === 'user') && (tags ?? []).includes('bundled')) return 'bundled'
  return !source || source === 'user' ? 'user' : source
}

/** Variables a prompt declares (the typed `variables` list). */
export function promptVars(p: { variables?: PromptVariable[] }): PromptVariable[] {
  return p.variables ?? []
}

// The engine's canonical type names + the legacy aliases it accepts — mirrors
// prompt_providers/engine.py's alias table so the authoring UI reads the SAME
// grammar the renderer does (issue 596: the old name-only regex was blind to every
// inline typed declaration, so the two extractors returned disjoint results on the
// same input).
const TYPE_ALIASES: Record<string, PromptVarType> = {
  string: 'text', str: 'text', text: 'text',
  longtext: 'textarea', long_text: 'textarea', multiline: 'textarea', textarea: 'textarea',
  numeric: 'number', integer: 'number', int: 'number', float: 'number', number: 'number',
  bool: 'boolean', boolean: 'boolean',
  select: 'select', multiselect: 'select', enum: 'select',
}

/** Mirror of the engine's parse_type_decl: `text`, `number`, `select::[a, b]`, or a
 *  bare `[a, b]` (→ select). Unknown types fall back to text; options imply select. */
function parseTypeDecl(suffix: string): { type: PromptVarType; options: string[] } {
  const s = suffix.trim()
  let options: string[] = []
  let typePart = s
  const bracket = /\[(.*)\]/.exec(s)
  if (bracket) {
    options = bracket[1].split(',').map((o) => o.trim()).filter(Boolean)
    typePart = s.slice(0, bracket.index).replace(/[:\s]+$/, '').trim()
  }
  let type = typePart ? TYPE_ALIASES[typePart.toLowerCase()] : undefined
  if (options.length && !type) type = 'select'
  return { type: type ?? 'text', options }
}

/** Every user-fillable variable a template body declares — bare `{{ name }}` AND
 *  the engine's inline typed forms (`{{ name::type }}`, `{{ name::select::[a, b] }}`,
 *  `{{ name::[a, b] }}`) — deduped, in first-appearance order, with the declared
 *  type/options carried through so an "add variable" affordance can create exactly
 *  the row the declaration asks for. Excludes {{> snippet}} includes (surfaced via
 *  detectIncludes), dotted paths (not user-fillable, matching the engine), and
 *  function calls / expressions (no bare-name or declaration match). */
export function detectVariables(content: string): PromptVariable[] {
  const out: PromptVariable[] = []
  const seen = new Set<string>()
  const expr = /\{\{\s*([\s\S]*?)\s*\}\}/g
  let m: RegExpExecArray | null
  while ((m = expr.exec(content)) !== null) {
    const body = m[1].trim()
    if (body.startsWith('>')) continue
    const decl = /^([a-zA-Z_][\w.]*)\s*::\s*([\s\S]+)$/.exec(body)
    if (decl) {
      const name = decl[1]
      if (name.includes('.') || seen.has(name)) continue
      seen.add(name)
      const { type, options } = parseTypeDecl(decl[2])
      out.push({ name, type, ...(options.length ? { options } : {}) })
      continue
    }
    const bare = /^([a-zA-Z_][a-zA-Z0-9_]*)$/.exec(body)
    if (bare && !seen.has(bare[1])) { seen.add(bare[1]); out.push({ name: bare[1], type: 'text' }) }
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
