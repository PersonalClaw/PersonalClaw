import { useRef, useState } from 'react'
import { Field, Select, TextArea } from '../../ui/forms'
import { Markdown } from '../../ui/Markdown'
import { api } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { presentSecrets, rebaseRecord, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import {
  missingRequired,
  SchemaField,
  SchemaFields,
  type JsonSchema,
  type SchemaMeta,
} from '../tools/schema'
import { usePromptWidgets } from '../prompts/promptWidgets'
import { listFieldKind, listValueFits, SchemaListField } from './schemaListField'

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
  const long = unsafeIntegerLiteral(trimmed)
  if (long) return { error: `${long} has more digits than a number here can keep — put it in quotes to keep every digit` }
  return { value: parsed }
}

/** The first integer written in JSON *text*, outside its strings, that a JavaScript number cannot
 *  hold exactly, or `''`. Parsed, `1289011223344556677` is `1289011223344556800`, a different
 *  ID, and a save would store that with nothing on screen saying so. Called on text that parsed. */
function unsafeIntegerLiteral(text: string): string {
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '"') {
      for (i++; i < text.length && text[i] !== '"'; i++) if (text[i] === '\\') i++
      continue
    }
    if (c !== '-' && (c < '0' || c > '9')) continue
    const m = /^-?\d+(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i))
    if (!m) continue
    if (!m[1] && !m[2] && !Number.isSafeInteger(Number(m[0]))) return m[0]
    i += m[0].length - 1
  }
  return ''
}

/** A structured setting's text while it does not parse. The form holds THIS as the field's value,
 *  not the last value that parsed: that value is not what the screen shows, and a save that sent
 *  it closed the dialog as if saved and put back the old list (an empty one included) over the
 *  text the user had entered. A save sees it and refuses, naming the field (`unparsedLabels`).
 *
 *  It cannot be sent by a form that forgot to look: serializing it throws that same sentence, so a
 *  save that gets as far as the request fails loudly instead of writing a value nobody entered. */
export class UnparsedJson {
  constructor(readonly text: string, readonly error: string, readonly label: string) {}
  toJSON(): never {
    throw new Error(unparsedSentence([this.label]))
  }
}

/** The labels of the structured settings in *values* whose text does not parse, in order. */
export function unparsedLabels(values: Record<string, unknown>): string[] {
  return Object.values(values)
    .filter((v): v is UnparsedJson => v instanceof UnparsedJson)
    .map((v) => v.label)
}

/** Why a save is refused while *labels* do not parse. */
export function unparsedSentence(labels: string[]): string {
  const one = labels.length === 1
  return `${labels.join(', ')} ${one ? "isn't" : "aren't"} valid JSON yet — fix ${one ? 'it' : 'them'}, or clear ${one ? 'it' : 'them'}, before saving.`
}

/** A structured field's text on screen: what was typed while it does not parse, else the value. */
export function jsonFieldText(value: unknown, expected: 'array' | 'object'): string {
  return value instanceof UnparsedJson ? value.text : serializeJsonField(value, expected)
}

/** A JSON editor's text, its error and its edit, for both settings forms' editors (this dialog's
 *  `JsonField` and Settings › Providers' `SchemaField`), so the two cannot disagree.
 *
 *  Each edit hands the form the parsed value (of the expected shape) or, while the text does not
 *  parse, an `UnparsedJson` holding it — never the last value that parsed. The value coming back
 *  as what the editor handed out is its own edit; a value that changes to anything else was
 *  changed elsewhere (an edit discarded for the stored copy, the copy a save stored), and the text
 *  follows it. The ⚠ is about text the user typed, and only that: the stored value written out is
 *  not theirs to correct (a long number in it was already rounded when the browser read it, so
 *  telling them to quote it would quote the wrong digits). `active` is false for a field this
 *  editor does not render, which it then leaves alone. */
export function useJsonFieldText(value: unknown, expected: 'array' | 'object', label: string,
  onChange: (v: unknown) => void, active = true) {
  const [text, setText] = useState(() => (active ? jsonFieldText(value, expected) : ''))
  const [seen, setSeen] = useState<unknown>(value)
  const [handedOut, setHandedOut] = useState<unknown>(value)
  // Whether the text on screen is the user's own edit, rather than the form's value written out.
  const [typed, setTyped] = useState(false)
  if (active && value !== seen) {
    setSeen(value)
    if (value !== handedOut) {
      setText(jsonFieldText(value, expected))
      setTyped(false)
    }
  }
  const own = active && typed ? parseJsonField(text, expected) : null
  const error = value instanceof UnparsedJson ? value.error : own && 'error' in own ? own.error : null
  const edit = (nv: string) => {
    setText(nv)
    setTyped(true)
    const parsed = parseJsonField(nv, expected)
    const next = 'error' in parsed ? new UnparsedJson(nv, parsed.error, label) : parsed.value
    setHandedOut(next)
    onChange(next)
  }
  return { text, error, edit }
}

