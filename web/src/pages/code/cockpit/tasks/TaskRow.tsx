import { ChevronRight } from 'lucide-react'
import { type TaskItem } from '../../../../lib/api'
import { type ExecState, STATE_ICON, STATE_COLOR, STATE_LABEL } from './taskState'

// ── A task in the cockpit's list ───────────────────────────────────────────────────────────────
//
// One row of the Tasks rail: the task's state as an icon and a label, opening its detail on click.

/** A navigable task row — clicking opens the task detail view in the sidebar. A
 *  chevron affords the navigation; a needs-input task flags an attention dot. */
export function TaskRow({ task, state, preview, active, onSelect }: {
  task: TaskItem; state: ExecState; preview: boolean; active?: boolean; onSelect: () => void
}) {
  const Icon = STATE_ICON[state]
  // Both terminal states (done + cancelled) read struck-through + muted.
  const done = state === 'done' || state === 'cancelled'
  if (preview) {
    // pre-launch: no detail to navigate to yet, just show the planned row.
    return (
      <div data-type="body-s" className="flex items-start gap-1.5 px-2 py-1.5">
        <Icon size={14} className="mt-0.5 shrink-0" style={{ color: STATE_COLOR[state] }} />
        <span className="min-w-0 flex-1 text-on-surface-var">{task.title}</span>
      </div>
    )
  }
  return (
    <button type="button" onClick={onSelect} aria-label={`${task.title} — ${STATE_LABEL[state]}`}
      data-type="body-s" className="group flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left hover:bg-surface-high/60">
      <Icon size={14} className={`shrink-0 ${state === 'running' ? 'animate-spin' : ''}`} style={{ color: STATE_COLOR[state] }} />
      <span className={`min-w-0 flex-1 truncate ${done ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>{task.title}</span>
      {/* a live pulse when THIS task's worker is actively emitting events right now */}
      {active && <span className="reveal-caret size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} title="Active now" />}
      {state === 'cancelled' && <span data-type="caption" className="shrink-0 text-on-surface-low/70">cancelled</span>}
      {state === 'running' && <span data-type="caption" className="shrink-0 text-primary">running</span>}
      {state === 'queued' && <span data-type="caption" className="shrink-0 text-primary">queued</span>}
      {state === 'blocked' && <span data-type="caption" className="shrink-0 text-on-surface-low/70">blocked</span>}
      {state === 'waiting' && <span data-type="caption" className="shrink-0 text-on-surface-low/70" title="Waiting for its stage to start">waiting</span>}
      {state === 'undone' && <span data-type="caption" className="shrink-0 text-on-surface-low/70" title="The project ended before this task was done">not done</span>}
      {/* `ready` is the actionable state — a quiet chip (matches the others' pattern)
          so it reads as available-to-queue, not just an unlabeled inert row. */}
      {state === 'ready' && <span data-type="caption" className="shrink-0 text-on-surface-low/70" title="Ready to queue">ready</span>}
      <ChevronRight size={13} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
    </button>
  )
}
