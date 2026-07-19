import { createContext, useContext, useId, useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Plus, GripVertical, Check, Search, AlertTriangle } from 'lucide-react'
import { IconButton } from '../../ui/IconButton'
import { Bud, Reorderable } from '../../ui/motion'
import { spring } from '../../design/motion'
import type { TaskItem, TaskNote } from '../../lib/api'
import { depMap, wouldCycle } from './dag'
import { statusMeta } from './taskMeta'

// A Field publishes the id of its (visible, uppercase) label so the single
// control it wraps can point back to it with aria-labelledby — turning the
// sighted-only label into a real accessible name for screen readers, with zero
// call-site changes. Controls fall back to this when they have no id/name of
// their own. Only the FIRST control in a Field should claim it (multi-control
// Fields like Variables keep their own per-input aria-labels).
const FieldLabelCtx = createContext<string | undefined>(undefined)
export function useFieldLabelId() { return useContext(FieldLabelCtx) }

/** Field wrapper — label row (optional right slot for a SoonTag) + control.
 *  The label carries a stable id and is exposed via context so the wrapped
 *  control associates with it for accessibility. */
export function Field({ label, hint, right, children }: { label: string; hint?: string; right?: ReactNode; children: ReactNode }) {
  const labelId = useId()
  return (
    <FieldLabelCtx.Provider value={labelId}>
      <div>
        <div className="mb-1.5 flex items-center gap-s">
          <span id={labelId} className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">{label}</span>
          {right}
        </div>
        {children}
        {hint && <p className="mt-1 text-on-surface-low text-[0.75rem]">{hint}</p>}
      </div>
    </FieldLabelCtx.Provider>
  )
}

export function TextInput({ value, onChange, placeholder, autoFocus, onKeyDown, name, ariaLabel }: { value: string; onChange: (v: string) => void; placeholder?: string; autoFocus?: boolean; onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void; name?: string; ariaLabel?: string }) {
  const labelId = useFieldLabelId()
  const autoId = useId()
  // Accessible name resolves in order: a Field's published label (aria-labelledby),
  // else an explicit ariaLabel for call-sites outside a Field (e.g. inside a Modal).
  return (
    <input value={value} autoFocus={autoFocus} name={name} id={name || autoId} aria-labelledby={!name && labelId ? labelId : undefined} aria-label={!labelId && !name ? ariaLabel : undefined} onChange={(e) => onChange(e.target.value)} onKeyDown={onKeyDown} placeholder={placeholder}
      className="w-full h-10 rounded-md bg-surface-container px-m text-on-surface text-[0.9375rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
  )
}

export function TextArea({ value, onChange, placeholder, rows = 4, mono, ariaLabel }: { value: string; onChange: (v: string) => void; placeholder?: string; rows?: number; mono?: boolean; ariaLabel?: string }) {
  const labelId = useFieldLabelId()
  const autoId = useId()
  // Prefer a Field's published label (aria-labelledby); else an explicit ariaLabel
  // for call-sites that wrap the control in their own (non-Field) section label.
  return (
    <textarea value={value} rows={rows} id={autoId} aria-labelledby={labelId} aria-label={!labelId ? ariaLabel : undefined} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
      className={`w-full rounded-md bg-surface-container px-m py-2 text-on-surface text-[0.9375rem] placeholder:text-on-surface-low outline-none resize-y focus:ring-2 focus:ring-inset focus:ring-primary/50 ${mono ? 'font-mono text-[0.8125rem]' : ''}`} />
  )
}

export function DateInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const labelId = useFieldLabelId()
  const autoId = useId()
  return (
    <input type="date" value={value} id={autoId} aria-labelledby={labelId} onChange={(e) => onChange(e.target.value)}
      className="h-10 rounded-md bg-surface-container px-m text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]" />
  )
}

/** Styled native select — matches the TextInput chrome. */
export function Select({ value, onChange, options, disabled, name }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; disabled?: boolean; name?: string }) {
  const labelId = useFieldLabelId()
  const autoId = useId()
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} name={name} id={name || autoId} aria-labelledby={!name && labelId ? labelId : undefined}
      className="w-full h-10 appearance-none rounded-md bg-surface-container pl-m pr-8 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 disabled:opacity-50 [color-scheme:dark]">
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