/** An app's `meta.help` is markdown its AUTHOR wrote — the same string the Store card and the
 *  detail panel render — so it reaches the field hint through the one renderer rather than as
 *  literal asterisks and backticks. `inline`, because the hint sink is a `<p>`. */
function helpHint(help?: string) {
  return help ? <Markdown inline>{help}</Markdown> : undefined
}

/** JSON editor for a structured (array/object) config field whose schema does not describe its
 *  entries — a list that does gets `SchemaListField` instead. The backend validates
 *  the persisted type, so a plain text input (which stringifies an object to the
 *  literal "[object Object]") would both misrender AND be rejected on save. What it hands the
 *  form, and when its text follows the form, is `useJsonFieldText`. `name` is the field's label
 *  without the required marker. */
function JsonField({ label, name, help, expected, value, onChange }: {
  label: string
  name: string
  help?: string
  expected: 'array' | 'object'
  value: unknown
  onChange: (v: unknown) => void
}) {
  const { text, error, edit } = useJsonFieldText(value, expected, name, onChange)
  // The error keeps its own literal text (ours, not the app's) beside the rendered help.
  const hint = error
    ? <>{help ? <>{helpHint(help)}{' — '}</> : null}⚠ {error}</>
    : helpHint(help)
  return (
    <Field label={label} hint={hint}>
      <TextArea
        value={text}
        rows={4}
        mono
        ariaLabel={label}
        onChange={edit}
      />
    </Field>
  )
}

