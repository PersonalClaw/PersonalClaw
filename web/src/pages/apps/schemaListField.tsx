import { useRef, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { AddItemButton } from '../../ui/AddItemButton'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Toggle } from '../../ui/Toggle'
import { ChipInput, Select, TextInput } from '../../ui/forms'

/** The part of a JSON-Schema property a list control reads. Structural, so the Apps Configure
 *  form's `SchemaProp` and Settings › Providers' `ProviderSchemaProp` both fit it. */
export interface ListSchema {
  type?: string | string[]
  enum?: unknown[]
  default?: unknown
  items?: ListSchema
  properties?: Record<string, ListSchema>
  required?: string[]
  'x-meta'?: { label?: string; placeholder?: string }
}

type Row = Record<string, unknown>

const SCALAR_TYPES = new Set(['string', 'number', 'integer', 'boolean'])

/** A field of a record the list can edit: a choice, a text, a number, a yes/no, or a list of texts. */
function editableInRecord(s: ListSchema): boolean {
  if (Array.isArray(s.enum) && s.enum.length > 0) return true
  if (s.type === 'array') return s.items?.type === 'string' && !s.items.enum
  return typeof s.type === 'string' && SCALAR_TYPES.has(s.type)
}

/** How an `array` field's entries can be edited, from what its schema says they are: `strings`
 *  (texts, one chip each), `records` (one row per entry, each field its own control), or `null` —
 *  the schema does not describe its entries, so the field stays the JSON editor. */
export function listFieldKind(schema: ListSchema): 'strings' | 'records' | null {
  const items = schema.type === 'array' ? schema.items : undefined
  if (!items) return null
  if (items.type === 'string' && !items.enum) return 'strings'
  if (items.type !== 'object' || !items.properties) return null
  const fields = Object.values(items.properties)
  return fields.length > 0 && fields.every(editableInRecord) ? 'records' : null
}

/** Whether a stored value is one the list control can show as it is. One it cannot (entries of
 *  another shape, written by hand or by an older version) keeps the JSON editor, so the form never
 *  rewrites what it could not display. An absent value is an empty list. */
export function listValueFits(schema: ListSchema, value: unknown): boolean {
  if (value === undefined || value === null) return true
  if (!Array.isArray(value)) return false
  const kind = listFieldKind(schema)
  if (kind === 'strings') return value.every((v) => typeof v === 'string')
  return value.every((v) => typeof v === 'object' && v !== null && !Array.isArray(v))
}

function fieldLabel(key: string, s: ListSchema): string {
  return s['x-meta']?.label || key
}

/** An entry nobody has filled in yet: none of its texts, numbers or lists holds anything. Its
 *  choices and yes/no fields always hold their default, so they do not count. Such an entry is kept
 *  on screen for the user to fill and left out of what is saved. */
function unfilled(row: Row, fields: [string, ListSchema][]): boolean {
  return fields.every(([k, s]) => {
    if (s.type === 'boolean' || (Array.isArray(s.enum) && s.enum.length > 0)) return true
    const v = row[k]
    return v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)
  })
}

/** A new entry: each field's declared default, a choice's first option when it declares none. */
function newRow(fields: [string, ListSchema][]): Row {
  const row: Row = {}
  for (const [k, s] of fields) {
    if (s.default !== undefined) row[k] = s.default
    else if (Array.isArray(s.enum) && s.enum.length > 0) row[k] = s.enum[0]
  }
  return row
}

function RecordFieldControl({ name, schema, required, value, onChange }: {
  name: string
  schema: ListSchema
  required: boolean
  value: unknown
  onChange: (v: unknown) => void
}) {
  const placeholder = schema['x-meta']?.placeholder
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return (
      <Select size="sm" surface="high" ariaLabel={name} required={required} value={String(value ?? '')}
        onChange={onChange} options={schema.enum.map((o) => ({ value: String(o), label: String(o) }))} />
    )
  }
  if (schema.type === 'boolean') return <Toggle on={!!value} onChange={onChange} label={name} size="sm" />
  if (schema.type === 'array') {
    return (
      <ChipInput values={Array.isArray(value) ? value.map(String) : []} onChange={onChange}
        ariaLabel={`Add to ${name}`} placeholder={placeholder ?? 'Add…'} />
    )
  }
  const numeric = schema.type === 'integer' || schema.type === 'number'
  return (
    <TextInput size="sm" surface="high" type={numeric ? 'number' : 'text'} ariaLabel={name} required={required}
      placeholder={placeholder} value={value === undefined || value === null ? '' : String(value)}
      onChange={(v) => onChange(numeric ? (v === '' ? undefined : Number(v)) : v)} />
  )
}

