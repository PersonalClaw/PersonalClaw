import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Play, Loader2, Wrench, Activity, ChevronRight, Target, Rocket, Hand } from 'lucide-react'
import { Button } from '../../../../ui/Button'
import { Eyebrow } from '../../../../ui/Eyebrow'
import { Centered } from '../../../../ui/Centered'
import { type CodeProject, type CodeStage, type TaskItem } from '../../../../lib/api'
import { fadeEnter, physics } from '../../../../design/motion'
import { Expandable } from '../../../../ui/motion'
import { BUSY_REASON } from '../../../../ui/unavailable'
import { execState } from './taskState'
import { TaskRow } from './TaskRow'
import { TERMINAL_STATUSES, stageKey, type ActivityItem } from '../cockpitShared'
import { useStageTasks } from './useStageTasks'

// ── The cockpit's task list ────────────────────────────────────────────────────────────────────
//
// The Tasks rail's list: each stage of the plan with its tasks, the run's counts and its drive
// mode, or the worker's live activity for a run with no task breakdown. `useStageTasks` keeps what
// it knows and does.

/** The Tasks rail's list: each stage's tasks and what can be done with them. Exported for its tests. */
export function StageTasks({ project, onTasksChanged, loading, tasksByList, onSelect, activeTaskIds, mainActivity }: {
  project: CodeProject; onTasksChanged: () => void; loading: boolean
  tasksByList: Record<string, TaskItem[]>; onSelect: (taskId: string) => void; activeTaskIds: Set<string>
  mainActivity: ActivityItem[]
}) {
  const {
    links, stages, busy, noLists, isPreLaunch, queuedSet, doneIds, knownIds, ended, queueable, runningCount,
    undoneCount, queue, autopilot, toggleAutopilot,
  } = useStageTasks({ project, onTasksChanged, tasksByList })
  if (loading) return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  if (noLists && !stages.length) {
    // No task lists AND no stage plan. The message must match the project's actual
    // state: pre-launch is genuinely "not started yet", but a RUNNING/active project
    // with no breakdown is working straight off its brief (minimal intake / no
    // decomposition) — saying "once launched" there contradicts the live worker.
    // For a RUNNING no-breakdown project, render the worker's live activity right here
    // — there's no task card to host it, so the old "watch its activity on the right"
    // copy pointed at an empty panel (the activity was collected but never shown).
    if (!isPreLaunch && !TERMINAL_STATUSES.has(project.status) && mainActivity.length > 0) {
      return (
        <div className="flex flex-col gap-2 p-3">
          <p data-type="caption" className="text-on-surface-low">No task breakdown for this run — the worker is operating directly from the brief.</p>
          <div className="rounded-lg border border-primary/30 bg-primary/5 p-2.5">
            <div data-type="caption" className="mb-1.5 inline-flex items-center gap-1.5 text-primary"><Loader2 size={11} className="animate-spin" /> working now</div>
            <div className="flex flex-col gap-1">
              {mainActivity.map((it, i) => (
                it.kind === 'say'
                  ? <p key={i} data-type="caption" className="whitespace-pre-wrap text-on-surface-var">{it.label}</p>
                  : <div key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low">
                      {it.kind === 'tool' ? <Wrench size={11} className="mt-0.5 shrink-0" /> : <Activity size={11} className="mt-0.5 shrink-0" />}
                      <span className="min-w-0"><span className="text-on-surface-var">{it.label}</span>{it.detail && <span className="text-on-surface-low/70"> · {it.detail.slice(0, 60)}</span>}</span>
                    </div>
              ))}
            </div>
          </div>
        </div>
      )
    }
    const msg = isPreLaunch
      ? 'Tasks appear here once the project is launched.'
      : TERMINAL_STATUSES.has(project.status)
        ? 'This project ran without a task breakdown — see the activity and workspace files for what it did.'
        : 'No task breakdown for this run — the worker is operating directly from the brief. Watch its activity below as it works.'
    return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">{msg}</p>
  }
  return (
    <div className="flex flex-col gap-1 p-2">
      {!noLists && (
        <div className="flex items-center justify-between gap-2 px-2 pb-1">
          <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low">
            {runningCount > 0 && (
              <span className="inline-flex items-center gap-1 text-primary" title="Tasks running in parallel worktrees">
                <Loader2 size={10} className="animate-spin" />{runningCount} running
              </span>
            )}
            {runningCount > 0 && (queuedSet.size > 0 || queueable.length > 0) && <span className="opacity-40">·</span>}
            {!ended && queuedSet.size > 0 && <span>{queuedSet.size} queued</span>}
            {queueable.length > 0 && <span className="opacity-70">{queueable.length} can queue</span>}
            {undoneCount > 0 && <span>{undoneCount} not done</span>}
          </span>
          {!ended && <div className="flex items-center gap-1.5">
            {/* Autopilot drives the phased plan itself; one-by-one hands queueing to
                the user. Toggle is live (any non-terminal state). */}
            <Button variant={autopilot ? 'tonal' : 'ghost'} size="xs" disabled={busy} disabledReason={BUSY_REASON}
              onClick={toggleAutopilot} ariaPressed={autopilot} ariaLabel="Autopilot"
              title={autopilot ? 'Autopilot on — the system queues + drives the phased tasks. Click for one-by-one.' : 'One-by-one — you queue tasks yourself. Click to let the system drive.'}>
              {autopilot ? <Rocket size={11} /> : <Hand size={11} />} {autopilot ? 'Autopilot' : 'One-by-one'}
            </Button>
            {/* Manual Queue all only matters in one-by-one mode (autopilot auto-queues). */}
            {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
            {!autopilot && queueable.length > 0 && (
              <Button variant="tonal" size="xs" disabled={busy} disabledReason={BUSY_REASON} onClick={() => queue(queueable.map((t) => t.id))}
                className="gap-1 px-2 text-[0.75rem]">
                <Play size={11} /> Queue all
              </Button>
            )}
          </div>}
        </div>
      )}
      {noLists && isPreLaunch && (
        <p data-type="caption" className="px-2 pb-1 text-on-surface-low">Planned — provisioned when you launch.</p>
      )}
      {stages.map((s, si) => {
        const key = stageKey(s)
        const lid = links[key]
        const tasks: TaskItem[] = (lid && tasksByList[lid])
          || (noLists ? (s.tasks ?? []).map((t, i) => ({ id: `plan-${key}-${i}`, title: t.title, status: 'open' } as TaskItem)) : [])
        const st = project.stage_status?.[key] ?? 'pending'
        return (
          // Always suffix the index: two stages can share an effective key (stage ||
          // title). The backend dedupes TaskLists by key at LAUNCH, but the plan array
          // can still hold both rows for a project created via the chat tool / API
          // (which bypass Plan Review's dup-guard) — `key || si` then emitted the SAME
          // React key for both, a duplicate-key warning + reconciliation glitch. The
          // index keeps each row's React identity unique regardless.
          <StageGroup key={`${key}:${si}`} stage={s} status={st} tasks={tasks} ended={ended}
            preview={noLists} doneIds={doneIds} queuedSet={queuedSet} knownIds={knownIds}
            onSelect={onSelect} activeTaskIds={activeTaskIds}
            attention={['blocked', 'needs_input', 'stagnant', 'failed', 'stopped'].includes(project.status)} />
        )
      })}
    </div>
  )
}

/** One stage's task group: an (optional) expandable objective + exit-criteria
 *  header, then its task rows. Rows NAVIGATE to the task detail view on click. */
function StageGroup({ stage: s, status: st, tasks, preview, doneIds, queuedSet, knownIds, onSelect, activeTaskIds, attention = false, ended }: {
  stage: CodeStage; status: string; tasks: TaskItem[]; preview: boolean
  doneIds: Set<string>; queuedSet: Set<string>; knownIds: Set<string>; onSelect: (taskId: string) => void; activeTaskIds: Set<string>; attention?: boolean
  /** The project finished or was stopped: its unfinished tasks read 'not done' (`execState`). */
  ended: boolean
}) {
  // The active stage's header reads primary (in-flight) — but when the PROJECT is in an
  // attention state (blocked/needs_input/stagnant/failed/stopped) it's parked AT this
  // stage, not progressing, so warn-tone it to agree with the StageTrail + status pill.
  const color = st === 'active' ? (attention ? 'var(--color-warn)' : 'var(--color-primary)')
    : st === 'done' ? 'var(--color-ok)' : 'var(--color-on-surface-low)'
  // A stage is "open" for execution when it's the active one (or already done — its
  // tasks just show done). A pending (not-yet-reached) stage holds its tasks behind
  // the phase barrier → their rows read 'waiting' rather than 'queued'/'ready'.
  const stageOpen = st === 'active' || st === 'done'
  // The stage's objective + exit criteria — shaped in Plan Review — were dropped at
  // execution time (header showed only the title). Surface them behind an expandable
  // header so the user can see what the stage is FOR and how it clears, without
  // cluttering the rail by default. Auto-open the active stage (the one in play).
  const objective = (s.objective || '').trim()
  const criteria = (s.exit_criteria ?? []).filter((c) => (c || '').trim())
  const hasDetail = !!objective || criteria.length > 0
  const [open, setOpen] = useState(st === 'active')
  // Auto-open when this stage BECOMES active during a live run (pending→active as the
  // worker advances) — not just on mount. Without this the newly-in-play stage stayed
  // collapsed mid-run, hiding the tasks now executing. Fire only on the transition INTO
  // active (track the prev status), so a user who manually collapsed a different stage
  // isn't fought, and a re-render at steady state doesn't re-open what they closed.
  const prevSt = useRef(st)
  const rootRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (st === 'active' && prevSt.current !== 'active') {
      setOpen(true)
      // Also bring the freshly-active stage into view: on a long multi-stage plan the
      // newly-in-play stage can be below the fold, so auto-opening it alone wasn't
      // enough — the user wouldn't see the tasks now executing without scrolling. Only
      // on the real transition (not mount), so opening the cockpit doesn't yank the
      // rail. rAF so it runs after the expand lays out.
      requestAnimationFrame(() => rootRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
    }
    prevSt.current = st
  }, [st])
  // Stage status (active/done/pending) is conveyed only by the header COLOR — invisible
  // to a screen reader, which would hear just the title + count. Fold it into the
  // accessible name so the stage's state is spoken (e.g. "Implementation — active, 3 tasks").
  const stLabel = st === 'active' ? 'active' : st === 'done' ? 'done' : 'pending'
  const a11yLabel = `${s.title || s.stage} — ${stLabel}, ${tasks.length} task${tasks.length === 1 ? '' : 's'}`
  return (
    <div ref={rootRef} className="rounded-lg">
      {hasDetail ? (
        // Morph the objective/exit-criteria open in place (height+opacity), rather than
        // popping the block in/out — the SAME stage row grows to reveal its detail
        // ("morph, don't mount"). Reduced-motion degrades to an instant swap.
        <Expandable open={open} header={
          // Raw: a full-row disclosure header, which no primitive draws.
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label={a11yLabel}
            data-type="caption" className="flex w-full items-center gap-1.5 px-2 py-1.5 hover:bg-surface-high/40" style={{ color }}>
            <motion.span animate={{ rotate: open ? 90 : 0 }} transition={physics.snappy} className="shrink-0">
              <ChevronRight size={12} />
            </motion.span>
            <span className="truncate">{s.title || s.stage}</span>
            <span className="opacity-60">({tasks.length})</span>
          </button>
        }>
          <div className="mb-1 ml-4 flex flex-col gap-1.5 border-l border-outline-variant/40 pl-2.5">
            {objective && <p data-type="caption" className="text-on-surface-var normal-case">{objective}</p>}
            {criteria.length > 0 && (
              <div className="flex flex-col gap-0.5">
                <Eyebrow as="span">Done when</Eyebrow>
                {criteria.map((c, i) => (
                  <div key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low normal-case">
                    <Target size={9} className="mt-[3px] shrink-0 opacity-60" /><span>{c}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Expandable>
      ) : (
        <div role="group" aria-label={a11yLabel}
          data-type="caption" className="flex w-full items-center gap-1.5 px-2 py-1.5" style={{ color }}>
          <span className="truncate">{s.title || s.stage}</span>
          <span className="opacity-60">({tasks.length})</span>
        </div>
      )}
      {tasks.length === 0
        ? <p data-type="caption" className="px-3 pb-2 text-on-surface-low/70">no tasks yet</p>
        // The rows fade in together on first paint, with no cascade and no rise: the cockpit's
        // task list is read over and over, so the order rows arrive in says nothing. Keyed by task
        // id, so the poll/SSE refresh (a re-render, not a remount) never re-fires it — only a
        // genuinely new task fades in. Framer's `initial` runs once per mounted key.
        : <motion.div initial="initial" animate="animate">
            {tasks.map((t) => (
              <motion.div key={t.id} variants={fadeEnter}>
                <TaskRow task={t} preview={preview}
                  state={execState(t, doneIds, queuedSet, stageOpen, knownIds, ended)} active={activeTaskIds.has(t.id)} onSelect={() => onSelect(t.id)} />
              </motion.div>
            ))}
          </motion.div>}
    </div>
  )
}
