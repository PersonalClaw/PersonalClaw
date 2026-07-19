import { useEffect, useMemo } from 'react'
import { Plus, Zap, Clock, CheckCircle2, XCircle, Circle, Rocket, Pencil } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { ListControls } from '../../ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/useQueryState'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type ScheduleJob, type HookItem, type ActionProvider } from '../../lib/api'
import { ScheduleDetail } from '../schedule/ScheduleDetail'
import { LifecycleDetail } from './LifecycleDetail'
import { scheduleToTrigger, hookToTrigger, relPast, type Trigger, type TriggerKind } from './triggerMeta'

const FILTERS: Array<{ key: string; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'schedule', label: 'Schedules' },
  { key: 'lifecycle', label: 'Lifecycle' },
]

function statusDot(s?: string | null) {
  if (s === 'ok' || s === 'success') return { tone: 'var(--color-ok)', icon: CheckCircle2 }
  if (s === 'error' || s === 'timeout' || s === 'blocked') return { tone: 'var(--color-danger)', icon: XCircle }
  // "launched": a fire-and-forget run only started bg work — neutral info tone,
  // NOT ok-green (honest "started ≠ succeeded", T7).
  if (s === 'launched') return { tone: 'var(--color-info)', icon: Rocket }
  return { tone: 'var(--color-on-surface-low)', icon: Circle }
}

/** Unified Triggers list — schedule + lifecycle triggers in one view, with a
 *  type filter. Detail opens the right inspector per kind (ScheduleDetail reused
 *  verbatim; LifecycleDetail for lifecycle triggers). Backed by the unified
 *  /api/triggers facade (api.schedules()/api.hooks() project it per kind). */
