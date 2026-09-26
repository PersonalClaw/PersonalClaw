import { useEffect, useId, useState } from 'react'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import { api, type ProviderSchema, type ProviderSchemaProp } from '../../lib/api'
// The SAME serialize/parse pair the Apps Configure dialog uses for structured fields —
// imported rather than reimplemented, so the two schema-driven forms cannot disagree about
// what a valid array/object entry is (they already disagreed about secrets).
import { parseJsonField, serializeJsonField } from '../apps/appConfigForm'
import { Button } from '../../ui/Button'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Toggle } from '../../ui/Toggle'
import { Select, TextArea, TextInput } from '../../ui/forms'
import { SavedToast } from './settingsUI'

/** Seed {key: default} from a schema's properties so a created instance submits
 *  the same defaults the form shows (else a field with a `default` renders but
 *  isn't sent, failing schema validation — looks like "the button does nothing"). */
export function schemaDefaults(schema: ProviderSchema | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, p] of Object.entries(schema?.properties ?? {})) {
    if (p && p.default !== undefined) out[k] = p.default
  }
  return out
}

/** Renders a provider's settingsSchema (JSON-Schema + x-meta) as an editable
 *  form and saves via PATCH /api/providers/{name}/config. Lives under a
 *  provider's toggle — only mounted when the provider is enabled + has a schema.
 *  `onSaved` runs after a save the gateway accepted: the save rebuilt the provider. */
export function ProviderConfigForm({ name, onSaved }: { name: string; onSaved?: () => void }) {
  const [schema, setSchema] = useState<ProviderSchema | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [secretSet, setSecretSet] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    Promise.all([api.providerSchema(name), api.providerConfig(name)])
      .then(([s, c]) => {
        if (!live) return
        setSchema(s)
        // A sensitive field with a stored secret arrives MASKED (write-only over the
        // API). Start its input BLANK rather than pre-filled with the mask: editing dots
        // is nonsense, and a blank submit means "keep the stored secret" — the same
        // treatment the Apps Configure dialog already gives its own secrets (#43).
        const set = c._secret_set ?? []
        const next = { ...(c.config ?? {}) }
        for (const k of set) next[k] = ''
        setSecretSet(set)
        setValues(next)
      })
      .catch(() => { if (live) setSchema({ properties: {} }) })
    return () => { live = false }
  }, [name])

  if (!schema) return <div data-type="caption" className="py-2 text-on-surface-low"><Loader2 size={12} className="inline animate-spin" /> Loading config…</div>
  const props = Object.entries(schema.properties ?? {})
  if (props.length === 0) return null

  const set = (k: string, v: unknown) => { setValues((p) => ({ ...p, [k]: v })); setDirty(true); setSaved(false); setErr('') }
  const save = async () => {
    setSaving(true); setErr('')
    try { await api.saveProviderConfig(name, values); setDirty(false); setSaved(true); setTimeout(() => setSaved(false), 2000); onSaved?.() }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Save failed'
      try { const p = JSON.parse(msg); msg = p.error + (p.details ? `: ${p.details.join('; ')}` : '') } catch { /* raw */ }
      setErr(msg)
    }
    setSaving(false)
  }

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-outline-variant/30 pt-3">
      {props.map(([key, prop]) => (
        <SchemaField key={key} fieldKey={key} prop={prop} value={values[key]}
          secretAlreadySet={secretSet.includes(key)} onChange={(v) => set(key, v)} />
      ))}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} loading={saving} disabled={!dirty || saving} disabledReason={!dirty && !saving ? 'No changes to save' : undefined}>Save</Button>
        <SavedToast show={saved} />
        {dirty && !saved && <span data-type="caption" className="text-on-surface-low">Unsaved changes</span>}
        {err && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      </div>
    </div>
  )
}

