import { useId, useState, type ReactNode } from 'react'
import { Toggle } from '../../ui/Toggle'

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

export function typeLabel(s: JsonSchema): string {
  const t = Array.isArray(s.type) ? s.type.join('|') : s.type
  if (t === 'array') return `${(s.items?.type as string) ?? 'any'}[]`
  if (s.enum) return 'enum'
  return t ?? 'any'
}

/** Seed an arguments object from a schema's defaults (so the run form starts
 *  populated and the user can edit). */
export function seedArgs(parameters: unknown): Record<string, unknown> {
  const { props } = schemaProps(parameters)
  const out: Record<string, unknown> = {}
  for (const [k, s] of props) {
    if (s.default !== undefined) out[k] = s.default
    else if (s.enum?.length) out[k] = ''
    else if (s.type === 'boolean') out[k] = false
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
  const base = 'w-full rounded-md bg-surface px-m py-2 text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'
  let control: ReactNode
  const customWidget = meta.widget ? widgets?.[meta.widget] : undefined
  if (customWidget) {
    // Custom widgets render their own control; name them via aria-labelledby to
    // the visible label span (id below). The widget renderer forwards no id, so
    // we wrap it in a labelled group.
    control = <div role="group" aria-labelledby={`${id}-label`}>{customWidget({ value, onChange, schema, placeholder: meta.help })}</div>
  } else if (schema.enum?.length) {
    control = (
      <select id={id} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} className={`${base} [color-scheme:dark]`}>
        <option value="">—</option>
        {schema.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    )
  } else if (t === 'boolean') {
    control = <Toggle on={!!value} onChange={onChange} size="sm" label={label} />
  } else if (t === 'number' || t === 'integer') {
    control = <input id={id} type="number" value={value === '' || value == null ? '' : Number(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} className={base} />
  } else if (t === 'object' || t === 'array') {
    control = <textarea id={id} value={typeof value === 'string' ? value : JSON.stringify(value ?? (t === 'array' ? [] : {}), null, 2)} onChange={(e) => onChange(e.target.value)} rows={3} placeholder={t === 'array' ? '[ … ]' : '{ … }'} className={`${base} font-mono text-[0.75rem] resize-y`} />
  } else {
    control = <input id={id} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} placeholder={meta.help?.slice(0, 60) ?? schema.description?.slice(0, 60)} className={base} />
  }
  // The boolean Toggle carries its own aria-label; everything else binds the
  // <label> to the control by id (htmlFor). A plain-label span id lets custom
  // widgets reference it via aria-labelledby.
  const bindsHtmlFor = !customWidget && t !== 'boolean'
  return (
    <div>
      <div className="mb-1 flex items-center gap-s">
        <label id={`${id}-label`} htmlFor={bindsHtmlFor ? id : undefined} className="text-on-surface text-[0.8125rem]">{label}</label>
        <span className="text-on-surface-low text-[0.7rem] font-mono">{typeLabel(schema)}</span>
        {required && <span className="text-danger text-[0.7rem]">required</span>}
      </div>
      {control}
      {meta.help && <p className="mt-1 text-on-surface-low text-[0.7rem]">{meta.help}</p>}
    </div>
  )
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
