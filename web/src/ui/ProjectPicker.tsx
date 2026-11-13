import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { FolderKanban, ChevronDown, Check, Pin, Plus } from 'lucide-react'
import { api, type ProjectItem } from '../lib/api'
import { useProjectPins } from '../lib/defaultProject'
import { epochSeconds } from '../lib/epoch'
import { projectOrder } from '../lib/projectPulse'
import { ProjectMark } from './ProjectMark'
import { menuCursorKeydown, useMenuCursor } from '../lib/useMenuCursor'
import { InlineLoadError } from './ListScaffold'
import { overlayEnter, physics } from '../design/motion'

/** A compact project chooser for the create flows (a loop, a chat, a secret).
 *
 *  The user picks which Project this work scopes under, or leaves it on the empty option (value
 *  ""): no project, or what the caller's empty label says its flow does with none (a code
 *  session's own project, named from its task). The chosen id is threaded into the create payload
 *  as `project_id`.
 *
 *  Controlled: `value` is the project id ("" = the empty option). Loads the project list
 *  lazily on first open so the create page paints instantly.
 */
/** The New project dialog, the Projects list's own, loaded when a picker's "New project…" opens it. */
const NewProjectModal = lazy(() => import('../pages/projects/NewProjectDialog').then((m) => ({ default: m.NewProjectModal })))

/** The projects every project list offers, in the order every list takes: unarchived (and the
 *  one already chosen, archived or not), in `projectOrder` (the pins first, in their order, then
 *  latest activity, ties by name), the ones a loop made for itself after the owner's own. A picker
 *  reads a project's activity off its own `updated_at`. Exported so every picker lists the same
 *  rows. */
export function pickerProjects(projects: readonly ProjectItem[], pins: readonly string[], value = ''): ProjectItem[] {
  const at = new Map(projects.map((p) => [p.id, (epochSeconds(p.updated_at) ?? 0) * 1000]))
  const listed = projectOrder(projects.filter((p) => p.status !== 'archived' || p.id === value), pins, (id) => at.get(id) ?? 0)
  return [...listed.filter((p) => !p.made_for), ...listed.filter((p) => !!p.made_for)]
}

