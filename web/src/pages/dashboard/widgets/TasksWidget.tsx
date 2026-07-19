import { useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { CheckCircle2, Circle, ListTodo, Plus } from 'lucide-react'
import { api } from '../../../lib/api'
import { useDashboardLive } from '../DashboardLive'
import { EmptyState, WidgetRow, RowAction } from './kit'
import type { RouteProps } from '../../../app/useQueryState'

const PRIORITY_TONE: Record<string, string> = {
  critical: 'var(--color-danger)', high: 'var(--color-warn)',
  medium: 'var(--color-info)', low: 'var(--color-on-surface-low)',
}

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
      <EmptyState
        icon={ListTodo}
        action={
          <button type="button" onClick={() => navigate('tasks/new')} className="inline-flex items-center gap-xs rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface" data-type="label-m">
            <Plus size={13} /> New task
          </button>
        }
      >No tasks ready to work.</EmptyState>
    )
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      <AnimatePresence initial={false}>
        {visible.slice(0, 6).map((t) => (
          <WidgetRow
            key={t.id}
            onClick={() => navigate('tasks')}
            actions={
              busy.has(t.id)
                ? <span data-type="label-m" className="px-s text-on-surface-low">…</span>
                : <RowAction tone="ok" onClick={() => complete(t.id)} title="Mark complete"><CheckCircle2 size={15} /></RowAction>
            }
          >
            <div className="flex items-center gap-s">
              <Circle size={13} className="shrink-0" style={{ color: PRIORITY_TONE[t.priority ?? 'low'] ?? 'var(--color-on-surface-low)' }} />
              <span data-type="body-m" className="truncate text-on-surface">{t.title}</span>
            </div>
          </WidgetRow>
        ))}
      </AnimatePresence>
      <button type="button" onClick={() => navigate('tasks/new')} className="mt-xs inline-flex items-center gap-xs self-start rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface" data-type="label-m">
        <Plus size={13} /> New task
      </button>
    </div>
  )
}