// The canonical Segmented now lives in ui/Segmented.tsx; re-exported here so the
// existing form call-sites (status/priority pickers) keep their import path.
export { Segmented, type SegOption } from '../../ui/Segmented'

/** Tag / chip input — type + Enter (or comma) to add, × to remove. */
export function ChipInput({ values, onChange, placeholder, max, suggestions }: { values: string[]; onChange: (v: string[]) => void; placeholder?: string; max?: number; suggestions?: string[] }) {
  const [draft, setDraft] = useState('')
  const listId = useId()
  const labelId = useFieldLabelId()
  const add = () => {
    const v = draft.trim().replace(/,$/, '')
    if (v && !values.includes(v) && (!max || values.length < max)) onChange([...values, v])
    setDraft('')
  }
  // Suggest existing values the user hasn't already added (autocomplete to avoid
  // near-duplicate fragments like "Kubernetes" vs "kubernetes").
  const remaining = suggestions?.filter((s) => !values.includes(s)) ?? []
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-surface-container px-2 py-2 min-h-10 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary/50">
      {values.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-7 text-on-surface-var text-[0.8125rem]">
          {v}
          <button type="button" onClick={() => onChange(values.filter((x) => x !== v))} className="text-on-surface-low hover:text-on-surface"><X size={12} /></button>
        </span>
      ))}
      <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={values.length ? '' : placeholder}
        list={remaining.length ? listId : undefined} name={`chip-${listId}`} aria-labelledby={labelId} aria-label={labelId ? undefined : 'Add a tag'}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add() } else if (e.key === 'Backspace' && !draft && values.length) onChange(values.slice(0, -1)) }}
        onBlur={add}
        className="flex-1 min-w-[80px] bg-transparent text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none" />
      {remaining.length > 0 && <datalist id={listId}>{remaining.map((s) => <option key={s} value={s} />)}</datalist>}
    </div>
  )
}

/** Dependency picker — choose prerequisite tasks for `selfId`. Candidates that
 *  would create a cycle (the task already depends on the current one, directly
 *  or transitively) are disabled with a reason, so the graph stays acyclic
 *  even though the backend doesn't enforce it. */