export function ProjectPicker({ value, onChange, disabled, emptyLabel, emptyHint, emptyMakesProject = false, openSignal, align = 'left' }: {
  value: string
  onChange: (projectId: string) => void
  disabled?: boolean
  // Which edge of the trigger the menu lines up with, as `Popover`'s `align`: 'right' for a picker
  // at the right of a row (a settings row's control), whose menu would otherwise run off the page.
  align?: 'left' | 'right'
  // The label/hint for the empty ("") option: "No project" and no hint unless the caller says what
  // its flow does with none (the loop composer's per kind, Secrets' "Every project"). Same
  // component, no dual path.
  emptyLabel?: string
  emptyHint?: string
  // The empty option makes a project of its own (the loop composer's code session and goal loop):
  // it then draws the + of making one, else the projects glyph.
  emptyMakesProject?: boolean
  // Monotonic counter — each increment opens the picker (drives the "/project"
  // slash command). Ignored on mount / 0.
  openSignal?: number
}) {
  const [open, setOpen] = useState(false)
  const [projects, setProjects] = useState<ProjectItem[] | null>(null)
  // #532: this read used to `.catch(() => setProjects([]))`, which cost more than a wrong sentence.
  // The list is ALSO the stale-value self-heal below, so an empty list on failure read as "your
  // bound project no longer exists" and fired `onChange('')` — a failed fetch silently discarded
  // the user's selection. Keeping `projects` null on failure leaves that effect inert.
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const ref = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const { pins } = useProjectPins()
  const [creating, setCreating] = useState(false)
  // Archived projects are "put away" — don't offer them as a target for NEW work.
  // (The Projects page still manages them; this picker is for active scoping.) An
  // already-selected project that's since been archived stays shown via `current`
  // below — we just don't list it as a fresh option below. Computed HERE, above the
  // listeners, because the cursor hook needs the option count.
  const selectable = projects ? pickerProjects(projects, pins, value) : null
  // This popup declared `role="listbox"` and implemented none of what that promises: measured on
  // `#/chat`, focus stayed on the trigger, ArrowDown did nothing, and all 6 options were separate
  // tab stops. Options are the empty row + `selectable` + "New project…", and the cursor opens on
  // the SELECTED one.
  const optionCount = 2 + (selectable?.length ?? 0)
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
    if (projects || loadErr || (!open && !value)) return
    api.projects().then(setProjects).catch(setLoadErr)
  }, [open, value, projects, loadErr])

  // Once the list is loaded, if our value names a project that no longer exists,
  // reset to the empty option (""), so the picker never points at a deleted project.
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
  const label = value ? (current?.name ?? 'Project') : (emptyLabel ?? 'No project')

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
        data-type="body-s"
        className="inline-flex h-8 items-center gap-1.5 rounded-pill bg-surface-high px-2.5 text-on-surface-var transition-colors hover:bg-surface-highest disabled:opacity-50">
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
          style={{ transformOrigin: align === 'right' ? 'top right' : 'top left' }}
          className={`absolute z-30 mt-1 max-h-[300px] w-[240px] overflow-y-auto rounded-lg border border-outline-variant/50 bg-surface-container p-1 shadow-menu${align === 'right' ? ' right-0' : ''}`}>
          {/* The empty option: "No project" unless the caller names what its flow does with none.
              No row but the last starts with "New project". */}
          <button type="button" role="option" aria-selected={!value} tabIndex={tabIndexFor(0)}
            onClick={() => { onChange(''); closeAndReturnFocus() }}
            data-type="body-s"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-on-surface-var hover:bg-surface-high">
            {emptyMakesProject
              ? <Plus size={14} className="shrink-0 text-on-surface-low" />
              : <FolderKanban size={14} data-empty-glyph className="shrink-0 text-on-surface-low" />}
            <span className="min-w-0 flex-1 truncate">{emptyLabel ?? 'No project'} {emptyHint && <span className="text-on-surface-low/70">{emptyHint}</span>}</span>
            {!value && <Check size={13} className="shrink-0 text-primary" />}
          </button>
          {loadErr !== null ? (
            // The shared rail, and it keeps the "New project" option above reachable — the picker is
            // still usable for the unbound case while the existing list is unknown.
            <div className="px-s"><InlineLoadError what="your projects" error={loadErr} /></div>
          ) : selectable === null ? (
            <div data-type="caption" className="px-2 py-2 text-on-surface-low">Loading…</div>
          ) : selectable.length === 0 ? (
            <div data-type="caption" className="px-2 py-2 text-on-surface-low">No existing projects.</div>
          ) : (
            <>
              {/* The projects sit under a plain rule, as New project… does: the rows say what they
                  are, so no eyebrow names them. */}
              <div aria-hidden data-picker-rule className="mx-2 my-1 h-px bg-outline-variant/40" />
              {selectable.map((p, i) => (
                <button key={p.id} type="button" role="option" aria-selected={value === p.id}
                  tabIndex={tabIndexFor(i + 1)}
                  onClick={() => { onChange(p.id); closeAndReturnFocus() }}
                  data-type="body-s"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-on-surface-var hover:bg-surface-high">
                  <ProjectMark name={p.name} size="sm" />
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  {/* only the currently-selected project can be archived + still listed */}
                  {p.status === 'archived' && <span data-type="caption" className="shrink-0 text-on-surface-low/60">archived</span>}
                  {pins.includes(p.id) && <Pin size={13} aria-hidden data-pinned className="shrink-0 text-on-surface-low" />}
                  {value === p.id && <Check size={13} className="shrink-0 text-primary" />}
                </button>
              ))}
            </>
          )}
          {/* Every project list ends with a way to make one, so a picker never dead-ends: the New
              project dialog, and the picker then holds what it made. Its rule is its own straight
              line, so it neither bends with the row's corners nor takes its hover. */}
          <div aria-hidden data-picker-rule className="mx-2 my-1 h-px bg-outline-variant/40" />
          <button type="button" role="option" aria-selected={false} tabIndex={tabIndexFor(optionCount - 1)}
            onClick={() => { setOpen(false); setCreating(true) }}
            data-type="body-s" data-new-project
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-on-surface-var hover:bg-surface-high">
            <Plus size={14} className="shrink-0 text-on-surface-low" />
            <span className="min-w-0 flex-1 truncate">New project…</span>
          </button>
        </motion.div>
      )}
      </AnimatePresence>
      {creating && (
        <Suspense fallback={null}>
          <NewProjectModal taken={(projects ?? []).map((p) => p.name)} onClose={() => { setCreating(false); restoreFocus() }}
            onCreated={(p) => { setCreating(false); setProjects((list) => (list ? [...list, p] : list)); onChange(p.id); restoreFocus() }} />
        </Suspense>
      )}
    </div>
  )
}
