/** The Generative-UI component registry (AMBIENT-SURFACES §5.1).
 *
 *  The SAME one-`register()`-call discipline as the content-type registry
 *  (`ui/content/registerBuiltins.ts`): each genui component declares its name, a
 *  typed positional-or-named arg schema, the group it belongs to, a one-line
 *  description that feeds the generated authoring prompt, and the token-driven
 *  React component that renders it. Adding a component = one `defineComponent`
 *  call beside its render fn — never a sweep.
 *
 *  Controlled rendering is the safety model (§5.2): a `<widget kind="genui">`
 *  block renders in the HOST React tree (not a sandboxed iframe) precisely
 *  *because* only registered, schema-validated components can appear. An unknown
 *  component / missing-required arg / excess arg is a typed, LLM-friendly error
 *  the renderer surfaces and DROPS — never a null hole, never a crash. Raw HTML
 *  keeps going to the iframe path; the two kinds never mix trust levels.
 *
 *  `library.prompt()` derives the authoring section mechanically from THIS
 *  registry — hand-maintained component docs are banned (they drift). */
import type { ComponentType } from 'react'

/** The type of one declared arg — drives coercion + the generated signature. */
export type GenUiArgType =
  | 'string'
  | 'number'
  | 'boolean'
  | 'string[]'
  | 'number[]'
  | 'rows' // array of arrays (a table body)
  | 'ref' // an id referencing another line (a single child)
  | 'refs' // an array of ids (children, forward refs legal)
  | 'any'

/** One declared arg of a component. `key` ORDER is the positional-arg contract. */
export interface GenUiArg {
  key: string
  type: GenUiArgType
  required?: boolean
  /** One phrase for the generated authoring prompt. */
  note?: string
}

export type GenUiGroup = 'Layout' | 'Data' | 'Charts' | 'Feedback'

/** Props every genui component receives: its validated args, plus the already-
 *  rendered children the parser resolved from `ref`/`refs`-typed args (keyed by
 *  the arg key, so a component reads `children.body` for its `body: refs` arg). */
export interface GenUiRenderProps {
  args: Record<string, unknown>
  children: Record<string, React.ReactNode>
}

/** A registered component: metadata (for validation + the prompt) + its renderer. */
export interface GenUiComponentDef {
  name: string
  group: GenUiGroup
  description: string
  args: GenUiArg[]
  component: ComponentType<GenUiRenderProps>
}

const _byName = new Map<string, GenUiComponentDef>()
/** Insertion order preserved so the authoring prompt is stable across builds. */
const _order: string[] = []

/** Register one genui component. A later registration for the same name is a
 *  clean override (mirrors `registerContentType`); shadowing at REGISTER time is
 *  what AS-6's L2 overlay will gate — the core set here never collides. */
export function defineComponent(def: GenUiComponentDef): void {
  if (!_byName.has(def.name)) _order.push(def.name)
  _byName.set(def.name, def)
}

export function getComponent(name: string): GenUiComponentDef | undefined {
  return _byName.get(name)
}

export function allComponents(): GenUiComponentDef[] {
  return _order.map((n) => _byName.get(n)!).filter(Boolean)
}

/** The typed, LLM-friendly validation verdicts (§5.2). A line whose verdict is
 *  anything but `ok` is DROPPED (never a null hole) and its error surfaced for
 *  one-shot self-correction. */
export type GenUiErrorKind = 'unknown-component' | 'missing-required' | 'excess-args'

export interface GenUiValidationError {
  kind: GenUiErrorKind
  component: string
  /** The offending arg keys (missing-required / excess-args); empty otherwise. */
  keys: string[]
  /** A one-line, model-facing correction hint. */
  message: string
}

/** Validate a raw component invocation against the registry. Returns null when
 *  the line is renderable, or a typed error when it must be dropped. Order of
 *  checks: unknown-component → missing-required → excess-args, so the most
 *  fundamental problem is the one reported. */
export function validateInvocation(
  name: string,
  argKeys: string[],
): GenUiValidationError | null {
  const def = _byName.get(name)
  if (!def) {
    const known = _order.join(', ')
    return {
      kind: 'unknown-component',
      component: name,
      keys: [],
      message: `Unknown component "${name}". Available: ${known}.`,
    }
  }
  const declared = new Set(def.args.map((a) => a.key))
  const missing = def.args.filter((a) => a.required && !argKeys.includes(a.key)).map((a) => a.key)
  if (missing.length) {
    return {
      kind: 'missing-required',
      component: name,
      keys: missing,
      message: `${name} is missing required arg(s): ${missing.join(', ')}.`,
    }
  }
  const excess = argKeys.filter((k) => !declared.has(k))
  if (excess.length) {
    return {
      kind: 'excess-args',
      component: name,
      keys: excess,
      message: `${name} got unknown arg(s): ${excess.join(', ')}. Allowed: ${[...declared].join(', ')}.`,
    }
  }
  return null
}

/** The authoring section, derived MECHANICALLY from the registry (§5.2): a
 *  per-component signature line grouped by section, so the visual-output skill and
 *  workflow node prompts embed the CURRENT set. Never hand-maintained. */
function signature(def: GenUiComponentDef): string {
  const args = def.args
    .map((a) => `${a.key}${a.required ? '' : '?'}: ${a.type}`)
    .join(', ')
  return `  ${def.name}(${args})${def.description ? ` — ${def.description}` : ''}`
}

export const library = {
  /** The mechanically-derived authoring section (component signatures grouped by
   *  section). The single source an author/model reads to know what it may emit. */
  prompt(): string {
    const groups: GenUiGroup[] = ['Layout', 'Data', 'Charts', 'Feedback']
    const lines: string[] = [
      'Generative-UI components you may emit inside a <widget kind="genui"> block.',
      'DSL: one line per component — `id = Component(key: value, …)`. Forward references are legal.',
      'Compose children with a `refs`/`ref` arg holding other line ids (e.g. children: [a, b]).',
      '',
    ]
    for (const group of groups) {
      const defs = allComponents().filter((d) => d.group === group)
      if (!defs.length) continue
      lines.push(`${group}:`)
      for (const d of defs) lines.push(signature(d))
      lines.push('')
    }
    lines.push('Example:')
    lines.push('  root = Stack(gap: "m", body: [stat, note])')
    lines.push('  stat = StatTile(label: "Revenue", value: "$1.2M", delta: 12)')
    lines.push('  note = Callout(tone: "info", text: "Up 12% vs last quarter.")')
    return lines.join('\n')
  },
}