export function DependencyEditor({ selfId, allTasks, value, onChange }: {
  selfId: string | undefined
  allTasks: TaskItem[]
  value: string[]
  onChange: (ids: string[]) => void
}) {
  const [q, setQ] = useState('')
  const [picking, setPicking] = useState(false)
  const searchId = useId()
  const byId = useMemo(() => new Map(allTasks.map((t) => [t.id, t])), [allTasks])

  // candidate map includes a provisional self node so cycle checks work pre-save
  const m = useMemo(() => {
    const dm = depMap(allTasks)
    if (selfId && !dm.has(selfId)) dm.set(selfId, value)
    else if (selfId) dm.set(selfId, value)
    return dm
  }, [allTasks, selfId, value])

  const candidates = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return allTasks
      .filter((t) => t.id !== selfId && !value.includes(t.id))
      .filter((t) => !needle || t.title.toLowerCase().includes(needle))
      .map((t) => ({ task: t, cyclic: selfId ? wouldCycle(m, selfId, t.id) : false }))
      .slice(0, 40)
  }, [allTasks, selfId, value, q, m])

  return (
    <div className="flex flex-col gap-2">
      {value.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {value.map((id) => {
            const t = byId.get(id)
            const sm = statusMeta(t?.status)
            return (
              <div key={id} className="group flex items-center gap-s rounded-md bg-surface-container px-2 py-1.5">
                <sm.icon size={14} className="shrink-0" style={{ color: sm.tone }} />
                <span className="flex-1 truncate text-on-surface text-[0.875rem]">{t?.title ?? id}</span>
                <button type="button" onClick={() => onChange(value.filter((x) => x !== id))} className="shrink-0 opacity-0 group-hover:opacity-100 text-on-surface-low hover:text-danger transition-opacity"><X size={14} /></button>
              </div>
            )
          })}
        </div>
      )}
      {/* The prerequisite picker buds OUT of the "Add prerequisite" button (a
          liquid droplet splitting off the control) rather than appearing from
          nowhere; both occupy the same slot so it grows from the top edge. */}
      <AnimatePresence mode="wait" initial={false}>
        {picking ? (
          <Bud key="picker" from="top" className="bg-surface-container p-2">
            <div className="relative mb-1.5">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
              <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a prerequisite task" aria-label="Find a prerequisite task" name={`dep-search-${searchId}`}
                className="w-full h-8 rounded-md bg-surface pl-8 pr-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            </div>
            <div className="max-h-52 overflow-y-auto flex flex-col gap-0.5">
              {candidates.length === 0 ? <div className="px-2 py-3 text-on-surface-low text-[0.8125rem]">No tasks to add.</div> : candidates.map(({ task, cyclic }) => {
                const sm = statusMeta(task.status)
                return (
                  <button key={task.id} type="button" disabled={cyclic}
                    onClick={() => { onChange([...value, task.id]); setQ(''); }}
                    className="flex items-center gap-s rounded-md px-2 py-1.5 text-left transition-colors enabled:hover:bg-surface-high disabled:opacity-45 disabled:cursor-not-allowed">
                    <sm.icon size={14} className="shrink-0" style={{ color: sm.tone }} />
                    <span className="flex-1 truncate text-on-surface text-[0.8125rem]">{task.title}</span>
                    {cyclic && <span className="shrink-0 inline-flex items-center gap-1 text-warn text-[0.7rem]" title="Would create a dependency cycle"><AlertTriangle size={11} /> cycle</span>}
                  </button>
                )
              })}
            </div>
            <div className="mt-1.5 flex justify-end"><button type="button" onClick={() => { setPicking(false); setQ('') }} className="text-on-surface-low text-[0.8125rem] hover:text-on-surface px-2 py-1">Done</button></div>
          </Bud>
        ) : (
          <motion.button key="addbtn" type="button" onClick={() => setPicking(true)}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}
            className="inline-flex items-center gap-1.5 self-start rounded-md bg-surface-container px-m h-9 text-on-surface-var text-[0.8125rem] hover:bg-surface-high transition-colors"><Plus size={14} /> Add prerequisite</motion.button>
        )}
      </AnimatePresence>
    </div>
  )
}

/** Checklist editor — used for exit criteria ({description, met}) and action
 *  plan ({description, completed}). `doneKey` selects which boolean to toggle. */
