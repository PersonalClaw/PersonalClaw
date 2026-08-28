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
import { LAYER_CORE, layerName, maxSurfaceLayer, type SurfaceLayer } from '../surfaces/layers'

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

export type GenUiGroup = 'Layout' | 'Data' | 'Charts' | 'Feedback' | 'Forms'

/** Group render order — also the order `library.prompt()` emits. Declared once so a
 *  new group cannot be registered-but-unprompted (a component nothing can author). */
const GROUPS: GenUiGroup[] = ['Layout', 'Data', 'Charts', 'Forms', 'Feedback']

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

/** Where a registration came from (§6). Core is the shipped set; an app registration
 *  carries the app's name so DISABLING the app removes exactly its components. */
export interface GenUiRegistration extends GenUiComponentDef {
  layer: SurfaceLayer
  /** `""` for core; the app name for an L1 registration. */
  source: string
}

const _byName = new Map<string, GenUiRegistration>()
/** Insertion order preserved so the authoring prompt is stable across builds. */
const _order: string[] = []

/** Register one CORE (L0) genui component. A later core registration for the same
 *  name is a clean override (mirrors `registerContentType`) — the shipped set is
 *  authored in one file and cannot collide with itself by accident. Higher layers do
 *  NOT come through here: they use `registerLayerComponent`, which refuses to shadow. */
export function defineComponent(def: GenUiComponentDef): void {
  if (!_byName.has(def.name)) _order.push(def.name)
  _byName.set(def.name, { ...def, layer: LAYER_CORE, source: '' })
}

/** Why a layered registration was refused. */
export type GenUiRegisterRefusal =
  | { ok: false; code: 'shadows-core'; message: string }
  | { ok: false; code: 'shadows-layer'; message: string }
  | { ok: false; code: 'layer-disabled'; message: string }
  | { ok: false; code: 'invalid'; message: string }

export type GenUiRegisterResult = { ok: true } | GenUiRegisterRefusal

/** Register a component from a layer ABOVE core (an app's L1 module, a user/agent L2
 *  overlay). Registrations COMPOSE: a higher layer may ADD a name, never SHADOW one a
 *  lower layer already owns (§6).
 *
 *  Refusing at REGISTER time — not at render time — is what makes the safety model
 *  hold: the reason a genui block may render in the host React tree at all is that
 *  only registered, schema-validated components can appear in it, and a model writing
 *  `StatTile(…)` in a chat transcript must reach the CORE StatTile. If an app could
 *  take that name, model-authored text would be selecting app code, which is a new
 *  trust edge nobody consented to. (This is the "registration reading" the
 *  APP-PLATFORM-EVOLUTION APE-11 scope call deferred to a separate atom with its own
 *  threat argument; the argument is: additive names only, never core names, gated on
 *  the app being installed AND enabled, args still validated by the HOST schema, and
 *  removed the moment the app is disabled.)
 *
 *  Safe mode (`maxSurfaceLayer() === 0`) refuses every layered registration, so the
 *  recovery route resolves nothing but L0 even if a module somehow loaded. */
export function registerLayerComponent(
  def: GenUiComponentDef,
  opts: { layer: SurfaceLayer; source: string },
): GenUiRegisterResult {
  const name = (def.name || '').trim()
  if (!name || !def.component) {
    return { ok: false, code: 'invalid', message: 'A genui component needs a name and a renderer.' }
  }
  if (opts.layer <= LAYER_CORE) {
    return { ok: false, code: 'invalid', message: 'Core (L0) components are registered by the shipped bundle, not by a layer.' }
  }
  if (opts.layer > maxSurfaceLayer()) {
    return {
      ok: false,
      code: 'layer-disabled',
      message: `Safe mode is on — ${layerName(opts.layer)}-layer component "${name}" was not registered.`,
    }
  }
  const existing = _byName.get(name)
  if (existing && existing.layer <= LAYER_CORE) {
    return {
      ok: false,
      code: 'shadows-core',
      message: `"${name}" is a core component — an ${layerName(opts.layer)} layer may add components, never shadow core ones.`,
    }
  }
  if (existing && existing.layer <= opts.layer && existing.source !== opts.source) {
    return {
      ok: false,
      code: 'shadows-layer',
      message: `"${name}" is already registered by ${existing.source || layerName(existing.layer)}.`,
    }
  }
  if (!_byName.has(name)) _order.push(name)
  _byName.set(name, { ...def, name, layer: opts.layer, source: opts.source })
  return { ok: true }
}

