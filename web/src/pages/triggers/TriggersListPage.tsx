import { useEffect, useMemo } from 'react'
import { fvs } from '../../design/fontWeight'
import { Plus, Zap, Clock, Pencil, CalendarDays } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { ListControls } from '../../ui/ListControls'
import { Segmented } from '../../ui/Segmented'
import { WeekGridView } from './WeekGridView'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/useQueryState'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type ScheduleJob, type HookItem, type ActionProvider, type Trigger as WireTrigger } from '../../lib/api'
import { ScheduleDetail } from '../schedule/ScheduleDetail'
import { LifecycleDetail } from './LifecycleDetail'
import { StoreTriggerDetail } from './StoreTriggerDetail'
import { scheduleToTrigger, hookToTrigger, storeToTrigger, eventToTrigger, eventPatternMeta, relPast, type Trigger } from './triggerMeta'
import { RungChip } from '../../ui/RungChip'
import { providerRungIndex, useAutonomyLadder } from '../../lib/rungs'
import { statusMeta, triggerHealthMeta, relFuture } from '../schedule/scheduleMeta'
import { PageTitle } from '../../ui/PageTitle'

// One chip per kind `GET /api/triggers` can return: schedule · lifecycle · event · store.
// `Data events` was missing, so an event trigger — creatable from this page's own form — had no
// chip AND was absent from every count.
// The plural wording is deliberate and differs from `TRIGGER_KINDS`' singular labels: these name
// a CATEGORY OF ROWS you are filtering to, not the kind of the one thing you are creating.
const FILTERS: Array<{ key: string; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'schedule', label: 'Schedules' },
  { key: 'lifecycle', label: 'Lifecycle' },
  { key: 'event', label: 'Data events' },
  { key: 'store', label: 'Automations' },
]

