import { useState } from 'react'
import { Field, Select, TextArea } from '../../ui/forms'
import { api } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { missingRequired } from '../tools/schema'

/** Serialize a structured config value for the JSON editor's text buffer. */
export function serializeJsonField(value: unknown, expected: 'array' | 'object'): string {
  if (value === undefined || value === null) return expected === 'array' ? '[]' : '{}'
  try { return JSON.stringify(value, null, 2) } catch { return '' }
}

/** Parse edited JSON text for a structured field. Returns the parsed value (of the
 *  expected shape) OR an error string — never a partial/corrupt value. Empty text
 *  means "clear to the empty container". The backend validates the persisted type,
 *  so this must reject a JSON scalar / wrong-container before it reaches `onChange`. */
export function parseJsonField(text: string, expected: 'array' | 'object'):
  { value: unknown } | { error: string } {
  const trimmed = text.trim()
  if (trimmed === '') return { value: expected === 'array' ? [] : {} }
  let parsed: unknown
  try { parsed = JSON.parse(trimmed) } catch { return { error: 'invalid JSON' } }
  const okType = expected === 'array' ? Array.isArray(parsed)
    : (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed))
  if (!okType) return { error: `must be a JSON ${expected}` }
  return { value: parsed }
}

/** JSON editor for a structured (array/object) config field. The backend validates
 *  the persisted type, so a plain text input (which stringifies an object to the
 *  literal "[object Object]") would both misrender AND be rejected on save. This
 *  keeps a local text buffer, parses on edit, and calls `set` only with valid JSON
 *  of the expected shape — surfacing a parse error inline instead of corrupting state. */
function JsonField({ label, help, expected, value, onChange }: {
  label: string
  help?: string
  expected: 'array' | 'object'
  value: unknown
  onChange: (v: unknown) => void
}) {
  const [text, setText] = useState(() => serializeJsonField(value, expected))
  const [error, setError] = useState<string | null>(null)
  const hint = error ? `${help ? help + ' — ' : ''}⚠ ${error}` : help
  return (
    <Field label={label} hint={hint}>
      <TextArea
        value={text}
        rows={4}
        mono
        ariaLabel={label}
        onChange={(nv) => {
          setText(nv)
          const res = parseJsonField(nv, expected)
          if ('error' in res) { setError(res.error); return }
          setError(null)
          onChange(res.value)
        }}
      />
    </Field>
  )
}

// One JSON-Schema property as the app config UI understands it (Draft-07 + x-meta).
export interface SchemaProp {
  type?: string
  default?: unknown
  enum?: unknown[]
  // Constraint keywords the platform enforces (#616) — validate_config's
  // supported set; the form mirrors them as native input attributes.
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  'x-meta'?: { label?: string; help?: string; sensitive?: boolean }
}

export interface AppConfigSchema {
  properties?: Record<string, SchemaProp>
  // #491: the renderer never read this, so a required field looked identical to an optional one and
  // Save was gated on busy-ness alone — the only feedback was a server 400.
  required?: string[]
}

/** Render the schema-driven fields for an app's config into `cur`, calling
 *  `set(key, value)` on edit. Shared by the Apps-page Configure modal and the
 *  Settings > Apps panel so both render identical controls from one source.
 *  Each field carries a stable id/name (a11y). */
export function AppConfigFields({ appName, props, cur, set, secretSet = [], required = [] }: {
  appName: string
  props: Record<string, SchemaProp>
  cur: Record<string, unknown>
  set: (key: string, value: unknown) => void
  // The schema's `required` array. A required field gets the same ` *` marker the trigger
  // action-config form already uses (`ActionConfig.tsx`) plus `aria-required` on the control, so
  // the affordance is not colour- or glyph-only.
  required?: readonly string[]
  // Names of sensitive fields that already have a stored secret (from the config
  // GET's `_secret_set`). Such fields are WRITE-ONLY: the backend never sends the
  // real value, so the input starts blank with a "saved — leave blank to keep"
  // placeholder; typing a new value replaces the secret, blank keeps it (#43).
  secretSet?: string[]
}) {
  return (
    <>
      {Object.entries(props).map(([key, p]) => {
        const meta = p['x-meta'] ?? {}
        const isRequired = required.includes(key)
        const label = (meta.label || key) + (isRequired ? ' *' : '')
        const v = cur[key]
        const fieldId = `app-cfg-${appName}-${key}`
        const secretAlreadySet = !!meta.sensitive && secretSet.includes(key)
        if (Array.isArray(p.enum) && p.enum.length) {
          return (
            <Field key={key} label={label} hint={meta.help}>
              <Select name={fieldId} value={String(v ?? '')} onChange={(nv) => set(key, nv)}
                required={isRequired}
                options={p.enum.map((o) => ({ value: String(o), label: String(o) }))} />
            </Field>
          )
        }
        if (p.type === 'boolean') {
          return (
            <Field key={key} label={label} hint={meta.help}>
              <button type="button" id={fieldId} name={fieldId} onClick={() => set(key, !v)}
                className={`h-6 w-11 rounded-pill transition-colors ${v ? 'bg-primary' : 'bg-surface-highest'}`}
                aria-pressed={!!v} aria-label={label} aria-required={isRequired || undefined}>
                <span className={`block size-5 rounded-full bg-white transition-transform ${v ? 'translate-x-5' : 'translate-x-0.5'}`} />
              </button>
            </Field>
          )
        }
        if (p.type === 'array' || p.type === 'object') {
          return (
            <JsonField key={key} label={label} help={meta.help}
              expected={p.type} value={v} onChange={(nv) => set(key, nv)} />
          )
        }
        const isNum = p.type === 'integer' || p.type === 'number'
        return (
          <Field key={key} label={label} hint={meta.help}>
            <input
              id={fieldId} name={fieldId}
              aria-required={isRequired || undefined}
              type={meta.sensitive ? 'password' : isNum ? 'number' : 'text'}
              placeholder={secretAlreadySet ? 'saved — leave blank to keep' : undefined}
              // #616: a manifest's declared bounds reach the browser as native
              // constraint attributes, so the form hints/rejects before the
              // round trip — mirroring what validate_config now enforces.
              min={isNum && typeof p.minimum === 'number' ? p.minimum : undefined}
              max={isNum && typeof p.maximum === 'number' ? p.maximum : undefined}
              step={p.type === 'integer' ? 1 : undefined}
              minLength={!isNum && typeof p.minLength === 'number' ? p.minLength : undefined}
              maxLength={!isNum && typeof p.maxLength === 'number' ? p.maxLength : undefined}
              pattern={!isNum && !meta.sensitive && typeof p.pattern === 'string' ? p.pattern : undefined}
              className="w-full rounded-m border border-outline-variant bg-surface-high px-m py-s text-[0.8125rem] text-on-surface"
              value={v === undefined || v === null ? '' : String(v)}
              onChange={(e) => {
                const raw = e.target.value
                set(key, isNum ? (raw === '' ? undefined : Number(raw)) : raw)
              }} />
          </Field>
        )
      })}
    </>
  )
}

