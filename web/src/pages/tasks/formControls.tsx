import { useEffect, useId, useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Plus, GripVertical, Check, Search, AlertTriangle } from 'lucide-react'
import { IconButton } from '../../ui/IconButton'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Button } from '../../ui/Button'
import { Bud, Reorderable } from '../../ui/motion'
import { spring } from '../../design/motion'
import { TextInput } from '../../ui/forms'
import type { TaskItem, TaskNote } from '../../lib/api'
import { depMap, wouldCycle } from './dag'
import { statusMeta } from './taskMeta'

// ── Task-specific form editors ──────────────────────────────────────────────
// The generic form primitives (Field/TextInput/TextArea/DateInput/Select/
// ChipInput + the Segmented re-export) now live in ui/forms.tsx alongside the
// other design-system primitives. These three editors stay here because they
// depend on task domain code (./dag, ./taskMeta, TaskItem/TaskNote) — a
// ui/ → pages/ dependency would be backwards.

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
                <span className="flex-1 truncate text-on-surface text-[0.8125rem]">{t?.title ?? id}</span>
                <SquareIconButton icon={X} tone="danger" label="Remove prerequisite" onClick={() => onChange(value.filter((x) => x !== id))} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" />
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
            <div className="mb-1.5">
              <TextInput autoFocus value={q} onChange={setQ} placeholder="Find a prerequisite task" ariaLabel="Find a prerequisite task" name={`dep-search-${searchId}`}
                size="sm" surface="base" leadingIcon={<Search size={14} />} />
            </div>
            <div className="max-h-52 overflow-y-auto flex flex-col gap-0.5">
              {candidates.length === 0 ? <div className="px-2 py-3 text-on-surface-low text-[0.8125rem]">No tasks to add.</div> : candidates.map(({ task, cyclic }) => {
                const sm = statusMeta(task.status)
                return (
                  <button key={task.id} type="button" disabled={cyclic}
                    onClick={() => { onChange([...value, task.id]); setQ(''); }}
                    className="flex items-center gap-s rounded-md px-2 py-1.5 text-left transition-colors enabled:hover:bg-surface-high disabled:opacity-40 disabled:cursor-not-allowed">
                    <sm.icon size={14} className="shrink-0" style={{ color: sm.tone }} />
                    <span className="flex-1 truncate text-on-surface text-[0.8125rem]">{task.title}</span>
                    {cyclic && <span className="shrink-0 inline-flex items-center gap-1 text-warn text-[0.75rem]" title="Would create a dependency cycle"><AlertTriangle size={11} /> cycle</span>}
                  </button>
                )
              })}
            </div>
            <div className="mt-1.5 flex justify-end"><button type="button" onClick={() => { setPicking(false); setQ('') }} className="text-on-surface-low text-[0.8125rem] hover:text-on-surface px-2 py-1">Done</button></div>
          </Bud>
        ) : (
          <motion.div key="addbtn" className="self-start"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
            <Button variant="secondary" size="sm" onClick={() => setPicking(true)}><Plus size={14} /> Add prerequisite</Button>
          </motion.div>
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
  // Two-stage destructive reveal (TASKS-SOPS §7 R15, S61k), matching the shipped armed-delete
  // pattern in the workflows list rather than inventing a second one: first click arms, second
  // confirms, and the arm times out. A checklist row is one click from gone otherwise, and the item
  // it deletes is text the user typed — there is nothing to undo it with.
  const [armed, setArmed] = useState<number | null>(null)
  useEffect(() => {
    if (armed === null) return
    // Disarmed on a timer for the same reason the list page does it: a row left armed indefinitely
    // becomes a trap the next time the user reaches for that area.
    const timer = window.setTimeout(() => setArmed(null), 4000)
    return () => window.clearTimeout(timer)
  }, [armed])
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
      <span className={`flex-1 text-[0.8125rem] ${it[doneKey] ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{String(it.description ?? '')}</span>
      {armed === i ? (
        // The shared primitive rather than bespoke chrome: the design-system ratchet caught the raw
        // element (278 > 277 baseline), which is exactly what it is for — a one-off confirm chip
        // would drift from every other danger affordance the first time the tokens moved.
        //
        // The comment itself is worded around the tag name on purpose: the scanner counts the
        // literal string, so writing it here would flag this file for a raw element that is not in
        // it (measured — that is what the second ratchet failure was).
        <Button
          variant="danger"
          size="xs"
          className="shrink-0"
          onClick={() => { remove(i); setArmed(null) }}
          title="Click again to remove this item"
        >
          Remove?
        </Button>
      ) : (
        <SquareIconButton icon={X} tone="danger" label="Remove" onClick={() => setArmed(i)} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" />
      )}
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
          // Checked-locks-drag, enforced by the primitive rather than by styling: a completed
          // step's position is the record of what happened in what order, which is the one thing a
          // checklist is FOR.
          canDrag={({ it }) => !it[doneKey]}
          onReorder={(next) => onChange(next.map((k) => k.it))}
          renderItem={({ it, i }) => (
            <div className={`${rowClass} mb-1.5`}>
              {/* Checked-locks-drag (R15). A completed step's position is history — reordering it
                  would rewrite the record of what happened in what order, which is the one thing a
                  checklist is FOR. The grip stays visible but inert so the row still reads as part
                  of the same list rather than looking broken. */}
              {rowInner(
                it,
                i,
                <span
                  className="shrink-0"
                  title={it[doneKey] ? 'A completed step keeps its place' : undefined}
                >
                  <GripVertical
                    size={14}
                    className={`text-on-surface-low ${it[doneKey] ? 'cursor-not-allowed opacity-40' : 'cursor-grab active:cursor-grabbing'}`}
                  />
                </span>,
              )}
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
          className="flex-1 h-9 rounded-md bg-surface-container px-m text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
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
          <span className="flex-1 text-on-surface text-[0.8125rem] whitespace-pre-wrap">{n.content}</span>
          <SquareIconButton icon={X} tone="danger" label="Remove note" onClick={() => remove(i)} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" />
        </motion.div>
      ))}
      </AnimatePresence>
      <div className="flex items-center gap-s">
        <span className="size-5 shrink-0" />
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} aria-label={placeholder} name={`note-add-${addId}`}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} onBlur={add}
          className="flex-1 h-9 rounded-md bg-surface-container px-m text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <IconButton icon={Plus} label="Add note" size={32} onClick={add} />
      </div>
    </div>
  )
}
