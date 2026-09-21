import { Fragment, useId, useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { Toggle } from '../../ui/Toggle'
import { QuietButton } from '../../ui/QuietButton'

/** Minimal JSON-Schema helpers for the tool inspector. A tool's `parameters` is
 *  a JSON Schema object ({type:'object', properties, required}); we render its
 *  top-level properties as a signature (view) and as an input form (run). */

/** Optional presentation metadata a provider attaches to a schema property.
 *  ``widget`` drives WHICH control renders (beyond the JSON type) — e.g. a
 *  ``prompt`` widget renders a saved-prompt picker for a string field; ``label``
 *  / ``help`` give a human label + hint; ``tags`` (e.g. ["advanced"]) classify
 *  the field. Carried under the JSON-Schema ``x-meta`` extension key. */
export interface SchemaMeta {
  label?: string
  help?: string
  widget?: string
  tags?: string[]
  sensitive?: boolean
}

export interface JsonSchema {
  type?: string | string[]
  description?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  enum?: unknown[]
  default?: unknown
  'x-meta'?: SchemaMeta
}

/** The presentation metadata for a property, or an empty object. */
export function schemaMeta(s: JsonSchema): SchemaMeta {
  return (s['x-meta'] ?? {}) as SchemaMeta
}

export function schemaProps(parameters: unknown): { props: [string, JsonSchema][]; required: Set<string> } {
  const s = (parameters ?? {}) as JsonSchema
  const props = Object.entries(s.properties ?? {})
  return { props, required: new Set(s.required ?? []) }
}

/** Whether a field carries a configured value beyond its schema default.
 *
 *  The Advanced disclosure uses this for its live "N set" marker. Defaults do not count: a form
 *  that seeds `provider: "native"` from the schema has not been customized. A false/zero value DOES
 *  count when there is no matching default — both are real settings, not empty sentinels. */
function schemaValueIsSet(value: unknown, schema: JsonSchema): boolean {
  if (value === undefined || value === null || value === '') return false
  if (schema.default === undefined) return true
  if (Object.is(value, schema.default)) return false
  try {
    return JSON.stringify(value) !== JSON.stringify(schema.default)
  } catch {
    return true
  }
}

/** One field-list policy for every JSON-Schema form.
 *
 *  Optional properties tagged `advanced` sit behind an accessible disclosure; required fields stay
 *  visible even when tagged. If EVERY field would be hidden, the list stays flat — a disclosure
 *  ranks secondary fields but must never erase the whole form. The trigger, tool, workflow, model
 *  and app-config surfaces all render through this component while retaining their own field
 *  controls via `renderField`.
 *
 *  The disclosure states both how many fields it hides and, when applicable, how many already carry
 *  configured values. `configured` names write-only values (stored secrets) whose blank form value
 *  would otherwise make that marker lie. */
export function SchemaFields<S extends JsonSchema>({
  fields,
  required,
  values = {},
  configured = [],
  advancedFieldClassName = 'flex flex-col gap-m',
  renderField,
}: {
  fields: [string, S][]
  required: readonly string[] | ReadonlySet<string>
  values?: Record<string, unknown>
  configured?: readonly string[] | ReadonlySet<string>
  advancedFieldClassName?: string
  renderField: (name: string, schema: S, required: boolean) => ReactNode
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const requiredSet = new Set(required)
  const configuredSet = new Set(configured)
  const isAdvanced = ([name, schema]: [string, S]) =>
    !requiredSet.has(name) && schemaMeta(schema).tags?.includes('advanced')

  // With nothing left to rank, keep the whole form visible instead of rendering an empty surface
  // above a disclosure. `native-tasks` is the shipped one-field example of this shape.
  const allAdvanced = fields.length > 0 && fields.every(isAdvanced)
  const advancedFields = allAdvanced ? [] : fields.filter(isAdvanced)
  const visibleFields = allAdvanced ? fields : fields.filter((field) => !isAdvanced(field))
  const setCount = advancedFields.filter(([name, schema]) =>
    configuredSet.has(name) || schemaValueIsSet(values[name], schema)).length

  const render = ([name, schema]: [string, S]) => (
    <Fragment key={name}>{renderField(name, schema, requiredSet.has(name))}</Fragment>
  )

  return (
    <>
      {visibleFields.map(render)}
      {advancedFields.length > 0 && (
        <div className="border-t border-outline-variant/40 pt-m">
          <QuietButton ariaExpanded={advancedOpen} onClick={() => setAdvancedOpen((open) => !open)}>
            <ChevronDown
              size={14}
              aria-hidden
              className={`transition-transform ${advancedOpen ? 'rotate-180' : ''}`}
            />
            Advanced ({advancedFields.length}{setCount > 0 ? `, ${setCount} set` : ''})
          </QuietButton>
          {advancedOpen && (
            <div className={`mt-m ${advancedFieldClassName}`}>
              {advancedFields.map(render)}
            </div>
          )}
        </div>
      )}
    </>
  )
}

/** Required keys that carry no usable value, in the order given.
 *
 *  Lives here beside `schemaProps` (which reads the `required` array) so every schema-driven form
 *  shares ONE emptiness rule. Getting this wrong is silent: a bare `!value` counts a legitimate
 *  `false` or `0` as missing and blocks a save the user cannot explain.
 *
 *  - `undefined` / `null` / a blank-or-whitespace string are missing.
 *  - `false` and `0` are PRESENT — values a schema can legitimately require.
 *  - `satisfied` names keys some other mechanism already fills, so a blank input is fine. The only
 *    case today is a write-only sensitive field whose secret is already stored: the backend never
 *    sends it back, the input deliberately starts blank, and blank means "keep the stored secret"
 *    (#43). Counting it missing would make such a form permanently unsavable.
 *
 *  Deliberately STRICTER than the backend, which checks presence only (`key not in values`), so a
 *  blank string satisfies it. A schema author marking a field required does not mean "may be
 *  empty", and a user reads a blank box as "not filled in" — so the form refuses it rather than
 *  posting a value the author never intended. The asymmetry is safe in this direction: it can only
 *  prevent a save, never permit one the server would reject. */
export function missingRequired(
  values: Record<string, unknown>,
  required: readonly string[] | ReadonlySet<string>,
  opts?: { satisfied?: readonly string[] },
): string[] {
  const satisfied = new Set(opts?.satisfied ?? [])
  const out: string[] = []
  for (const key of required) {
    if (satisfied.has(key)) continue
    const v = values[key]
    if (v === undefined || v === null) { out.push(key); continue }
    if (typeof v === 'string' && v.trim() === '') out.push(key)
  }
  return out
}

export function typeLabel(s: JsonSchema): string {
  const t = Array.isArray(s.type) ? s.type.join('|') : s.type
  if (t === 'array') return `${(s.items?.type as string) ?? 'any'}[]`
  if (s.enum) return 'enum'
  return t ?? 'any'
}

/** Seed an arguments object from a schema's defaults (so the run form starts
 *  populated and the user can edit).
 *
 *  An OPTIONAL boolean with no declared `default` is seeded to `''` — the same "unset"
 *  sentinel every other type uses — NOT to `false`. Seeding `false` put `false` on the
 *  wire for a switch the user never touched, which OVERRODE the provider's own default:
 *  `run-prompt-action`'s `dry_run` is exactly this shape (a boolean with no schema
 *  `default`, whose real default lives in the provider). A toggle nobody looked at must
 *  not out-vote the provider. Flipping it on — or on and back off — still sends an
 *  explicit `true`/`false`, because `buildArgs` drops only `''` and `null`.
 *
 *  A REQUIRED boolean keeps `false`: `buildArgs` never drops a required key, so the form
 *  must hold a real boolean for it, and `false` is the honest reading of a Toggle that
 *  renders OFF. */
export function seedArgs(parameters: unknown): Record<string, unknown> {
  const { props, required } = schemaProps(parameters)
  const out: Record<string, unknown> = {}
  for (const [k, s] of props) {
    if (s.default !== undefined) out[k] = s.default
    else if (s.enum?.length) out[k] = ''
    else if (s.type === 'boolean') out[k] = required.has(k) ? false : ''
    else out[k] = ''
  }
  return out
}

/** A custom-widget renderer keyed by ``x-meta.widget``. Lets a caller supply
 *  richer controls (e.g. a saved-prompt picker) without this module depending on
 *  feature APIs — the metadata names the widget, the caller provides it. */
export type WidgetRenderer = (props: {
  value: unknown; onChange: (v: unknown) => void; schema: JsonSchema; placeholder?: string
}) => ReactNode
export type WidgetMap = Record<string, WidgetRenderer>

/** One input control per JSON-Schema property. A provider can drive the control
 *  via ``x-meta`` (``label``/``help`` for presentation, ``widget`` for a custom
 *  control from ``widgets``); otherwise the JSON type picks it (enum → select,
 *  boolean → toggle, number/integer → number, object/array → JSON textarea). */
export function SchemaField({ name, schema, required, value, onChange, widgets }: {
  name: string; schema: JsonSchema; required: boolean; value: unknown; onChange: (v: unknown) => void
  widgets?: WidgetMap
}) {
  const t = Array.isArray(schema.type) ? schema.type[0] : schema.type
  const meta = schemaMeta(schema)
  const label = meta.label ?? name
  // Associate the visible label with the control for screen readers: native
  // inputs/selects/textareas get `id` + a <label htmlFor>; the boolean Toggle and
  // custom widgets (which own their own element) take an accessible name instead.
  const id = useId()
  // The type SIZE left this const deliberately: a `data-type` role is an ATTRIBUTE, so it cannot
  // ride a className string — each control below declares its own role instead (`body-s` for the
  // one-line controls, `caption` for the JSON textarea, which already sized itself smaller).
  const base = 'w-full rounded-md bg-surface px-m py-s text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  let control: ReactNode
  const customWidget = meta.widget ? widgets?.[meta.widget] : undefined
  if (customWidget) {
    // Custom widgets render their own control; name them via aria-labelledby to
    // the visible label span (id below). The widget renderer forwards no id, so
    // we wrap it in a labelled group.
    control = <div role="group" aria-labelledby={`${id}-label`}>{customWidget({ value, onChange, schema, placeholder: meta.help })}</div>
  } else if (schema.enum?.length) {
    control = (
      <select id={id} data-type="body-s" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} className={`${base}`}>
        <option value="">—</option>
        {schema.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    )
  } else if (t === 'boolean') {
    control = <Toggle on={!!value} onChange={onChange} size="sm" label={label} />
  } else if (t === 'number' || t === 'integer') {
    control = <input id={id} data-type="body-s" type="number" value={value === '' || value == null ? '' : Number(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} className={base} />
  } else if (t === 'object' || t === 'array') {
    control = <textarea id={id} data-type="caption" value={typeof value === 'string' ? value : JSON.stringify(value ?? (t === 'array' ? [] : {}), null, 2)} onChange={(e) => onChange(e.target.value)} rows={3} placeholder={t === 'array' ? '[ … ]' : '{ … }'} className={`${base} font-mono resize-y`} />
  } else {
    // `meta.help` is NOT the placeholder: it is already rendered under the control below, and
    // using it for both printed the same sentence twice — visible the moment a form whose fields
    // all carry `help` came through here (the workflow launch form, #327). `description` is the
    // fallback because a JSON Schema's `description` has no other slot in this layout.
    control = <input id={id} data-type="body-s" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} placeholder={meta.help ? undefined : schema.description?.slice(0, 60)} className={base} />
  }
  // The boolean Toggle carries its own aria-label; everything else binds the
  // <label> to the control by id (htmlFor). A plain-label span id lets custom
  // widgets reference it via aria-labelledby.
  const bindsHtmlFor = !customWidget && t !== 'boolean'
  return (
    <div>
      <div className="mb-xs flex items-center gap-s">
        <label id={`${id}-label`} htmlFor={bindsHtmlFor ? id : undefined} data-type="body-s" className="text-on-surface">{label}</label>
        <span data-type="caption" className="text-on-surface-low font-mono">{typeLabel(schema)}</span>
        {required && <span data-type="caption" className="text-danger">required</span>}
      </div>
      {control}
      {meta.help && <p data-type="caption" className="mt-xs text-on-surface-low">{meta.help}</p>}
    </div>
  )
}

/** The spellings a declared `boolean` accepts from a text field, and the ONE list in `web/` that
 *  says so. The backend's `safety_flags.BOOL_TRUE_WORDS` / `BOOL_FALSE_WORDS` are the same two sets
 *  and are the authority — a value this list rejects is sent to the API unchanged, so the door
 *  reports it rather than the client guessing. Keep them in step if either grows a word. */
export const BOOL_TRUE_WORDS = ['true', 'yes', 'on', '1', 'y']
export const BOOL_FALSE_WORDS = ['false', 'no', 'off', '0', 'n']

/** One declared parameter in a shape that is NOT JSON Schema — a flat `{type, required, default,
 *  help}` record, keyed by name. A workflow definition's `inputs` block is the one in the product;
 *  it is declared structurally here so this module keeps depending on nothing but its own types. */
export interface DeclaredParam {
  type?: string
  required?: boolean
  default?: unknown
  help?: string
}

/** A flat declared-parameter map, as the JSON Schema this module's renderer speaks.
 *
 *  The adapter exists so there is ONE schema-driven renderer, not two. The workflow launch form
 *  had its own: every declared input rendered as a free-text box whatever its type, so a `boolean`
 *  and a `number` took arbitrary strings and posted them verbatim (#327) — while the tool inspector
 *  and the trigger action form, both driven by `SchemaField` below, got a toggle and a spinbutton
 *  from the same information. A second renderer is how one surface ends up years behind another;
 *  a shape adapter is six lines and cannot drift. */
export function declaredInputsSchema(declared: Record<string, DeclaredParam> | undefined): JsonSchema {
  const properties: Record<string, JsonSchema> = {}
  const required: string[] = []
  for (const [name, raw] of Object.entries(declared ?? {})) {
    const meta = raw ?? {}
    // `?? undefined` rather than `||`: `false` and `0` are legitimate defaults, and a declared
    // `null` means "no default" — which is what `seedArgs` reads an absent key as.
    properties[name] = {
      type: meta.type || 'string',
      default: meta.default ?? undefined,
      ...(meta.help ? { 'x-meta': { help: meta.help } } : {}),
    }
    if (meta.required) required.push(name)
  }
  return { type: 'object', properties, required }
}

/** Coerce form values for invoke: parse object/array JSON, drop empty optionals. */
export function buildArgs(parameters: unknown, raw: Record<string, unknown>): { args: Record<string, unknown>; error?: string } {
  const { props, required } = schemaProps(parameters)
  const args: Record<string, unknown> = {}
  for (const [k, s] of props) {
    const v = raw[k]
    const t = Array.isArray(s.type) ? s.type[0] : s.type
    if ((v === '' || v == null) && !required.has(k)) continue
    if (t === 'object' || t === 'array') {
      if (typeof v === 'string' && v.trim()) {
        try { args[k] = JSON.parse(v) } catch { return { args, error: `${k}: invalid JSON` } }
      } else if (typeof v !== 'string') args[k] = v
    } else {
      args[k] = v
    }
  }
  return { args }
}

export function useArgs(parameters: unknown) {
  return useState<Record<string, unknown>>(() => seedArgs(parameters))
}