/** Load + edit + persist an app's config against its schema. Returns the schema
 *  props, the effective values (saved over defaults), an editor, and a save fn.
 *  `loading` is true only while the read is in flight; `error` is the read's rejection (the hook
 *  used to DISCARD it, which made `loading` true forever on a failed read); `hasSchema` is false
 *  for a schema-less app. */
export function useAppConfig(name: string) {
  const { data, error: loadErr, refresh } = useQuery(`app-config:${name}`, () => api.appConfig(name), { persist: false })
  const [values, setValues] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState(0)
  const reload = () => { invalidateKeys(`app-config:${name}`); refresh() }

  const schema = (data?.schema ?? {}) as AppConfigSchema
  const props = schema.properties ?? {}
  const hasSchema = Object.keys(props).length > 0
  const secretSet = data?._secret_set ?? []
  // Only keys the schema actually declares — a `required` entry naming a property that does not
  // exist would otherwise gate Save on a field the form never renders, i.e. an unsavable form with
  // nothing to fill in.
  const required = (schema.required ?? []).filter((k) => k in props)
  const cur: Record<string, unknown> = values ?? (() => {
    const base: Record<string, unknown> = {}
    for (const [k, p] of Object.entries(props)) if (p.default !== undefined) base[k] = p.default
    const merged = { ...base, ...(data?.config ?? {}) }
    // A set sensitive field arrives as a mask sentinel — start the input BLANK so
    // the user isn't editing dots; a blank submit means "keep the stored secret"
    // (the backend preserves it). Typing a value replaces it. (#43 write-only)
    for (const k of secretSet) merged[k] = ''
    return merged
  })()

  const set = (k: string, v: unknown) => setValues({ ...cur, [k]: v })
  const dirty = values !== null
  // A stored secret satisfies its own required field even though the input is blank by design
  // (write-only: blank means "keep it"). Labels, not keys — the whole point of #491 is that a raw
  // schema key is not what the user is looking at.
  const missing = missingRequired(cur, required, { satisfied: secretSet })
  const missingLabels = missing.map((k) => props[k]?.['x-meta']?.label || k)

  async function save(onDone?: () => void) {
    // 🔴 REFUSE A WRITE FROM A FORM THAT NEVER LOADED. `cur` falls back to `{}` before `data`
    // arrives, and the backend's `write_config` REPLACES the file (`atomic_write(json.dumps(values))`,
    // no merge) — so this save would erase the app's stored config, secrets included: the
    // keep-the-stored-secret branch only fires for keys PRESENT in the payload, and an unloaded form
    // sends none. Reachable without any failure, because the modal's Save sits outside its loading
    // branch. The guard is HERE rather than at the two call sites so every consumer inherits it.
    if (data === undefined) {
      setErr(loadErr
        ? "Couldn't load this app's configuration, so there is nothing to save yet. Retry the load first."
        : 'Still loading this app’s configuration — nothing to save yet.')
      return
    }
    // Refuse an incomplete form HERE, for the same reason the guard above lives here: both
    // consumers (the Apps modal and the Settings > Apps row) inherit it, so neither can post a
    // config the schema calls invalid just because its button forgot to gate.
    if (missing.length > 0) {
      setErr(`Fill in ${missingLabels.join(', ')} before saving.`)
      return
    }
    setBusy(true); setErr(null)
    try {
      await api.saveAppConfig(name, cur)
      invalidateKeys(`app-config:${name}`)
      setValues(null)
      setSavedAt(Date.now())
      onDone?.()
    } catch (e) { setErr(String((e as Error).message || e)) }
    finally { setBusy(false) }
  }

  // `loading` is the read still being IN FLIGHT — not merely "no data". A failed read has no data
  // either, and conflating the two is what showed "Loading…" forever with no way out.
  return { loading: data === undefined && !loadErr, error: loadErr, reload,
    props, hasSchema, cur, set, save, busy, err, dirty, savedAt, secretSet,
    required, missing, missingLabels }
}
