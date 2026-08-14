import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { FolderKanban, ChevronDown, Check, Plus } from 'lucide-react'
import { api, type ProjectItem } from '../lib/api'
import { menuCursorKeydown, useMenuCursor } from '../lib/useMenuCursor'
import { overlayEnter, physics } from '../design/motion'

/** A compact project chooser for the Goal Loop + Code create flows.
 *
 *  The user picks which Project this work scopes under, or leaves it on
 *  "New project" (value ""), in which case the backend auto-creates one named
 *  from the goal/task at intake (the "initiate a project for the session"
 *  behavior). The chosen id is threaded into the create payload as `project_id`.
 *
 *  Controlled: `value` is the project id ("" = auto/new). Loads the project list
 *  lazily on first open so the create page paints instantly.
 */
export function ProjectPicker({ value, onChange, disabled, emptyLabel, emptyHint, openSignal }: {
  value: string
  onChange: (projectId: string) => void
  disabled?: boolean
  // The label/hint for the empty ("") option. Defaults suit the loop create flow
  // (backend auto-creates a project from the task). Chat passes "No project" / no hint
  // since an unbound chat scopes to nothing — same component, no dual path.
  emptyLabel?: string
  emptyHint?: string
  // Monotonic counter — each increment opens the picker (drives the "/project"
  // slash command). Ignored on mount / 0.
  openSignal?: number
}) {
  const [open, setOpen] = useState(false)
  const [projects, setProjects] = useState<ProjectItem[] | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  // Archived projects are "put away" — don't offer them as a target for NEW work.
  // (The Projects page still manages them; this picker is for active scoping.) An
  // already-selected project that's since been archived stays shown via `current`
  // below — we just don't list it as a fresh option below. Computed HERE, above the
  // listeners, because the cursor hook needs the option count.
  const selectable = projects?.filter((p) => p.status !== 'archived' || p.id === value) ?? null
  // This popup declared `role="listbox"` and implemented none of what that promises: measured on
  // `#/chat`, focus stayed on the trigger, ArrowDown did nothing, and all 6 options were separate
  // tab stops. Options are the empty row + `selectable`, and the cursor opens on the SELECTED one.
  const optionCount = 1 + (selectable?.length ?? 0)
  const selectedIdx = value ? Math.max(0, 1 + (selectable?.findIndex((pr) => pr.id === value) ?? -1)) : 0
  const { move, restoreFocus, tabIndexFor } = useMenuCursor({
    containerRef: listRef, count: optionCount, openKey: open ? 'open' : null, initialIndex: selectedIdx,
  })
  const closeAndReturnFocus = useCallback(() => { setOpen(false); restoreFocus() }, [restoreFocus])
  const lastSignal = useRef(openSignal ?? 0)
  useEffect(() => {
    if (openSignal === undefined || openSignal === lastSignal.current) return
    lastSignal.current = openSignal
    if (openSignal > 0) setOpen(true)
  }, [openSignal])

  // Resolve a non-empty value on mount (not just on open) so the chip can show the
  // real name immediately AND a STALE value (e.g. the active project was deleted in
  // another tab) can self-heal to "" rather than sending a dead id.
  useEffect(() => {
    if (projects || (!open && !value)) return
    api.projects().then(setProjects).catch(() => setProjects([]))
  }, [open, value, projects])

  // Once the list is loaded, if our value names a project that no longer exists,
  // reset to "New project" (""), so the picker never points at a deleted project.
  useEffect(() => {
    if (value && projects && !projects.some((p) => p.id === value)) onChange('')
  }, [value, projects, onChange])

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); restoreFocus(); return }
      menuCursorKeydown(e, { move, dismiss: closeAndReturnFocus })
    }
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('pointerdown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open, move, restoreFocus, closeAndReturnFocus])

  const current = value ? projects?.find((p) => p.id === value) : null
  const label = value ? (current?.name ?? 'Project') : (emptyLabel ?? 'New project')

  return (
    <div ref={ref} className="relative">
      {/* The name carries the DIMENSION, composed exactly as `PillButton` and `HeaderModePill`
          compose theirs (`${dimension}: ${label}`). This trigger shows a value and nothing else, and
          its `title` is not the accessible name while the button has text — so it announced its value
          alone. Measured on `#/code`, that value is "New project", which is ALSO the name of the
          Project-kind tab sitting 8px to its right: two controls in one row with the identical
          accessible name and different jobs. The guard is not theoretical here either — `label` falls
          back to the bare word "Project" when a bound project is missing from the list, which would
          otherwise announce "Project: Project". Sighted users see no change. */}
      <button type="button" disabled={disabled} onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox" aria-expanded={open}
        aria-label={label === 'Project' ? 'Project' : `Project: ${label}`}
        title="Choose the project this work scopes under"
        className="inline-flex h-8 items-center gap-1.5 rounded-pill bg-surface-high px-2.5 text-[0.8125rem] text-on-surface-var transition-colors hover:bg-surface-highest disabled:opacity-50">
        <FolderKanban size={14} className="shrink-0 text-primary" />
        {/* 🔴 THE WIDEST CONTROL IN A HEADER ROW THAT CANNOT SHRINK. `LoopComposer`'s row is
            `[&>*]:shrink-0` **by design** — every control there must keep its natural size — and its
            own comment records the consequence: "the row can only fit by each wide control having its
            OWN collapse strategy — which only the granularity dial had". This is that strategy for
            this control, and it is the app's established one (`hidden sm:inline` beside a glyph, as in
            `ui/FilterMenu`): below `sm` the trigger is the folder glyph + chevron.
            Measured at 390px: the trigger was **132px** (`Project: New project`), the row overflowed its
            slot, and axe reported `target-size` **serious** on `#/loops`, `#/loop` and `#/code` because
            the overflow left controls overlapping. Nothing is lost when the text hides — the accessible
            name is already on the button's `aria-label` (`Project: <label>`) and the `title` explains
            the control, so a screen-reader and a hover user both still get the full label. */}
        <span className="hidden sm:inline max-w-[140px] truncate">{label}</span>
        {/* chevron flips on a spring so open/close settles with life */}
        <motion.span className="shrink-0 opacity-60" animate={{ rotate: open ? 180 : 0 }} transition={physics.snappy}>
          <ChevronDown size={13} />
        </motion.span>
      </button>
      <AnimatePresence>
      {open && (
        <motion.div ref={listRef} role="listbox" aria-orientation="vertical"
          // 🔴 axe (serious) `aria-input-field-name` before this: a listbox is an ARIA INPUT FIELD,
          // so unlike a `role="menu"` it MUST carry a name — and this one had none.
          aria-label="Project"
          variants={overlayEnter} initial="initial" animate="animate" exit="exit"
          style={{ transformOrigin: 'top left' }}
          className="absolute z-30 mt-1 max-h-[300px] w-[240px] overflow-y-auto rounded-lg border border-outline-variant/50 bg-surface-container p-1 shadow-menu">
          {/* The empty option. Loop flow: "New project (auto-named)" — backend names it
              from the goal/task. Chat passes emptyLabel="No project" (unbound). */}
          <button type="button" role="option" aria-selected={!value} tabIndex={tabIndexFor(0)}
            onClick={() => { onChange(''); closeAndReturnFocus() }}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[0.8125rem] text-on-surface-var hover:bg-surface-high">
            <Plus size={14} className="shrink-0 text-on-surface-low" />
            <span className="min-w-0 flex-1 truncate">{emptyLabel ?? 'New project'} {(emptyHint ?? '(auto-named)') && <span className="text-on-surface-low/70">{emptyHint ?? '(auto-named)'}</span>}</span>
            {!value && <Check size={13} className="shrink-0 text-primary" />}
          </button>
          {selectable === null ? (
            <div className="px-2 py-2 text-on-surface-low text-[0.75rem]">Loading…</div>
          ) : selectable.length === 0 ? (
            <div className="px-2 py-2 text-on-surface-low text-[0.75rem]">No existing projects.</div>
          ) : (
            <>
              {/* 🔴 axe (serious) `color-contrast` measured this at 3.62:1 — a 9pt uppercase label
                  needs 4.5. The `/70` alpha on an already-low ink was the whole cause; dropping it
                  takes the same token to 5.93:1 and changes nothing else. Found while driving the
                  listbox for this cycle's keyboard work, and it was there before it. */}
              <div className="mt-1 border-t border-outline-variant/40 px-2 pt-1.5 pb-0.5 text-[0.75rem] uppercase tracking-wide text-on-surface-low">Existing</div>
              {selectable.map((p, i) => (
                <button key={p.id} type="button" role="option" aria-selected={value === p.id}
                  tabIndex={tabIndexFor(i + 1)}
                  onClick={() => { onChange(p.id); closeAndReturnFocus() }}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[0.8125rem] text-on-surface-var hover:bg-surface-high">
                  <FolderKanban size={14} className="shrink-0 text-on-surface-low" />
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  {/* only the currently-selected project can be archived + still listed */}
                  {p.status === 'archived' && <span className="shrink-0 text-on-surface-low/60 text-[0.75rem]">archived</span>}
                  {value === p.id && <Check size={13} className="shrink-0 text-primary" />}
                </button>
              ))}
            </>
          )}
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  )
}