// One JSON-Schema property as the app config UI understands it (Draft-07 + x-meta).
export interface SchemaProp extends JsonSchema {
  // Constraint keywords the platform enforces (#616) — validate_config's
  // supported set; the form mirrors them as native input attributes.
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  'x-meta'?: SchemaMeta
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
 *
 *  🪤 Each field carries a stable `id`/`name`, and NEITHER IS AN ACCESSIBLE NAME. This
 *  docstring used to say "(a11y)" after the id/name clause, which is what the defect
 *  looked like from the inside. Measured by driving Apps → kebab → Configure against a
 *  real gateway: **16 of 16 fields across six installed apps had no resolvable
 *  accessible name**, so `getByRole('spinbutton', {name: 'Request Timeout'})` found
 *  zero and a screen reader announced "spin button, 20".
 *
 *  A control gets its name by claiming the wrapping `Field`'s published label through
 *  `FieldLabelCtx`, and only the `ui/forms` primitives read that context. Two of the
 *  four branches below already knew it — the boolean toggle sets `aria-label={label}`
 *  and `JsonField` passes `ariaLabel={label}` — so this was the file half-applying its
 *  own rule. The raw `<input>` cannot subscribe at all (`design/rawFormControls.test.tsx`
 *  proves that with a reproduction), and `Select`/`TextInput` deliberately stand DOWN
 *  from the Field label whenever a `name` is passed (`claimsFieldLabel = !!labelId &&
 *  !name && !ariaLabel`) — which this call site always does. So passing a primitive
 *  would not have fixed it either; the name has to be explicit.
 *
 *  Why the raw `<input>` stays rather than becoming `TextInput`: `TextInput` takes no
 *  `step`, and `step={1}` on an integer field is #616's declared-bounds contract
 *  reaching the browser. `design/rawFormControls.test.tsx` blesses exactly this escape
 *  hatch ("a raw element with its own aria-label IS named"). The rail is
 *  `appConfigFieldNames.test.tsx`, which asserts the NAME rather than the attribute. */
export function AppConfigFields({ appName, props, cur, set, secretSet = [], required = [] }: {
  appName: string
  props: Record<string, SchemaProp>
  cur: Record<string, unknown>
  set: (key: string, value: unknown) => void
  // The schema's `required` array. A required field gets the same ` *` marker the trigger
  // action-config form already uses (`ActionConfig.tsx`) plus `aria-required` on the control, so
  // the affordance is not colour- or glyph-only.
  required?: readonly string[]
  // Names of fields that already have a stored secret (from the config GET's
  // `_secret_set`). Such fields are WRITE-ONLY: the backend never sends the real value,
  // so the input starts blank with a "saved — leave blank to keep" placeholder; typing a
  // new value replaces the secret, blank keeps it (#43). The backend's list, not the
  // schema's `sensitive` flag, decides: it also names a field whose stored value is a
  // credential-store reference the schema never declared (a credential-named field).
  secretSet?: string[]
}) {
  const needsPrompt = Object.values(props).some((p) => p['x-meta']?.widget === 'prompt')
  const { widgets } = usePromptWidgets(needsPrompt)

  const renderField = (key: string, p: SchemaProp, isRequired: boolean) => {
        const meta = p['x-meta'] ?? {}
        const label = (meta.label || key) + (isRequired ? ' *' : '')
        const v = cur[key]
        const fieldId = `app-cfg-${appName}-${key}`
        const secretAlreadySet = secretSet.includes(key)
        if (meta.widget && widgets[meta.widget]) {
          return (
            <SchemaField
              key={key}
              name={key}
              schema={p}
              required={isRequired}
              value={v}
              onChange={(nv) => set(key, nv)}
              widgets={widgets}
            />
          )
        }
        if (Array.isArray(p.enum) && p.enum.length) {
          return (
            <Field key={key} label={label} hint={helpHint(meta.help)}>
              <Select name={fieldId} ariaLabel={label} value={String(v ?? '')} onChange={(nv) => set(key, nv)}
                required={isRequired}
                options={p.enum.map((o) => ({ value: String(o), label: String(o) }))} />
            </Field>
          )
        }
        if (p.type === 'boolean') {
          return (
            <Field key={key} label={label} hint={helpHint(meta.help)}>
              <button type="button" id={fieldId} name={fieldId} onClick={() => set(key, !v)}
                className={`h-6 w-11 rounded-pill transition-colors ${v ? 'bg-primary' : 'bg-surface-highest'}`}
                aria-pressed={!!v} aria-label={label} aria-required={isRequired || undefined}>
                <span className={`block size-5 rounded-full bg-white transition-transform ${v ? 'translate-x-5' : 'translate-x-0.5'}`} />
              </button>
            </Field>
          )
        }
        if (p.type === 'array' && listFieldKind(p) && listValueFits(p, v)) {
          return (
            <Field key={key} label={label} hint={helpHint(meta.help)}>
              <SchemaListField label={label} schema={p} value={v} onChange={(nv) => set(key, nv)} />
            </Field>
          )
        }
        if (p.type === 'array' || p.type === 'object') {
          return (
            <JsonField key={key} label={label} name={meta.label || key} help={meta.help}
              expected={p.type} value={v} onChange={(nv) => set(key, nv)} />
          )
        }
        const isNum = p.type === 'integer' || p.type === 'number'
        return (
          <Field key={key} label={label} hint={helpHint(meta.help)}>
            <input
              id={fieldId} name={fieldId}
              // The name, carrying the ` *` required marker exactly as the visible label
              // does, so the two never diverge.
              aria-label={label}
              aria-required={isRequired || undefined}
              type={meta.sensitive || secretAlreadySet ? 'password' : isNum ? 'number' : 'text'}
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
              className="w-full rounded-md border border-outline-variant bg-surface-high px-m py-s text-[0.8125rem] text-on-surface"
              value={v === undefined || v === null ? '' : String(v)}
              onChange={(e) => {
                const raw = e.target.value
                set(key, isNum ? (raw === '' ? undefined : Number(raw)) : raw)
              }} />
          </Field>
        )
  }

  return (
    <SchemaFields
      fields={Object.entries(props)}
      required={required}
      values={cur}
      configured={secretSet}
      renderField={renderField}
    />
  )
}

/** The form's starting values from a config read — schema defaults under the stored config, each
 *  stored secret BLANKED — with the revision that read reported, which a save names. A set
 *  sensitive field arrives as a mask sentinel; the input starts blank so the user isn't editing
 *  dots, and a blank submit means "keep the stored secret" (the backend preserves it, #43). */
function editableAppConfig(d: {
  config: Record<string, unknown>; schema: Record<string, unknown>; _secret_set?: string[]; revision: string
}): Revisioned<Record<string, unknown>> {
  const merged: Record<string, unknown> = {}
  for (const [k, p] of Object.entries((d.schema as AppConfigSchema).properties ?? {})) {
    if (p.default !== undefined) merged[k] = p.default
  }
  Object.assign(merged, d.config ?? {})
  for (const k of d._secret_set ?? []) merged[k] = ''
  return { value: merged, revision: d.revision }
}

/** Load + edit + persist an app's config against its schema. Returns the schema
 *  props, the effective values (saved over defaults), an editor, and a save fn.
 *  `loading` is true only while the read is in flight; `error` is the read's rejection (the hook
 *  used to DISCARD it, which made `loading` true forever on a failed read); `hasSchema` is false
 *  for a schema-less app. `guard` is the save's stale-write guard, for the consumer's
 *  `StaleWriteNotice`. */
export function useAppConfig(name: string) {
  const { data, error: loadErr, refresh } = useQuery(`app-config:${name}`, () => api.appConfig(name), { persist: false })
  const [values, setValues] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState(0)
  const reload = () => { invalidateKeys(`app-config:${name}`); refresh() }
  // What to do once the save the user asked for lands — now, or after the notice re-applies it.
  const onLanded = useRef<(() => void) | undefined>(undefined)
  // 🔴 THE FILE IS SAVED WHOLE, over the revision the form read it at. The backend replaces the
  // app's settings file with this form's values — and deletes the credentials the new one stops
  // referencing — so a form opened before Settings → Providers, another tab, or the app itself
  // saved it put its stale copy back over that save. A stale copy is now refused and the edit
  // re-applied field by field on top of what is stored (`ui/StaleWriteNotice`), compared in the
  // same defaults-and-blanked-secrets form this form edits.
  const guard = useStaleWriteGuard<Record<string, unknown>>({
    read: () => api.appConfig(name).then(editableAppConfig),
    write: (next, rev) => api.saveAppConfig(name, next, rev),
    onSaved: () => {
      invalidateKeys(`app-config:${name}`)
      setValues(null)
      setSavedAt(Date.now())
      const done = onLanded.current
      onLanded.current = undefined
      done?.()
    },
    onDiscard: () => { onLanded.current = undefined; setValues(null); reload() },
  })

  const schema = (data?.schema ?? {}) as AppConfigSchema
  const props = schema.properties ?? {}
  const hasSchema = Object.keys(props).length > 0
  const secretSet = data?._secret_set ?? []
  // Only keys the schema actually declares — a `required` entry naming a property that does not
  // exist would otherwise gate Save on a field the form never renders, i.e. an unsavable form with
  // nothing to fill in.
  const required = (schema.required ?? []).filter((k) => k in props)
  const cur: Record<string, unknown> = values ?? (data ? editableAppConfig(data).value : {})

  const set = (k: string, v: unknown) => setValues({ ...cur, [k]: v })
  const dirty = values !== null
  // A stored secret satisfies its own required field even though the input is blank by design
  // (write-only: blank means "keep it"). Labels, not keys — the whole point of #491 is that a raw
  // schema key is not what the user is looking at.
  const missing = missingRequired(cur, required, { satisfied: secretSet })
  const missingLabels = missing.map((k) => props[k]?.['x-meta']?.label || k)
  // The structured settings whose text on screen does not parse: there is nothing valid to send
  // for them, and sending the value from before would throw away what was typed.
  const unparsed = unparsedLabels(cur)

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
    if (unparsed.length > 0) {
      setErr(unparsedSentence(unparsed))
      return
    }
    setBusy(true); setErr(null)
    onLanded.current = onDone
    try {
      // `false` is a refused stale copy: the notice keeps the edit, the form stays open, and
      // `onDone` runs if the user re-applies it.
      const base = editableAppConfig(data)
      await guard.save(base, cur, rebaseRecord(base.value, cur))
    } catch (e) { onLanded.current = undefined; setErr(String((e as Error).message || e)) }
    finally { setBusy(false) }
  }

  // `loading` is the read still being IN FLIGHT — not merely "no data". A failed read has no data
  // either, and conflating the two is what showed "Loading…" forever with no way out.
  // What the stale-write review shows for this form: its secrets as saved or hidden, never blank.
  const present = presentSecrets((k) => !!props[k]?.['x-meta']?.sensitive, secretSet)
  return { loading: data === undefined && !loadErr, error: loadErr, reload,
    props, hasSchema, cur, set, save, busy, err, dirty, savedAt, secretSet,
    required, missing, missingLabels, unparsedLabels: unparsed, guard, present }
}