/** Drop every component a `source` registered — the app-disable path (§6: an L1
 *  contribution is "removable by disabling the app"). Returns how many went, so a
 *  caller can log a real number instead of assuming the removal happened. */
export function removeComponentsFrom(source: string): number {
  if (!source) return 0
  let removed = 0
  for (const [name, reg] of [..._byName]) {
    if (reg.source !== source) continue
    _byName.delete(name)
    const i = _order.indexOf(name)
    if (i >= 0) _order.splice(i, 1)
    removed += 1
  }
  return removed
}

/** Which layer owns `name` today, or null when nothing does. */
export function componentLayer(name: string): SurfaceLayer | null {
  return _byName.get(name)?.layer ?? null
}

export function getComponent(name: string): GenUiRegistration | undefined {
  return _byName.get(name)
}

export function allComponents(): GenUiRegistration[] {
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
      // The audience here is a MODEL (§5.2 — surfaced for one-shot self-correction), not a reader,
      // so this is the least user-facing member of the family. Converted anyway: `missing.length` is
      // already in hand, the two rails match on `'missing required arg'` as a SUBSTRING so both
      // forms satisfy them, and a verdict that models its own grammar is a better example for the
      // thing reading it.
      message: `${name} is missing required arg${missing.length === 1 ? '' : 's'}: ${missing.join(', ')}.`,
    }
  }
  const excess = argKeys.filter((k) => !declared.has(k))
  if (excess.length) {
    return {
      kind: 'excess-args',
      component: name,
      keys: excess,
      message: `${name} got unknown arg${excess.length === 1 ? '' : 's'}: ${excess.join(', ')}. Allowed: ${[...declared].join(', ')}.`,
    }
  }
  return null
}

/** The authoring section, derived MECHANICALLY from the registry (§5.2): a
 *  per-component signature line grouped by section, so the visual-output skill and
 *  workflow node prompts embed the CURRENT set. Never hand-maintained. */
function signature(def: GenUiRegistration): string {
  const args = def.args
    .map((a) => `${a.key}${a.required ? '' : '?'}: ${a.type}`)
    .join(', ')
  // An app-contributed component is NAMED as such in the prompt: a model choosing it
  // should know the component is only present while that app is enabled.
  const from = def.layer > LAYER_CORE && def.source ? ` [from the ${def.source} app]` : ''
  return `  ${def.name}(${args})${def.description ? ` — ${def.description}` : ''}${from}`
}

export const library = {
  /** The mechanically-derived authoring section (component signatures grouped by
   *  section). The single source an author/model reads to know what it may emit. */
  prompt(): string {
    const groups: GenUiGroup[] = GROUPS
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
    lines.push('Actions: a Forms component sends its `label`/`submit` text as the visible message and')
    lines.push('its collected values as the machine payload — so write a label a human reads, not a code.')
    lines.push('')
    lines.push('Example:')
    lines.push('  root = Stack(gap: "m", body: [stat, note, ask])')
    lines.push('  stat = StatTile(label: "Revenue", value: "$1.2M", delta: 12)')
    lines.push('  note = Callout(tone: "info", text: "Up 12% vs last quarter.")')
    lines.push('  ask = Form(title: "Log an expense", fields: ["amount", "vendor"], action: "log_expense", submit: "Log expense")')
    return lines.join('\n')
  },
}
