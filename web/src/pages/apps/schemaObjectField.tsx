import { useRef, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { AddItemButton } from '../../ui/AddItemButton'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Toggle } from '../../ui/Toggle'
import { TextInput } from '../../ui/forms'
import { editableInRecord, FieldCell, fieldLabel, newRow, RecordFieldControl, type ListSchema } from './schemaListField'

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)

/** The schema of a field that may also be `null` (`type: [<scalar>, 'null']`) with the `null` taken
 *  out, when the form can edit the rest; else `undefined`. */
function nullableOf(s: ListSchema): ListSchema | undefined {
  if (!Array.isArray(s.type) || s.type.length !== 2 || !s.type.includes('null')) return undefined
  const other = s.type.find((t) => t !== 'null')
  if (other === 'array') return undefined
  const rest = { ...s, type: other }
  return editableInRecord(rest) ? rest : undefined
}

/** An object's named fields, when the form can edit every one of them. */
function namedFields(s: ListSchema): [string, ListSchema][] | null {
  const fields = Object.entries(s.properties ?? {})
  if (fields.length === 0) return null
  return fields.every(([, f]) => editableInRecord(f) || nullableOf(f)) ? fields : null
}

/** What each of an object's entries by key is (`additionalProperties`), when the form can edit it:
 *  a record whose fields it can edit, or one text, number, choice or yes/no. */
function entrySchema(s: ListSchema): ListSchema | null {
  const entry = s.additionalProperties
  if (!isObj(entry)) return null
  if (entry.type === 'object') {
    const fields = Object.values(entry.properties ?? {})
    return fields.length > 0 && fields.every(editableInRecord) ? entry : null
  }
  return editableInRecord(entry) ? entry : null
}

/** How an `object` field can be edited, from what its schema says it holds: `fields` (named fields,
 *  a control each), `entries` (entries by key, a row each), or `null` — the schema does not say, so
 *  the field stays the JSON editor. */
export function objectFieldKind(schema: ListSchema): 'fields' | 'entries' | null {
  if (schema.type !== 'object') return null
  if (schema.properties && Object.keys(schema.properties).length > 0) {
    return !isObj(schema.additionalProperties) && namedFields(schema) ? 'fields' : null
  }
  return entrySchema(schema) ? 'entries' : null
}

/** Whether *v* is a value the control for field schema *s* shows as it is. */
function fits(s: ListSchema, v: unknown): boolean {
  const nullable = nullableOf(s)
  if (v === null) return nullable !== undefined
  const shape = nullable ?? s
  if (Array.isArray(shape.enum) && shape.enum.length > 0) return shape.enum.includes(v)
  if (shape.type === 'string') return typeof v === 'string'
  if (shape.type === 'integer' || shape.type === 'number') return typeof v === 'number'
  if (shape.type === 'boolean') return typeof v === 'boolean'
  if (shape.type === 'array') return Array.isArray(v) && v.every((x) => typeof x === 'string')
  return false
}

/** Whether a stored value is one the object's controls can show as it is. One they cannot (a key the
 *  schema does not name, a value of another type) keeps the JSON editor, so the form never rewrites
 *  what it could not display. An absent value is an empty object. */
export function objectValueFits(schema: ListSchema, value: unknown): boolean {
  if (value === undefined || value === null) return true
  if (!isObj(value)) return false
  const kind = objectFieldKind(schema)
  if (kind === 'fields') {
    const props = schema.properties ?? {}
    return Object.entries(value).every(([k, v]) => k in props && fits(props[k], v))
  }
  const entry = kind === 'entries' ? entrySchema(schema) : null
  if (!entry) return false
  return Object.values(value).every((v) => (entry.type === 'object' ? isObj(v) : fits(entry, v)))
}

/** A control per named field. A text left blank is left out of the value, so the app's own default
 *  applies (a field's `placeholder` can say what that is). A field that may be `null` has a switch:
 *  off stores `null`, and on again leaves the field out, back to its default. */