/** One row per entry, each of the entry's fields its own control, with Remove per row and Add below.
 *
 *  The rows are a local draft of the value: an entry just added is on screen while it is still
 *  empty, and what reaches `onChange` is every entry but the unfilled ones. A field the schema does
 *  not declare is carried along untouched. A value that changes from outside the list — an edit
 *  discarded for the stored copy, the copy a save stored — replaces the draft, so the rows never
 *  show entries the form no longer holds (and a later edit cannot put them back). */
function RecordList({ label, items, value, onChange }: {
  label: string
  items: ListSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  const fields = Object.entries(items.properties ?? {})
  const required = new Set(items.required ?? [])
  const entry = items['x-meta']?.label || 'entry'
  // Each row keeps an id of its own, so removing one leaves the others' controls (and a chip half
  // typed in one of them) with the entry they belong to.
  const nextId = useRef(0)
  const rowsOf = (v: unknown) =>
    (Array.isArray(v) ? (v as Row[]) : []).map((row) => ({ id: nextId.current++, row: { ...row } }))
  const [rows, setRows] = useState<{ id: number; row: Row }[]>(() => rowsOf(value))
  // The value as the list last handed it out (or was first given it): the list's own edit coming
  // back as `value` matches it, and anything else is a change made elsewhere.
  const [handedOut, setHandedOut] = useState(() => JSON.stringify(value ?? []))
  const incoming = JSON.stringify(value ?? [])
  if (incoming !== handedOut) {
    setHandedOut(incoming)
    setRows(rowsOf(value))
  }
  const commit = (next: { id: number; row: Row }[]) => {
    const kept = next.map((r) => r.row).filter((r) => !unfilled(r, fields))
    setRows(next)
    setHandedOut(JSON.stringify(kept))
    onChange(kept)
  }
  return (
    <div role="group" aria-label={label} className="flex flex-col gap-s">
      {rows.map(({ id, row }, i) => (
        <div key={id} role="group" aria-label={`${entry} ${i + 1}`}
          className="flex flex-wrap items-end gap-s rounded-md border border-outline-variant p-s">
          {fields.map(([k, s]) => (
            <div key={k} className="flex min-w-[10rem] flex-1 flex-col gap-xs">
              <span data-type="caption" className="text-on-surface-low" aria-hidden>
                {fieldLabel(k, s)}{required.has(k) ? ' *' : ''}
              </span>
              <RecordFieldControl name={`${fieldLabel(k, s)}, ${entry} ${i + 1}`} schema={s}
                required={required.has(k)} value={row[k]}
                onChange={(v) => commit(rows.map((r) => (r.id === id ? { id, row: { ...r.row, [k]: v } } : r)))} />
            </div>
          ))}
          <SquareIconButton icon={X} iconSize={14} label={`Remove ${entry} ${i + 1}`}
            onClick={() => commit(rows.filter((r) => r.id !== id))} className="shrink-0" />
        </div>
      ))}
      <AddItemButton className="self-start"
        onClick={() => commit([...rows, { id: nextId.current++, row: newRow(fields) }])}>
        <Plus size={14} aria-hidden /> Add {entry}
      </AddItemButton>
    </div>
  )
}

/** The control for an `array` field whose schema describes its entries (`listFieldKind`): chips
 *  for a list of texts, a row per entry for a list of records. Asking the user to type JSON for a
 *  list of Slack user IDs, or of mailboxes with their senders, is the form handing its job back.
 *
 *  `label` names the control for a screen reader where no `Field` wraps it (Settings › Providers
 *  labels its own rows); inside a `Field`, the chips claim the Field's label instead. */
export function SchemaListField({ label, schema, value, onChange }: {
  label: string
  schema: ListSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  if (listFieldKind(schema) === 'strings') {
    return (
      <ChipInput values={Array.isArray(value) ? (value as string[]) : []} onChange={onChange}
        ariaLabel={`Add to ${label}`} placeholder={schema.items?.['x-meta']?.placeholder ?? 'Add…'} />
    )
  }
  return <RecordList label={label} items={schema.items ?? {}} value={value} onChange={onChange} />
}
