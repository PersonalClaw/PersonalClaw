import { useState } from 'react'
import { AnimatePresence, motion, LayoutGroup } from 'framer-motion'
import { MessageSquare, GripVertical } from 'lucide-react'
import type { TaskItem } from '../../lib/api'
import { STATUSES, priorityMeta, dueMeta, exitDoneCount } from './taskMeta'
import { prereqIds } from './dag'
import { spring, bounce, expr } from '../../design/motion'
import { CollapseColumnButton, CollapsedBoardColumn, boardGridTemplate, useBoardCollapse } from '../../ui/BoardCollapse'

/** Kanban board, designed as a fixed SHELL: every status column is always on
 *  screen. The board fills the available height. Empty columns AUTO-collapse
 *  to a slim rail — and any column can be MANUALLY collapsed/expanded (header
 *  chevron / click the rail; persisted in localStorage) — shared mechanism
 *  with the chat-history tag board (ui/BoardCollapse). Rails stay collapsed
 *  during a drag and are themselves drop targets (highlight on dragover; an
 *  auto-collapsed rail expands naturally after the drop lands). Only each
 *  column's task list scrolls independently. Cards drag between columns to
 *  change status (persisted by the parent via onMove). */
export function TaskBoard({ tasks, onOpen, onMove }: {
  tasks: TaskItem[]
  onOpen: (id: string) => void
  onMove: (id: string, status: string) => void
}) {
  const [dragId, setDragId] = useState<string | null>(null)
  const [overCol, setOverCol] = useState<string | null>(null)
  const collapse = useBoardCollapse('board-collapsed:tasks')

  const columns = STATUSES.map((s) => ({ s, items: tasks.filter((t) => t.status === s.key) }))
  // Explicit per-column template (not auto-fit) is what lets collapsed rails
  // take a slim fixed slot while populated columns share the remaining width.
  // Deliberately NOT a function of drag state — see ui/BoardCollapse.
  const template = boardGridTemplate(columns.map((c) => collapse.isCollapsed(c.s.key, c.items.length)))

  return (
    // LayoutGroup so a card MOVED between columns animates flying to its new home
    // (shared layout across the sibling columns), not just popping into place.
    <LayoutGroup>
    <div
      className="grid h-full gap-m overflow-x-auto"
      style={{ gridTemplateColumns: template, gridAutoRows: 'minmax(180px, 1fr)' }}
    >
      {columns.map(({ s, items }) => {
        const isOver = overCol === s.key && dragId != null
        const dropHandlers = {
          onDragOver: (e: React.DragEvent) => { if (dragId) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setOverCol(s.key) } },
          onDragLeave: (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverCol((c) => (c === s.key ? null : c)) },
          onDrop: (e: React.DragEvent) => { e.preventDefault(); const id = e.dataTransfer.getData('text/plain') || dragId; if (id) onMove(id, s.key); setOverCol(null); setDragId(null) },
        }
        if (collapse.isCollapsed(s.key, items.length)) {
          // Shared slim rail — itself the drop target for its column: it stays
          // collapsed during the drag, highlights on dragover; auto-collapsed
          // (empty) rails expand on the next render once a drop moves an item
          // in, user-collapsed ones stay put (count ticks up). Click expands.
          return (
            <CollapsedBoardColumn key={s.key} icon={s.icon} label={s.label} count={items.length}
              tone={s.tone} {...dropHandlers}
              onExpand={() => collapse.toggle(s.key, items.length)}
              style={{
                background: isOver ? `color-mix(in srgb, ${s.tone} 12%, var(--color-surface-container))` : 'color-mix(in srgb, var(--color-surface-container) 40%, transparent)',
                outline: isOver ? `1.5px dashed ${s.tone}` : '1.5px solid transparent',
              }} />
          )
        }
        return (
          // The receiving column springs UP + brightens its dashed ring while a
          // card hovers over it — a physical "ready to catch" cue; scaled by expr.
          <motion.div key={s.key}
            animate={{ scale: isOver ? 1 + expr(0.012, 0.3) : 1 }}
            transition={bounce.subtle}
            {...dropHandlers}
            className="flex min-h-0 flex-col rounded-xl p-2 transition-colors"
            style={{ background: isOver ? `color-mix(in srgb, ${s.tone} 12%, var(--color-surface-container))` : 'color-mix(in srgb, var(--color-surface-container) 40%, transparent)', outline: isOver ? `1.5px dashed ${s.tone}` : '1.5px solid transparent' }}>
            <div className="mb-2 flex items-center gap-s px-1 pt-1 shrink-0">
              <s.icon size={15} style={{ color: s.tone }} />
              <span className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 550' }}>{s.label}</span>
              {/* count pops on change (a card arriving/leaving reads as an event) */}
              <motion.span key={items.length} initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={bounce.playful}
                className="flex-1 text-on-surface-low text-[0.75rem] tabular-nums">{items.length}</motion.span>
              <CollapseColumnButton onCollapse={() => collapse.toggle(s.key, items.length)} />
            </div>
            <div className="flex flex-1 min-h-0 flex-col gap-s overflow-y-auto pr-0.5">
              {items.length === 0 ? (
                <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-outline-variant/30 py-6 text-on-surface-low text-[0.75rem]">{isOver ? 'Drop here' : '—'}</div>
              ) : (
                <AnimatePresence initial={false}>
                  {items.map((t) => (
                    <BoardCard key={t.id} t={t} tone={s.tone}
                      onOpen={() => onOpen(t.id)}
                      // setData must run synchronously inside dragstart, but the
                      // STATE set is deferred a frame: flushing a re-render (and
                      // Framer's lift animation) while the browser is still
                      // committing the native drag makes Chrome cancel it
                      // (instant dragend — drag "does nothing").
                      onDragStart={(e) => { e.dataTransfer.setData('text/plain', t.id); e.dataTransfer.effectAllowed = 'move'; requestAnimationFrame(() => setDragId(t.id)) }}
                      onDragEnd={() => { setDragId(null); setOverCol(null) }}
                      dragging={dragId === t.id} />
                  ))}
                </AnimatePresence>
              )}
            </div>
          </motion.div>
        )
      })}
    </div>
    </LayoutGroup>
  )
}

