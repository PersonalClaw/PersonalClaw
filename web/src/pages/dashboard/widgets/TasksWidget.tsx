import { useState } from 'react'
import { MoreRow } from '../../../ui/MoreRow'
import { AnimatePresence } from 'framer-motion'
import { CheckCircle2, Circle, ListTodo, Plus } from 'lucide-react'
import { api } from '../../../lib/api'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState, SlotAction, WidgetRow, RowAction } from './kit'
import { signalPriority } from '../../tasks/taskMeta'
import type { RouteProps } from '../../../app/useQueryState'

/** Tasks — ready-to-work tasks with inline complete. A one-tap check marks the
 *  task done (updateTask status → done) and it leaves the list; the live feed
 *  reconciles. "+ New task" and the list header jump to the Tasks page. */
export function TasksWidget({ navigate }: RouteProps) {
  const { tasks, refreshAll } = useDashboardLive()
  const [done, setDone] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<Set<string>>(new Set())

  const complete = async (id: string) => {
    setBusy((s) => new Set(s).add(id))
    try { await api.updateTask(id, { status: 'done' }); setDone((s) => new Set(s).add(id)) }
    catch { /* leave in place */ }
    finally { setBusy((s) => { const n = new Set(s); n.delete(id); return n }) }
    refreshAll()
  }

  const visible = tasks.filter((t) => !done.has(t.id))

  if (visible.length === 0) {
    return (
      <SlotEmptyState
        icon={ListTodo}
        action={<SlotAction icon={Plus} onClick={() => navigate('tasks/new')}>New task</SlotAction>}
      >No tasks ready to work.</SlotEmptyState>
    )
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      <AnimatePresence initial={false}>
        {visible.slice(0, 6).map((t) => {
          // `null` for the default and for unset — see the note at the dot below.
          const pm = signalPriority(t.priority)
          return (
          <WidgetRow
            key={t.id}
            onClick={() => navigate('tasks')}
            label={t.title}
            actions={
              busy.has(t.id)
                ? <span data-type="label-m" className="px-s text-on-surface-low">…</span>
                : (
                  <RowAction tone="ok" onClick={() => complete(t.id)} title="Mark complete"
                    ariaLabel={`Mark complete: ${t.title}`}><CheckCircle2 size={15} /></RowAction>
                )
            }
          >
            <div className="flex items-center gap-s">
              {/* 🔑 THE PRIORITY DOT COMES FROM `signalPriority`, THE CANONICAL HELPER — this widget used
                  to keep a rival `PRIORITY_TONE` map of its own, and both defects that map caused are
                  the reason `taskMeta` owns this:

                   · it painted `--color-info` on every `medium` task, and `medium` is the BACKEND
                     DEFAULT (`models.py`: `priority: TaskPriority = MEDIUM`), so it is indistinguishable
                     from never-set. `signalPriority`'s own docstring measured 28 of 30 tasks medium on
                     the validation home — a semantic colour on 93% of rows, saying nothing. Semantic
                     colours never decorate.
                   · it listed 4 of the registry's 5 rungs, so `trivial` fell through to the `??`.

                  🪤 THE SLOT IS RESERVED EVEN WHEN THERE IS NO DOT. Rendering nothing for a no-signal
                  task would left-shift that row's title by 13px + the gap, so a six-row list would sit
                  ragged. The wrapper keeps the width; only the glyph is conditional.

                  🪤 AND THE COLOUR IS NEVER THE ONLY CARRIER (WCAG 1.4.1). The four canonical sites
                  render `pm.label` as visible tone-coloured text; a dense preview row cannot spare the
                  width (its title already truncates to 266px of 434 at 390px), so the dot keeps the
                  label as its accessible name instead of dropping it. */}
              <span className="inline-flex size-[13px] shrink-0 items-center justify-center">
                {pm && <Circle size={13} aria-label={`${pm.label} priority`} style={{ color: pm.tone }} />}
              </span>
              {/* 266px of 434 at 390px — 1.6x. Same subject, same fix as the list row and the DAG node. */}
              <span data-type="body-m" className="truncate text-on-surface" title={t.title}>{t.title}</span>
            </div>
          </WidgetRow>
          )
        })}
      {/* 🪤 A DASHBOARD WIDGET IS A PREVIEW, AND NOTHING SAID SO. Six of twenty open tasks rendered
          with no count anywhere — its `Section` frame carries a bare label, and unlike the schedule
          widget below it there is no disclosure for the rest. So a user reads six as all of them. */}
      <MoreRow total={visible.length} shown={6} />
      </AnimatePresence>
      <button type="button" onClick={() => navigate('tasks/new')} className="mt-xs inline-flex items-center gap-xs self-start rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface" data-type="label-m">
        <Plus size={13} /> New task
      </button>
    </div>
  )
}
