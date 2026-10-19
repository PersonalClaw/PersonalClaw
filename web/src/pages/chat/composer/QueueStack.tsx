import { useState } from 'react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { ChevronDown, Clock, Pencil, PlayCircle, X } from 'lucide-react'
import { spring, expr } from '../../../design/motion'
import { IconButton } from '../../../ui/IconButton'
import type { QueuedMessage } from '../useChatStream'

/** The mid-stream message queue, shown directly above the composer. Each item is
 *  a message the user sent while a turn was streaming; the backend dispatches them
 *  FIFO as turns finish. A pending item can be cancelled (removes it server-side).
 *  Numbered so the send order is obvious. */
/** QueueStack: the queued-message deck rendered as PHYSICAL stacked cards
 *  (top = next to run), overlapping with a translateY/scale/opacity depth falloff
 *  (the Toaster/Sonner idiom), depth offsets scaled by `expr()`. Only the TOP card
 *  shows its full text + actions; deeper cards peek behind it, and expanding on hover
 *  fans them out. Reduced-motion / refined expressiveness collapses to a flat list.
 *
 *  Actions are honest to what the backend supports (cancel-only — there is no
 *  reorder/edit endpoint, so we DON'T fake persistent reorder): Cancel removes the
 *  item; Edit cancels it AND drops its text back into the composer to resend (no
 *  false "in-place edit" that would silently move it to the back of the FIFO). */
export function QueueStack({ items, onCancel, onEdit, onInterrupt, canInterrupt = false }: {
  items: QueuedMessage[]
  onCancel: (id: string) => void
  onEdit: (q: QueuedMessage) => void
  onInterrupt?: (id: string) => void
  canInterrupt?: boolean  // a turn is running → "Interrupt now" can promote + soft-stop
}) {
  const reduce = useReducedMotion()
  const [expanded, setExpanded] = useState(false)
  if (!items.length) return null
  // Flat list when reduced-motion OR a small queue (a single card needs no deck).
  const stacked = !reduce && items.length > 1 && !expanded
  // Depth falloff for the collapsed deck: each card behind the top peeks down a few
  // px + shrinks + fades, scaled by expressiveness (refined → a tighter, calmer deck).
  const peekY = expr(7, 0.4)      // px each deeper card drops
  const peekScale = expr(0.04, 0.5)
  const maxPeek = 3               // cards visibly peeking behind the top

  // Raw: the stack's caption header toggles only with two or more queued; QuietButton would fill on hover where it does nothing.
  const header = (
    <button type="button" onClick={() => items.length > 1 && setExpanded((e) => !e)}
      className={`flex items-center gap-1.5 px-1 text-[0.75rem] uppercase tracking-wide text-on-surface-low ${items.length > 1 ? 'hover:text-on-surface-var' : 'cursor-default'}`}>
      <Clock size={11} className="shrink-0" /> {items.length} queued · sent one at a time as each turn finishes
      {items.length > 1 && <ChevronDown size={11} className={`shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />}
    </button>
  )

  const card = (q: QueuedMessage, i: number, depth: number) => (
    <motion.div key={q.id} layout
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={stacked
        ? { opacity: depth === 0 ? 1 : Math.max(0, 1 - depth * 0.28), y: -depth * peekY, scale: 1 - depth * peekScale }
        : { opacity: 1, y: 0, scale: 1 }}
      exit={reduce ? undefined : { opacity: 0, y: 8, transition: spring.spatialFast }}
      transition={spring.spatialDefault}
      style={stacked ? { position: depth === 0 ? 'relative' : 'absolute', insetInline: 0, top: 0, zIndex: maxPeek - depth } : undefined}
      className="group/q flex items-center gap-2 rounded-lg border border-outline-variant/50 bg-surface-high/60 px-2.5 py-1.5 text-[0.8125rem]">
      <span className="shrink-0 tabular-nums text-on-surface-low">{i + 1}</span>
      <span className="min-w-0 flex-1 truncate text-on-surface" title={q.content}>{q.content}</span>
      {/* Actions only on the top card in stacked mode (deeper cards are non-interactive peeks). */}
      {(!stacked || depth === 0) && (
        <span className="flex shrink-0 items-center gap-0.5">
          {canInterrupt && onInterrupt && (
            <IconButton icon={PlayCircle} label="Interrupt now — stop the current turn and run this next" onClick={() => onInterrupt(q.id)} size={20} iconSize={13}
              className="opacity-0 transition-opacity hover:text-primary group-hover/q:opacity-100 focus-within:opacity-100" />
          )}
          <IconButton icon={Pencil} label="Edit queued message" onClick={() => onEdit(q)} size={20} iconSize={12}
            className="opacity-0 transition-opacity hover:text-primary group-hover/q:opacity-100 focus-within:opacity-100" />
          <IconButton icon={X} label="Cancel queued message" onClick={() => onCancel(q.id)} size={20} iconSize={13}
            tone="danger" />
        </span>
      )}
    </motion.div>
  )

  return (
    <div className="mb-2 flex flex-col gap-1.5">
      {header}
      {stacked ? (
        // Collapsed deck: the top card in flow, up to `maxPeek` cards absolutely
        // stacked behind it. A wrapper reserves height for the peek offset.
        <div className="relative" style={{ paddingTop: Math.min(items.length - 1, maxPeek) * peekY }}>
          {items.slice(0, maxPeek + 1).map((q, i) => card(q, i, i)).reverse()}
        </div>
      ) : (
        <AnimatePresence initial={false}>
          <div className="flex flex-col gap-1.5">{items.map((q, i) => card(q, i, 0))}</div>
        </AnimatePresence>
      )}
    </div>
  )
}