function NamedFields({ label, schema, value, onChange }: {
  label: string
  schema: ListSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  const fields = namedFields(schema) ?? []
  const required = new Set(schema.required ?? [])
  const cur: Obj = isObj(value) ? value : {}
  const set = (key: string, v: unknown) => {
    const next = { ...cur }
    if (v === undefined || v === '' || (Array.isArray(v) && v.length === 0)) delete next[key]
    else next[key] = v
    onChange(next)
  }
  return (
    <div role="group" aria-label={label} className="flex flex-col gap-s">
      {fields.map(([key, s]) => {
        const name = fieldLabel(key, s)
        const nullable = nullableOf(s)
        const off = nullable !== undefined && cur[key] === null
        return (
          <div key={key} className="flex items-center gap-s">
            <span data-type="caption" className="w-32 shrink-0 truncate text-on-surface-low" aria-hidden>
              {name}{required.has(key) ? ' *' : ''}
            </span>
            {nullable && (
              <Toggle size="sm" on={!off} label={`${name} on`} onChange={(on) => set(key, on ? undefined : null)} />
            )}
            <div className="min-w-0 flex-1">
              <RecordFieldControl name={name} schema={nullable ?? s} required={required.has(key)}
                value={off ? undefined : cur[key]} onChange={(v) => set(key, v)}
                disabledReason={off ? `${name} is switched off` : undefined} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** One entry by key while it is edited: its key and its value, with an id of its own so removing one
 *  leaves the others' controls with the entry they belong to. */
type Draft = { id: number; key: string; value: unknown }

/** A row per entry: its key, then its value's fields, with Remove per row and Add below.
 *
 *  The rows are a local draft of the value, as a list's are (`schemaListField`): an entry with no key
 *  yet stays on screen for the user to fill and is left out of what is saved. A key listed a second
 *  time is not saved a second time, and its row says so. A field the schema does not declare is
 *  carried along untouched. A value that changes from outside replaces the draft. */
function KeyedEntries({ label, schema, value, onChange }: {
  label: string
  schema: ListSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  const entry = entrySchema(schema) ?? {}
  const record = entry.type === 'object'
  const fields = record ? Object.entries(entry.properties ?? {}) : []
  const required = new Set(entry.required ?? [])
  const keyMeta = schema.propertyNames?.['x-meta'] ?? {}
  const keyLabel = keyMeta.label || 'Key'
  const noun = entry['x-meta']?.label || 'entry'
  const nextId = useRef(0)
  const draftsOf = (v: unknown): Draft[] =>
    Object.entries(isObj(v) ? v : {}).map(([key, val]) => ({
      id: nextId.current++, key, value: record && isObj(val) ? { ...val } : val,
    }))
  const [drafts, setDrafts] = useState<Draft[]>(() => draftsOf(value))
  const [handedOut, setHandedOut] = useState(() => JSON.stringify(value ?? {}))
  const incoming = JSON.stringify(value ?? {})
  if (incoming !== handedOut) {
    setHandedOut(incoming)
    setDrafts(draftsOf(value))
  }
  const listedAbove = (i: number) => {
    const key = drafts[i].key.trim()
    return key !== '' && drafts.slice(0, i).some((d) => d.key.trim() === key)
  }
  const commit = (next: Draft[]) => {
    const out: Obj = {}
    for (const d of next) {
      const key = d.key.trim()
      if (key && !(key in out)) out[key] = d.value
    }
    setDrafts(next)
    setHandedOut(JSON.stringify(out))
    onChange(out)
  }
  const edit = (id: number, change: Partial<Draft>) =>
    commit(drafts.map((d) => (d.id === id ? { ...d, ...change } : d)))
  const blank = (): unknown => (record ? newRow(fields) : entry.default ?? (Array.isArray(entry.enum) ? entry.enum[0] : ''))
  return (
    <div role="group" aria-label={label} className="flex flex-col gap-s">
      {drafts.map((d, i) => {
        const n = `${noun} ${i + 1}`
        const val: Obj = isObj(d.value) ? d.value : {}
        return (
          <div key={d.id} role="group" aria-label={n}
            className="flex flex-col gap-xs rounded-md border border-outline-variant p-s">
            <div className="flex flex-wrap items-end gap-s">
              <FieldCell caption={keyLabel} required>
                <TextInput size="sm" surface="high" ariaLabel={`${keyLabel}, ${n}`} required
                  placeholder={keyMeta.placeholder} value={d.key} onChange={(key) => edit(d.id, { key })} />
              </FieldCell>
              {record
                ? fields.map(([k, s]) => (
                  <FieldCell key={k} caption={fieldLabel(k, s)} required={required.has(k)}>
                    <RecordFieldControl name={`${fieldLabel(k, s)}, ${n}`} schema={s} required={required.has(k)}
                      value={val[k]} onChange={(v) => edit(d.id, { value: { ...val, [k]: v } })} />
                  </FieldCell>
                ))
                : (
                  <FieldCell caption={fieldLabel('Value', entry)}>
                    <RecordFieldControl name={`${fieldLabel('Value', entry)}, ${n}`} schema={entry} required={false}
                      value={d.value} onChange={(v) => edit(d.id, { value: v })} />
                  </FieldCell>
                )}
              <SquareIconButton icon={X} iconSize={14} label={`Remove ${n}`} className="shrink-0"
                onClick={() => commit(drafts.filter((x) => x.id !== d.id))} />
            </div>
            {listedAbove(i) && (
              <span data-type="caption" className="text-on-surface-low">
                {d.key.trim()} is listed above, so this {noun} is not saved. Change its {keyLabel} or remove it.
              </span>
            )}
          </div>
        )
      })}
      <AddItemButton className="self-start"
        onClick={() => commit([...drafts, { id: nextId.current++, key: '', value: blank() }])}>
        <Plus size={14} aria-hidden /> Add {noun}
      </AddItemButton>
    </div>
  )
}

/** The control for an `object` field whose schema says what it holds (`objectFieldKind`): a control
 *  per named field, or a row per entry by key. `label` names the group for a screen reader. */
export function SchemaObjectField({ label, schema, value, onChange }: {
  label: string
  schema: ListSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  return objectFieldKind(schema) === 'fields'
    ? <NamedFields label={label} schema={schema} value={value} onChange={onChange} />
    : <KeyedEntries label={label} schema={schema} value={value} onChange={onChange} />
}