// 🔴 The local `statusDot` was DELETED (S164). It handled four values and defaulted the rest to a
// neutral grey circle — and for a store trigger this page feeds it `t.health`, whose vocabulary is
// `ok | degraded | parked | failing`. Measured: `degraded`, `parked` and `failing` ALL rendered as
// the same grey circle, so a FAILING automation was pixel-identical to a parked (self-healing) one
// on the single page a user manages automations from. Second instance of S163's defect shape in a
// second local copy, which is why the vocabulary now has one mapper: `statusMeta` for run outcomes,
// `triggerHealthMeta` for health + lifecycle state.

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
  // List | Week (AUTO-A3). URL-addressable, unlike the run-detail toggle: a week grid is something
  // you send someone ("look at Thursday"), and the section is already query-state driven.
  const [view, setView] = useQueryParam(query, setQuery, 'view', 'list', { replace: true })

  // Schedules carry live next-run/running state → persist:false (instant in-app
  // revisit, but never stale across a hard reload). Hooks + action providers are
  // lifecycle config that rarely changes → persist:true so they survive a reload.
  const { data: schedules, refresh: refreshSchedules } = useCachedData('triggers:schedules', () => api.schedules().then((d) => d.jobs).catch(() => [] as ScheduleJob[]), { persist: false })
  const { data: hooks, refresh: refreshHooks } = useCachedData('triggers:hooks', () => api.hooks().catch(() => [] as HookItem[]), { persist: true })
  // Store triggers (file/web_watch/idle/…) carry live enabled/health state → persist:false, like
  // schedules: instant in-app revisit but never stale across a hard reload.
  const { data: stores, refresh: refreshStores } = useCachedData('triggers:store', () => api.storeTriggers().catch(() => [] as WireTrigger[]), { persist: false })
  // Data-event triggers (EIAT). `GET /api/triggers` serves FOUR kinds — schedule, lifecycle,
  // event, store — and this page fetched only three, so an event trigger created through the
  // create form (`trigger_type: 'event'`) existed, fired, and was never listed anywhere. It
  // carries live enabled/fire-count state → persist:false, like schedules and stores.
  const { data: events } = useCachedData('triggers:events', () => api.eventTriggers().catch(() => [] as WireTrigger[]), { persist: false })
  const { data: providers = [] } = useCachedData('triggers:action-providers', () => api.actionProviders().catch(() => [] as ActionProvider[]), { persist: true })
  // How much each row's action may do UNATTENDED (AUTONOMY-GUARDRAILS §6.1). The ladder is
  // keyed on the action-provider name — the same identity the backend dispatch seams hold —
  // so this page annotates a row without knowing anything about action types. A failed read
  // leaves `ladder` null and the rows simply carry no chip: the Settings ladder panel is the
  // surface that OWNS this data and reports the failure, and substituting a rung here would
  // be a fabricated claim about what an automation may do on its own.
  const { ladder } = useAutonomyLadder()
  const rungByProvider = useMemo(() => providerRungIndex(ladder), [ladder])

  const loadSchedules = () => { invalidateCache('triggers:schedules'); refreshSchedules() }
  const loadHooks = () => { invalidateCache('triggers:hooks'); refreshHooks() }
  const loadStores = () => { invalidateCache('triggers:store'); refreshStores() }
  useEffect(() => {
    const t = window.setInterval(refreshSchedules, 10000)  // keep schedule next-run/running fresh
    return () => clearInterval(t)
  }, [refreshSchedules])

  const triggers = useMemo<Trigger[] | null>(() => {
    if (schedules === undefined || hooks === undefined || stores === undefined || events === undefined) return null
    // Every kind needs its converter: the wire carries no `whenLabel`/`whenIcon`/`actionLabel`,
    // so a raw row would render `undefined` for the icon the list draws per row.
    const all = [...schedules.map(scheduleToTrigger), ...hooks.map(hookToTrigger), ...stores.map(storeToTrigger), ...events.map(eventToTrigger)]
    const n = q.trim().toLowerCase()
    return all
      .filter((t) => filter === 'all' || t.kind === filter)
      .filter((t) => !n || `${t.name} ${t.whenLabel} ${t.actionLabel}`.toLowerCase().includes(n))
  }, [schedules, hooks, stores, events, filter, q])

  const open = useMemo(() => triggers?.find((t) => t.id === openId) ?? null, [triggers, openId])

  const counts = useMemo(() => {
    const s = schedules?.length ?? 0, h = hooks?.length ?? 0, st = stores?.length ?? 0, e = events?.length ?? 0
    return { all: s + h + st + e, schedule: s, lifecycle: h, store: st, event: e }
  }, [schedules, hooks, stores, events])

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Triggers</PageTitle>}
          right={<HeaderActions><HeaderControl icon={Plus} label="New trigger" variant="primary" priority="primary" onClick={onCreate} /></HeaderActions>}
        />
      }
      controls={(triggers === null || counts.all > 0)
        ? <ListControls
            results={{ count: (triggers ?? []).length, noun: 'triggers', active: !!q.trim() || filter !== 'all' }}
            // Search and the type filter belong to the LIST. The week grid plots every enabled clock
            // trigger by construction, so a search box over it would be a control that changes
            // nothing — worse than an absent one.
            search={view === 'list' ? { value: q, onChange: setQ, placeholder: 'Search triggers', label: 'Search triggers' } : undefined}
          >
            <Segmented
              ariaLabel="Triggers view"
              size="sm"
              value={view}
              onChange={setView}
              options={[{ key: 'list', label: 'List', icon: Zap }, { key: 'week', label: 'Week', icon: CalendarDays }]}
            />
            {view === 'list' && <FilterMenu sections={[{
              title: 'Type',
              value: filter,
              defaultKey: 'all',
              onChange: setFilter,
              options: FILTERS.map((f) => ({ key: f.key, label: f.label, count: counts[f.key as keyof typeof counts] })),
            } satisfies FilterSectionDef]} />}
          </ListControls>
        : undefined}
      panel={
        open && (
          <SidePanel key={open.id} fillHeight storeKey="trigger-panel-w" icon={<open.whenIcon size={18} style={{ color: open.whenTone }} />} title={open.name} onClose={() => setQuery({ open: null, edit: null })}>
            {open.kind === 'schedule' && open.schedule
              ? <ScheduleDetail job={open.schedule} editing={editing} onEditingChange={setEditing} onSaved={loadSchedules} onChanged={loadSchedules} onDeleted={() => { setOpenId(""); loadSchedules() }} />
              : open.kind === 'store' && open.store
              ? <StoreTriggerDetail trigger={open.store} onChanged={loadStores} onDeleted={() => { setOpenId(""); loadStores() }} />
              : open.kind === 'event' && open.event
              // Read-only for now: a data-event trigger has no editor yet, and an empty panel
              // (what a fell-through event row rendered) is worse than an honest summary. Edit +
              // delete land with its own inspector — tracked, not silently skipped.
              ? <EventTriggerSummary t={open} />
              : open.hook
              ? <LifecycleDetail hook={open.hook} providers={providers} editing={editing} onEditingChange={setEditing} onSaved={loadHooks} onDeleted={() => { setOpenId(""); loadHooks() }} />
              : null}
          </SidePanel>
        )
      }
    >
      {view === 'week' ? (
        // Click-through routes into the SAME side panel the list opens (`?open=<id>`), so a cell and
        // a row lead to one inspector rather than two surfaces that drift apart.
        <WeekGridView onOpenTrigger={(id) => setQuery({ open: id, edit: null, view: 'list' })} />
      ) : (
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {triggers === null ? <ListSkeleton rows={6} what="triggers" /> : triggers.length === 0 ? (
              <EmptyState icon={Zap} title={q || filter !== 'all' ? 'No matching triggers' : 'No triggers'} hint={q || filter !== 'all' ? 'Try a different filter.' : 'A trigger runs an action when something happens — a schedule tick or an agent-loop lifecycle event. Create one to automate work.'} action={!q && filter === 'all' ? { label: 'New trigger', onClick: onCreate, icon: Plus } : undefined} />
            ) : (
              <div className="flex flex-col gap-s">
                {triggers.map((t, i) => {
                  const sd = t.kind === 'store'
                    ? triggerHealthMeta(t.lastStatus, t.state)
                    : statusMeta(t.lastStatus)
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
                    <ListRow index={i} accent={t.enabled ? t.whenTone : undefined} onClick={() => setQuery({ open: t.id, edit: null })} label={t.name}>
                      <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${t.whenTone} 16%, transparent)` }}><t.whenIcon size={19} style={{ color: t.whenTone }} /></span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-s">
                          <span className={`truncate text-[0.9375rem] ${t.enabled ? 'text-on-surface' : 'text-on-surface-var'}`} style={fvs(500)}>{t.name}</span>
                          {!t.enabled && <span className="shrink-0 text-on-surface-low text-[0.75rem]">· disabled</span>}
                          {t.kind === 'schedule' && t.schedule?.is_running && <span className="shrink-0 inline-flex items-center gap-1 text-primary text-[0.75rem]"><span className="relative flex size-1.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-pill bg-primary opacity-60" /><span className="relative inline-flex size-1.5 rounded-pill bg-primary" /></span>running</span>}
                          {t.kind === 'lifecycle' && t.usedBy.length === 0 && <span className="shrink-0 text-on-surface-low text-[0.75rem]">· dormant</span>}
                          {t.kind === 'store' && t.broken && t.broken.length > 0 && <span className="shrink-0 text-danger text-[0.75rem]">· needs attention</span>}
                          {t.kind === 'store' && t.storeKind && <span className="shrink-0 text-on-surface-low text-[0.75rem]">· {t.storeKind}</span>}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
                          <span className="inline-flex items-center gap-1" style={{ color: t.whenTone }}><t.whenIcon size={11} /> {t.whenLabel}</span>
                          <span className="inline-flex items-center gap-1"><t.actionIcon size={11} /> {t.actionLabel}</span>
                          {t.kind === 'schedule' && t.enabled && t.schedule?.next_run_ts && <span className="inline-flex items-center gap-1"><Clock size={11} /> {relFuture(t.schedule.next_run_ts)}</span>}
                          {/* The rung chip: what this automation may do on its own, and why.
                              Placed beside the action label because that is the thing being
                              governed — the action, not the schedule that fires it. Absent for
                              an action no declaration claims (it keeps its pre-ladder
                              behaviour) and while the ladder is still loading. */}
                          {t.actionProvider && rungByProvider.get(t.actionProvider) && (
                            <RungChip type={rungByProvider.get(t.actionProvider)!} ladder={ladder} />
                          )}
                          {t.kind === 'lifecycle' && t.runCount != null && <span>ran {t.runCount}×</span>}
                          {/* An event trigger has no clock, so `fired N×` is its one live number. */}
                          {t.kind === 'event' && t.runCount != null && <span>fired {t.runCount}×</span>}
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
      )}
    </WorkbenchLayout>
  )
}

/** Read-only inspector for a data-event trigger. Mirrors `StoreTriggerDetail`'s section rhythm
 *  (uppercase caption over a value) so the two inspectors read as one family; it stays local
 *  rather than exporting that file's private `Section` for a single caller.
 *
 *  Shows only the matcher the row's pattern actually reads — `eventPatternMeta().matcher` names
 *  it, and the other glob fields are inert for that pattern, so rendering them would claim a
 *  constraint that is not applied. */
function EventTriggerSummary({ t }: { t: Trigger }) {
  const pm = eventPatternMeta(t.eventPattern)
  const rows: Array<[string, string]> = [
    ['Fires on', pm.label],
    ...(pm.matcher ? [[pm.matcherLabel, t.eventMatcher || 'anything'] as [string, string]] : []),
    ['Then', t.actionLabel],
    ['Fired', t.runCount != null ? `${t.runCount}×` : '—'],
  ]
  return (
    <div className="flex flex-col gap-l p-l">
      <p className="text-on-surface-var text-[0.8125rem]">{pm.desc}</p>
      {rows.map(([label, value]) => (
        <div key={label}>
          <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{label}</div>
          <div className="text-on-surface text-[0.875rem] break-words">{value}</div>
        </div>
      ))}
      <p className="text-on-surface-low text-[0.8125rem]">
        Editing a data-event trigger isn’t available here yet — recreate it to change its pattern.
      </p>
    </div>
  )
}