export function SchemaField({ fieldKey, prop, value, onChange, secretAlreadySet = false }: {
  fieldKey: string; prop: ProviderSchemaProp; value: unknown; onChange: (v: unknown) => void
  /** This sensitive field already holds a stored secret the API withholds. The input is
   *  blank by design, so it says "saved — leave blank to keep" instead of looking unset. */
  secretAlreadySet?: boolean
}) {
  const meta = prop['x-meta'] ?? {}
  const label = meta.label ?? fieldKey
  const [showSecret, setShowSecret] = useState(false)
  // Associate the visible label with the control for screen readers: every stacked control takes
  // this `id` and the row below binds a <label htmlFor> to it; the boolean Toggle takes an
  // accessible name via its own `label` prop (aria-label) instead. The form primitives grew an
  // explicit `id` for exactly this — before that only the raw controls could be bound, so the JSON
  // rows (already on TextArea) had a visible caption that named them for sighted users alone.
  const id = useId()
  const [jsonText, setJsonText] = useState(() =>
    serializeJsonField(value, prop.type === 'object' ? 'object' : 'array'))
  const [jsonErr, setJsonErr] = useState<string | null>(null)

  let control: React.ReactNode
  if (prop.type === 'array' || prop.type === 'object') {
    // A structured field needs a JSON editor. It fell through to the text branch below,
    // whose `String(value)` renders an array of objects as the literal
    // "[object Object],[object Object]" — so slack-channel's **Allowed Users** (the very
    // setting #953 is about), Tracking Channels, Open Channels and Reactions were not
    // merely unhelpful here, they were unreadable and unfillable. The Apps Configure
    // dialog has always rendered these as JSON; this reuses ITS exported
    // serialize/parse helpers rather than growing a second parser.
    const expected = prop.type === 'object' ? 'object' : 'array'
    control = (
      // The shared TextArea primitive rather than bespoke chrome: the design-system
      // adoption ratchet counts raw form elements and may only shrink. (It counts them by
      // regex over the file text, so do not spell the raw tag name in a comment here —
      // that alone tripped the ratchet.) `ariaLabel` because this form names its rows with
      // its own label element, not a Field context.
      <TextArea id={id} surface="high" value={jsonText} rows={4} mono ariaLabel={label}
        onChange={(nv) => {
          setJsonText(nv)
          const res = parseJsonField(nv, expected)
          // Invalid JSON updates the buffer and the hint but never the value: a half-typed
          // entry must not be savable as a silent {} that wipes a working allowlist.
          if ('error' in res) { setJsonErr(res.error); return }
          setJsonErr(null)
          onChange(res.value)
        }} />
    )
  } else if (prop.enum && prop.enum.length) {
    control = (
      <Select id={id} size="md" surface="high" value={String(value ?? prop.default ?? '')} onChange={onChange}
        options={prop.enum.map((o) => ({ value: o, label: o }))} />
    )
  } else if (prop.type === 'boolean') {
    const on = Boolean(value ?? prop.default)
    control = <Toggle on={on} onChange={onChange} label={label} />
  } else if (prop.type === 'integer' || prop.type === 'number') {
    control = (
      // An EMPTY entry commits `undefined`, not 0 — an optional numeric setting must be clearable
      // back to absent. That is also why this is the TextInput numeric field and not `NumberField`:
      // the stepper's `value: number` cannot express "unset" and reverts an empty entry to the last
      // good value, so adopting it here would make an optional bound permanent once typed.
      <TextInput id={id} type="number" size="md" surface="high" value={value == null ? '' : String(value)}
        min={prop.minimum} max={prop.maximum}
        onChange={(v) => onChange(v === '' ? undefined : Number(v))}
        placeholder={meta.placeholder ?? (prop.default != null ? String(prop.default) : '')} />
    )
  } else if (meta.sensitive) {
    control = (
      <TextInput id={id} type={showSecret ? 'text' : 'password'} size="md" surface="high"
        value={String(value ?? '')} onChange={onChange}
        minLength={prop.minLength} maxLength={prop.maxLength}
        placeholder={secretAlreadySet ? 'saved — leave blank to keep' : meta.placeholder ?? '••••••••'}
        trailingSlot={
          <SquareIconButton label={showSecret ? 'Hide' : 'Show'} onClick={() => setShowSecret((s) => !s)}>
            {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
          </SquareIconButton>
        } />
    )
  } else {
    control = (
      <TextInput id={id} type="text" size="md" surface="high" value={String(value ?? '')} onChange={onChange}
        minLength={prop.minLength} maxLength={prop.maxLength} pattern={prop.pattern}
        placeholder={meta.placeholder ?? (prop.default != null ? String(prop.default) : '')} />
    )
  }

  // boolean renders label + switch on one row; everything else stacks. The
  // Toggle carries its own aria-label; the stacked variants bind <label htmlFor>.
  if (prop.type === 'boolean') {
    return (
      <div className="flex items-center justify-between gap-l">
        <div className="min-w-0">
          <div data-type="body-s" className="text-on-surface">{label}</div>
          {meta.help && <div data-type="caption" className="mt-0.5 text-on-surface-low">{meta.help}</div>}
        </div>
        {control}
      </div>
    )
  }
  // A JSON parse error rides the help line — the same place the Apps dialog puts it — so a
  // half-typed entry explains itself instead of silently refusing to save.
  const hint = jsonErr ? `${meta.help ? meta.help + ' — ' : ''}⚠ ${jsonErr}` : meta.help
  return (
    <div>
      <label htmlFor={id} data-type="body-s" className="mb-1 block text-on-surface">{label}</label>
      {hint && <div data-type="caption" className="mb-1.5 text-on-surface-low">{hint}</div>}
      {control}
    </div>
  )
}