function BoardCard({ t, tone, onOpen, onDragStart, onDragEnd, dragging }: {
  t: TaskItem; tone: string; onOpen: () => void
  onDragStart: (e: React.DragEvent) => void; onDragEnd: () => void; dragging: boolean
}) {
  const pm = priorityMeta(t.priority)
  const due = dueMeta(t.due)
  const exit = t.exit_criteria ?? []
  const readOnly = t.provider === 'project'
  // Native HTML5 drag lives on a PLAIN wrapper (Framer's motion.div reserves
  // onDragStart/onDragEnd for its own pan gesture). The inner motion.div carries
  // the layoutId, so the card still FLIES to its new column on a status move, and
  // the lift/tilt/shadow while grabbed — cleanly separated from the DnD transport.
  // select-none: the WHOLE card must initiate the drag — selectable text (title,
  // pills) is its own native drag source (drag-selected-text), which steals the
  // gesture so only empty card area dragged. Cards are click-to-open anyway.
  return (
    <div draggable={!readOnly} onDragStart={onDragStart} onDragEnd={onDragEnd} onClick={onOpen}
      className={`shrink-0 select-none ${readOnly ? 'cursor-pointer' : 'cursor-grab active:cursor-grabbing'}`}>
    <motion.div
      layout
      layoutId={`board-card-${t.id}`}
      initial={{ opacity: 0, scale: 0.94 }}
      animate={{
        // While grabbed, the card LIFTS toward the viewer (scale + shadow) and
        // tilts a hair — a physical "picked up" feel — instead of just fading.
        // Depth scales via expr(); the source slot dims to a ghost.
        opacity: dragging ? 0.5 : 1,
        scale: dragging ? 1 + expr(0.04, 0.3) : 1,
        rotate: dragging ? -expr(2, 0) : 0,
        boxShadow: dragging ? 'var(--shadow-lift)' : 'var(--shadow-rest)',
      }}
      exit={{ opacity: 0, scale: 0.9, transition: spring.spatialFast }}
      transition={spring.spatialDefault}
      whileHover={readOnly ? undefined : { y: -expr(2, 0.3) }}
      className="group relative overflow-hidden rounded-lg bg-surface-container px-m py-2.5 transition-[background-color] hover:bg-surface-high">
      <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: tone }} />
      {/* pointer-events-none: the grip is a pure affordance — an SVG that eats
          the mousedown would keep the wrapper (the ONLY drag source) from
          initiating the drag. */}
      {!readOnly && <GripVertical size={13} className="pointer-events-none absolute right-1.5 top-2 text-on-surface-low opacity-0 group-hover:opacity-100 transition-opacity" />}
      <div className="pl-1.5">
        <div className="text-on-surface text-[0.875rem] leading-snug line-clamp-2 pr-4" style={{ fontVariationSettings: '"wght" 500' }}>{t.title}</div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center rounded-pill px-2 h-5 text-[0.65rem]" style={{ background: `color-mix(in srgb, ${pm.tone} 16%, transparent)`, color: pm.tone }}>{pm.label}</span>
          {due && <span className="inline-flex items-center rounded-pill px-2 h-5 text-[0.65rem]" style={{ background: `color-mix(in srgb, ${due.tone} 14%, transparent)`, color: due.tone }}>{due.label}</span>}
          {(t.labels ?? []).slice(0, 1).map((l) => <span key={l} className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-var text-[0.65rem]">{l}</span>)}
        </div>
        {(exit.length > 0 || prereqIds(t).length > 0 || (t.comment_count ?? 0) > 0) && (
          <div className="mt-1.5 flex items-center gap-m text-on-surface-low text-[0.65rem]">
            {exit.length > 0 && <span>{exitDoneCount(exit)}/{exit.length} criteria</span>}
            {prereqIds(t).length > 0 && <span>{prereqIds(t).length} deps</span>}
            {(t.comment_count ?? 0) > 0 && <span className="inline-flex items-center gap-1"><MessageSquare size={10} /> {t.comment_count}</span>}
          </div>
        )}
      </div>
    </motion.div>
    </div>
  )
}