export function ChecklistEditor<T extends { description?: string }>({ items, onChange, doneKey, placeholder, ordered }: {
  items: T[]
  onChange: (items: T[]) => void
  doneKey: keyof T
  placeholder: string
  ordered?: boolean
}) {
  const [draft, setDraft] = useState('')
  const addId = useId()
  const add = () => {
    const v = draft.trim()
    if (!v) return
    onChange([...items, { description: v, [doneKey]: false } as unknown as T])
    setDraft('')
  }
  const toggle = (i: number) => onChange(items.map((it, idx) => idx === i ? { ...it, [doneKey]: !it[doneKey] } as T : it))
  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i))
  // A stable per-item key for the reorder path — items may lack an id, so index-tag
  // them for the drag session (order is what we're editing, identity is positional).
  const keyed = items.map((it, i) => ({ it, i }))

  // One row's inner content, shared by the ordered (drag-reorderable) and the
  // plain (checkbox) paths so they stay visually identical.
  const rowInner = (it: T, i: number, dragHandle: ReactNode) => (
    <>
      {dragHandle}
      <button type="button" onClick={() => toggle(i)} className="shrink-0 inline-flex size-5 items-center justify-center rounded-sm border transition-colors" style={{ borderColor: it[doneKey] ? 'var(--color-ok)' : 'var(--color-outline-variant)', background: it[doneKey] ? 'var(--color-ok)' : 'transparent' }}>{it[doneKey] ? <Check size={13} className="text-white" /> : null}</button>
      <span className={`flex-1 text-[0.875rem] ${it[doneKey] ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{String(it.description ?? '')}</span>
      <button type="button" onClick={() => remove(i)} aria-label="Remove" className="shrink-0 opacity-0 group-hover:opacity-100 text-on-surface-low hover:text-danger transition-opacity"><X size={14} /></button>
    </>
  )
  const rowClass = 'group flex items-center gap-s rounded-md bg-surface-container px-2 py-1.5'

  return (
    <div className="flex flex-col gap-1.5">
      {/* Ordered lists (e.g. an action plan) are drag-to-reorder via the shared
          Reorderable primitive — the grip is the real handle, order edits persist
          through onChange. Unordered checklists (exit criteria) keep the plain
          bud-in/out list since their order is not meaningful. */}
      {ordered ? (
        <Reorderable
          items={keyed}
          getKey={({ i }) => String(i)}
          onReorder={(next) => onChange(next.map((k) => k.it))}
          renderItem={({ it, i }) => (
            <div className={`${rowClass} mb-1.5`}>
              {rowInner(it, i, <GripVertical size={14} className="shrink-0 cursor-grab text-on-surface-low active:cursor-grabbing" />)}
            </div>
          )}
        />
      ) : (
        <AnimatePresence initial={false}>
        {items.map((it, i) => (
          <motion.div key={i} layout
            initial={{ opacity: 0, scaleY: 0.4, borderRadius: 'var(--radius-pill)' }}
            animate={{ opacity: 1, scaleY: 1, borderRadius: 'var(--radius-md)' }}
            exit={{ opacity: 0, scaleY: 0.4 }} transition={spring.spatialDefault} style={{ originY: 0 }}
            className={rowClass}>
            {rowInner(it, i, null)}
          </motion.div>
        ))}
        </AnimatePresence>
      )}
      <div className="flex items-center gap-s">
        {ordered ? <GripVertical size={14} className="text-on-surface-low shrink-0 opacity-40" /> : <span className="size-5 shrink-0" />}
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} aria-label={placeholder} name={`checklist-add-${addId}`}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} onBlur={add}
          className="flex-1 h-9 rounded-md bg-surface-container px-m text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <IconButton icon={Plus} label="Add" size={32} onClick={add} />
      </div>
    </div>
  )
}

/** Editor for one note channel (general / research / execution). Each note is
 *  {content}; existing notes are removable, and an input adds new ones. Empty
 *  notes are dropped. Keeps any backend-supplied timestamp on existing entries. */
export function NotesEditor({ items, onChange, placeholder }: {
  items: TaskNote[]
  onChange: (items: TaskNote[]) => void
  placeholder: string
}) {
  const [draft, setDraft] = useState('')
  const addId = useId()
  const add = () => {
    const v = draft.trim()
    if (!v) return
    onChange([...items, { content: v }])
    setDraft('')
  }
  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i))
  return (
    <div className="flex flex-col gap-1.5">
      <AnimatePresence initial={false}>
      {items.map((n, i) => (
        <motion.div key={i} layout
          initial={{ opacity: 0, scaleY: 0.4, borderRadius: 'var(--radius-pill)' }}
          animate={{ opacity: 1, scaleY: 1, borderRadius: 'var(--radius-md)' }}
          exit={{ opacity: 0, scaleY: 0.4 }} transition={spring.spatialDefault} style={{ originY: 0 }}
          className="group flex items-start gap-s rounded-md bg-surface-container px-2 py-1.5">
          <span className="flex-1 text-on-surface text-[0.875rem] whitespace-pre-wrap">{n.content}</span>
          <button type="button" onClick={() => remove(i)} aria-label="Remove note" className="shrink-0 opacity-0 group-hover:opacity-100 text-on-surface-low hover:text-danger transition-opacity"><X size={14} /></button>
        </motion.div>
      ))}
      </AnimatePresence>
      <div className="flex items-center gap-s">
        <span className="size-5 shrink-0" />
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} aria-label={placeholder} name={`note-add-${addId}`}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} onBlur={add}
          className="flex-1 h-9 rounded-md bg-surface-container px-m text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <IconButton icon={Plus} label="Add note" size={32} onClick={add} />
      </div>
    </div>
  )
}
