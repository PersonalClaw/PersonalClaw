import { useEffect, useId, useState } from 'react'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import { api, type ProviderSchema, type ProviderSchemaProp } from '../../lib/api'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { SavedToast } from './settingsUI'

export const inputCls = 'h-9 w-full rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'

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
 *  provider's toggle — only mounted when the provider is enabled + has a schema. */
export function ProviderConfigForm({ name }: { name: string }) {
  const [schema, setSchema] = useState<ProviderSchema | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    Promise.all([api.providerSchema(name), api.providerConfig(name)])
      .then(([s, c]) => { if (live) { setSchema(s); setValues(c ?? {}) } })
      .catch(() => { if (live) setSchema({ properties: {} }) })
    return () => { live = false }
  }, [name])

  if (!schema) return <div className="py-2 text-on-surface-low text-[0.78rem]"><Loader2 size={12} className="inline animate-spin" /> Loading config…</div>
  const props = Object.entries(schema.properties ?? {})
  if (props.length === 0) return null

  const set = (k: string, v: unknown) => { setValues((p) => ({ ...p, [k]: v })); setDirty(true); setSaved(false); setErr('') }
  const save = async () => {
    setSaving(true); setErr('')
    try { await api.saveProviderConfig(name, values); setDirty(false); setSaved(true); setTimeout(() => setSaved(false), 2000) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Save failed'
      try { const p = JSON.parse(msg); msg = p.error + (p.details ? `: ${p.details.join('; ')}` : '') } catch { /* raw */ }
      setErr(msg)
    }
    setSaving(false)
  }

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-outline-variant/30 pt-3">
      {props.map(([key, prop]) => <SchemaField key={key} fieldKey={key} prop={prop} value={values[key]} onChange={(v) => set(key, v)} />)}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save'}</Button>
        <SavedToast show={saved} />
        {dirty && !saved && <span className="text-on-surface-low text-[0.75rem]">Unsaved changes</span>}
        {err && <span className="text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      </div>
    </div>
  )
}

export function SchemaField({ fieldKey, prop, value, onChange }: {
  fieldKey: string; prop: ProviderSchemaProp; value: unknown; onChange: (v: unknown) => void
}) {
  const meta = prop['x-meta'] ?? {}
  const label = meta.label ?? fieldKey
  const [showSecret, setShowSecret] = useState(false)
  // Associate the visible label with the control for screen readers: native
  // inputs/selects get `id` + a <label htmlFor>; the boolean Toggle takes an
  // accessible name via its own `label` prop (aria-label).
  const id = useId()

  let control: React.ReactNode
  if (prop.enum && prop.enum.length) {
    control = (
      <select id={id} value={String(value ?? prop.default ?? '')} onChange={(e) => onChange(e.target.value)} className={inputCls + ' cursor-pointer'}>
        {prop.enum.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  } else if (prop.type === 'boolean') {
    const on = Boolean(value ?? prop.default)
    control = <Toggle on={on} onChange={onChange} label={label} />
  } else if (prop.type === 'integer' || prop.type === 'number') {
    control = (
      <input id={id} type="number" value={value == null ? '' : String(value)} min={prop.minimum} max={prop.maximum}
        onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
        placeholder={meta.placeholder ?? (prop.default != null ? String(prop.default) : '')} className={inputCls} />
    )
  } else if (meta.sensitive) {
    control = (
      <div className="relative">
        <input id={id} type={showSecret ? 'text' : 'password'} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}
          placeholder={meta.placeholder ?? '••••••••'} className={inputCls + ' pr-10'} />
        <button type="button" onClick={() => setShowSecret((s) => !s)} aria-label={showSecret ? 'Hide' : 'Show'}
          className="absolute right-1.5 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-on-surface-low hover:text-on-surface">
          {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
    )
  } else {
    control = (
      <input id={id} type="text" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}
        placeholder={meta.placeholder ?? (prop.default != null ? String(prop.default) : '')} className={inputCls} />
    )
  }

  // boolean renders label + switch on one row; everything else stacks. The
  // Toggle carries its own aria-label; the stacked variants bind <label htmlFor>.
  if (prop.type === 'boolean') {
    return (
      <div className="flex items-center justify-between gap-l">
        <div className="min-w-0">
          <div className="text-on-surface text-[0.82rem]">{label}</div>
          {meta.help && <div className="mt-0.5 text-on-surface-low text-[0.75rem]">{meta.help}</div>}
        </div>
        {control}
      </div>
    )
  }
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-on-surface text-[0.82rem]">{label}</label>
      {meta.help && <div className="mb-1.5 text-on-surface-low text-[0.75rem]">{meta.help}</div>}
      {control}
    </div>
  )
}