export function TriggersListPage({ onCreate, query, setQuery }: { onCreate: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const [filter, setFilter] = useQueryParam(query, setQuery, 'filter', 'all', { replace: true })
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null
  const [editing, setEditing] = useEditFlag(query, setQuery)

  // Schedules carry live next-run/running state → persist:false (instant in-app
  // revisit, but never stale across a hard reload). Hooks + action providers are
  // lifecycle config that rarely changes → persist:true so they survive a reload.
  const { data: schedules, refresh: refreshSchedules } = useCachedData('triggers:schedules', () => api.schedules().then((d) => d.jobs).catch(() => [] as ScheduleJob[]), { persist: false })
  const { data: hooks, refresh: refreshHooks } = useCachedData('triggers:hooks', () => api.hooks().catch(() => [] as HookItem[]), { persist: true })
  const { data: providers = [] } = useCachedData('triggers:action-providers', () => api.actionProviders().catch(() => [] as ActionProvider[]), { persist: true })

  const loadSchedules = () => { invalidateCache('triggers:schedules'); refreshSchedules() }
  const loadHooks = () => { invalidateCache('triggers:hooks'); refreshHooks() }
  useEffect(() => {
    const t = window.setInterval(refreshSchedules, 10000)  // keep schedule next-run/running fresh
    return () => clearInterval(t)
  }, [refreshSchedules])

  const triggers = useMemo<Trigger[] | null>(() => {
    if (schedules === undefined || hooks === undefined) return null
    const all = [...schedules.map(scheduleToTrigger), ...hooks.map(hookToTrigger)]
    const n = q.trim().toLowerCase()
    return all
      .filter((t) => filter === 'all' || t.kind === filter)
      .filter((t) => !n || `${t.name} ${t.whenLabel} ${t.actionLabel}`.toLowerCase().includes(n))
  }, [schedules, hooks, filter, q])

  const open = useMemo(() => triggers?.find((t) => t.id === openId) ?? null, [triggers, openId])

  const counts = useMemo(() => {
    const s = schedules?.length ?? 0, h = hooks?.length ?? 0
    return { all: s + h, schedule: s, lifecycle: h }
  }, [schedules, hooks])

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<span data-type="title-l" className="text-on-surface">Triggers</span>}
          right={<HeaderActions><HeaderControl icon={Plus} label="New trigger" variant="primary" priority="primary" onClick={onCreate} /></HeaderActions>}
        />
      }
      controls={(triggers === null || counts.all > 0)
        ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search triggers', label: 'Search triggers' }}>
            <FilterMenu sections={[{
              title: 'Type',
              value: filter,
              defaultKey: 'all',
              onChange: setFilter,
              options: FILTERS.map((f) => ({ key: f.key, label: f.label, count: counts[f.key as TriggerKind] })),
            } satisfies FilterSectionDef]} />
          </ListControls>
        : undefined}
      panel={
        open && (
          <SidePanel key={open.id} fillHeight storeKey="trigger-panel-w" icon={<open.whenIcon size={18} style={{ color: open.whenTone }} />} title={open.name} onClose={() => setQuery({ open: null, edit: null })}>
            {open.kind === 'schedule' && open.schedule
              ? <ScheduleDetail job={open.schedule} editing={editing} onEditingChange={setEditing} onSaved={loadSchedules} onChanged={loadSchedules} onDeleted={() => { setOpenId(""); loadSchedules() }} />
              : open.hook
              ? <LifecycleDetail hook={open.hook} providers={providers} editing={editing} onEditingChange={setEditing} onSaved={loadHooks} onDeleted={() => { setOpenId(""); loadHooks() }} />
              : null}
          </SidePanel>
        )
      }
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {triggers === null ? <ListSkeleton rows={6} /> : triggers.length === 0 ? (
              <EmptyState icon={Zap} title={q || filter !== 'all' ? 'No matching triggers' : 'No triggers'} hint={q || filter !== 'all' ? 'Try a different filter.' : 'A trigger runs an action when something happens — a schedule tick or an agent-loop lifecycle event. Create one to automate work.'} action={!q && filter === 'all' ? { label: 'New trigger', onClick: onCreate, icon: Plus } : undefined} />
            ) : (
              <div className="flex flex-col gap-s">
                {triggers.map((t, i) => {
                  const sd = statusDot(t.lastStatus)
                  // Right-click / long-press → the scoped actions this list performs on
                  // a row (open the inspector, or open it straight into edit mode). Both
                  // route through the same `setQuery` the row's click uses — destructive
                  // + enable/disable live inside the opened detail panel, not here.
                  const menuItems: ContextMenuItem[] = [
                    { icon: <Zap size={15} />, label: 'Open', onSelect: () => setQuery({ open: t.id, edit: null }) },
                    { icon: <Pencil size={15} />, label: 'Edit', onSelect: () => setQuery({ open: t.id, edit: '1' }) },
                  ]
                  return (
                    <ContextMenu key={t.id} items={menuItems}>
                    <ListRow index={i} accent={t.enabled ? t.whenTone : undefined} onClick={() => setQuery({ open: t.id, edit: null })}>
                      <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${t.whenTone} 16%, transparent)` }}><t.whenIcon size={19} style={{ color: t.whenTone }} /></span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-s">
                          <span className={`truncate text-[0.9375rem] ${t.enabled ? 'text-on-surface' : 'text-on-surface-var'}`} style={{ fontVariationSettings: '"wght" 500' }}>{t.name}</span>
                          {!t.enabled && <span className="shrink-0 text-on-surface-low text-[0.7rem]">· disabled</span>}
                          {t.kind === 'schedule' && t.schedule?.is_running && <span className="shrink-0 inline-flex items-center gap-1 text-primary text-[0.7rem]"><span className="relative flex size-1.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-pill bg-primary opacity-60" /><span className="relative inline-flex size-1.5 rounded-pill bg-primary" /></span>running</span>}
                          {t.kind === 'lifecycle' && t.usedBy.length === 0 && <span className="shrink-0 text-on-surface-low text-[0.7rem]">· dormant</span>}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
                          <span className="inline-flex items-center gap-1" style={{ color: t.whenTone }}><t.whenIcon size={11} /> {t.whenLabel}</span>
                          <span className="inline-flex items-center gap-1"><t.actionIcon size={11} /> {t.actionLabel}</span>
                          {t.kind === 'schedule' && t.enabled && t.schedule?.next_run_ts && <span className="inline-flex items-center gap-1"><Clock size={11} /> {relFuture(t.schedule.next_run_ts)}</span>}
                          {t.kind === 'lifecycle' && t.runCount != null && <span>ran {t.runCount}×</span>}
                        </div>
                      </div>
                      <div className="hidden sm:flex shrink-0 items-center gap-1.5 text-on-surface-low text-[0.75rem]">
                        <sd.icon size={13} style={{ color: sd.tone }} />
                        <span>{t.lastRunTs ? relPast(t.lastRunTs) : 'never'}</span>
                      </div>
                    </ListRow>
                    </ContextMenu>
                  )
                })}
              </div>
            )}
      </div>
    </WorkbenchLayout>
  )
}

function relFuture(ts?: number | null): string {
  if (!ts) return ''
  const s = ts - Date.now() / 1000
  if (s < 0) return 'overdue'
  if (s < 60) return 'in <1m'
  if (s < 3600) return `in ${Math.floor(s / 60)}m`
  if (s < 86400) return `in ${Math.floor(s / 3600)}h`
  return `in ${Math.floor(s / 86400)}d`
}
