import { useState } from 'react'
import { motion } from 'framer-motion'
import { Plus, Pause, Play, Square, Trash2, ExternalLink } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { ListControls } from '../../ui/ListControls'
import { ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { Markdown } from '../../ui/Markdown'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Spark } from '../../ui/Spark'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { spring, expr } from '../../design/motion'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { api, type GoalLoop } from '../../lib/api'
import { loopKindMeta } from '../../lib/loopKind'
import { loopToGoalLoop } from './goalAdapter'
import { activePhaseIndex, phaseMinCycles, phaseForCycle } from './loopPhases'
import { LOOP_STATUS } from './loopStatusMeta'

// Keyed by LoopStatus PLUS the synthetic 'ended_early' (a non-genuine 'complete'),
// so the type is the broader string map. Shared with the dashboard Active Work
// widget via loopStatusMeta (single source of truth for the status color language).
const STATUS = LOOP_STATUS

// Goal-type glyph for the list row (§10.4) — the at-a-glance kind.
// Goal-type chip label. "open-ended" (not bare "open") so it doesn't read as a
// lifecycle status next to the status label (e.g. "◐ open · Completed" looked
// like a contradiction; "◐ open-ended · Completed" reads as type · status).
const GOAL_GLYPH: Record<string, string> = {
  verifiable: '✓ verifiable', open_ended: '◐ open-ended', monitor: '∞ monitor',
}

function ProgressRing({ pct, tone, size = 28 }: { pct: number; tone: string; size?: number }) {
  const r = size / 2 - 2.5, c = 2 * Math.PI * r
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-surface-high)" strokeWidth={2.5} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth={2.5} strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - pct)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
    </svg>
  )
}

// running first, then by recency
function order(a: GoalLoop, b: GoalLoop) {
  if ((a.status === 'running') !== (b.status === 'running')) return a.status === 'running' ? -1 : 1
  return (b.started_at ?? b.created_at) - (a.started_at ?? a.created_at)
}

export function LoopsListPage({ onOpen, onCreate, query, setQuery }: { onOpen: (id: string) => void; onCreate: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  // Cached list (instant paint on revisit) that still polls — persist:false so the
  // live status (running / cycle counts) is never stale across a hard reload.
  // This list is the back-target for the general/goal/design cockpits (Code keeps its own
  // section at #/code), so it shows ALL non-code kinds — not just goal, which would hide
  // a General or Design loop from the only list that links to it. The goalAdapter is
  // kind-agnostic (defaults for missing fields), so general/design rows render fine.
  const { data: loops, refresh } = useCachedData('loops', () => api.uLoops().then((ls) => ls.filter((l) => l.kind !== 'code').map(loopToGoalLoop).sort(order)).catch(() => [] as GoalLoop[]), { persist: false })
  // Row whose delete is armed (first click), cleared on a second click or timeout.
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  // Peek: a row click opens a quick-glance side panel (URL-backed ?peek=<id>);
  // the panel's "Open full" goes to the dedicated cockpit. Mirrors the Tasks list.
  const [peekId, setPeekId] = useQueryParam(query, setQuery, 'peek', '')
  const peek = peekId ? (loops?.find((l) => l.id === peekId) ?? null) : null
  // Default to active goals only; the user can switch to All/Ongoing/Done — URL-backed.
  const [filterRaw, setFilter] = useQueryParam(query, setQuery, 'filter', 'active', { replace: true })
  const filter = filterRaw as 'all' | 'active' | 'ongoing' | 'done'

  // Poll only while at least one loop is non-terminal (it can still change) AND the
  // tab is visible. A list of only finished loops never changes, so polling it every
  // 4s forever just hammers the API for nothing — pass null to disable until a live
  // loop appears (the `loops` dep flips hasLive on any status change).
  const hasLive = (loops ?? []).some((l) => !['complete', 'stopped', 'failed'].includes(l.status))
  useVisiblePoll(refresh, hasLive ? 4000 : null)

  async function act(e: React.MouseEvent | undefined, id: string, action: 'pause' | 'resume' | 'stop') {
    e?.stopPropagation()
    await api.uLoopAction(id, action).catch(() => {})
    invalidateCache('loops'); refresh()
  }

  // Delete a terminal loop from the list — two-step (arm, then confirm) like the
  // cockpit, so a hover misclick can't destroy a finished loop's history.
  async function del(e: React.MouseEvent | undefined, id: string) {
    e?.stopPropagation()
    if (confirmDelete !== id) { setConfirmDelete(id); window.setTimeout(() => setConfirmDelete((c) => (c === id ? null : c)), 4000); return }
    setConfirmDelete(null)
    await api.deleteULoop(id).catch(() => {})
    invalidateCache('loops'); refresh()
  }

  // "Active" must include the PRE-LAUNCH / planning states (intake/planning/review/
  // ready), not just the live ones — else a just-created loop awaiting launch, or one
  // mid-planning, matches no filter and is INVISIBLE under the default 'active' view
  // (it'd only show under 'All'). A loop is "done" only when terminal; everything
  // not-terminal-and-not-ongoing-only is active work the user is shepherding.
  const DONE_ST = ['complete', 'stopped', 'failed']
  const ACTIVE_ST = ['running', 'paused', 'stagnant', 'needs_input', 'intake', 'planning', 'review', 'ready']
  const matchesFilter = (c: GoalLoop, f: typeof filter) =>
    f === 'all' ? true
    : f === 'active' ? ACTIVE_ST.includes(c.status)
    : f === 'ongoing' ? (c.max_cycles === 0 || c.granularity === 'forever' || c.goal_type === 'monitor')
    : DONE_ST.includes(c.status)  // done
  const matches = (c: GoalLoop) => matchesFilter(c, filter)
  const count = (f: typeof filter) => (loops ?? []).filter((c) => matchesFilter(c, f)).length

  const filterSection: FilterSectionDef = {
    title: 'Show', value: filter, defaultKey: 'active',
    onChange: (k) => setFilter(k as typeof filter),
    options: [
      { key: 'active', label: 'Active', count: count('active') },
      { key: 'all', label: 'All', count: count('all') },
      { key: 'ongoing', label: 'Ongoing', count: count('ongoing') },
      { key: 'done', label: 'Done', count: count('done') },
    ],
  }

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<span data-type="title-l" className="text-on-surface">Loops</span>}
          right={
            <div className="flex items-center gap-s">
              <Button size="sm" className="h-10" onClick={onCreate}><Plus size={16} /> New loop</Button>
            </div>
          }
        />
      }
      controls={
        // Active / Ongoing (forever + monitor) / Done filters (§10.4) — on the page,
        // not the header.
        <ListControls>
          <FilterMenu sections={[filterSection]} />
        </ListControls>
      }
      panel={peek && (
        <SidePanel key={peek.id} fillHeight storeKey="loop-peek-w"
          icon={(() => { const KI = loopKindMeta((peek as { kind?: string }).kind).icon; return <KI size={18} className="text-primary" /> })()} title={peek.name || peek.goal.slice(0, 60)}
          onClose={() => setPeekId('')}>
          <LoopPeek loop={peek} onOpenFull={() => onOpen(peek.id)} />
        </SidePanel>
      )}
    >
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {loops === undefined ? (
            <ListSkeleton rows={6} />
          ) : loops.length === 0 ? (
            <div className="flex flex-col items-center gap-l py-2xl text-center">
              <Spark size={36} />
              <div>
                <h2 data-type="headline-s" className="text-on-surface">No loops yet</h2>
                <p className="mt-1 text-on-surface-low text-[0.9375rem] max-w-[400px]">Describe a task and let an agent classify, plan, and pursue it autonomously.</p>
              </div>
              <Button onClick={onCreate}><Plus size={16} /> Start a loop</Button>
            </div>
          ) : loops.filter(matches).length === 0 ? (
            // Loops exist, but none match the active filter — say so instead of
            // rendering a blank page (e.g. all loops done while on 'Active').
            <div className="flex flex-col items-center gap-m py-2xl text-center">
              <span className="text-on-surface-low opacity-60"><Spark size={28} animated={false} /></span>
              <p className="text-on-surface-low text-[0.9375rem] max-w-[360px]">
                {filter === 'active' ? 'No active loops right now.'
                  : filter === 'ongoing' ? 'No ongoing loops.'
                  : filter === 'done' ? 'No finished loops yet.'
                  : 'No loops match this filter.'}
              </p>
              <button type="button" onClick={() => setFilter('all')}
                className="text-primary text-[0.8125rem] hover:underline">View all loops</button>
            </div>
          ) : (
            <div className="flex flex-col gap-s">
              {loops
                .filter(matches)
                .map((c, i) => {
                // A 'complete' loop with an error_message ended early (budget exhausted,
                // DoD unmet) → the synthetic 'ended_early' meta, so it doesn't read as a
                // genuine completion. Mirrors effectiveLoopStatus on the Code surfaces.
                const endedEarly = c.status === 'complete' && !!c.error_message
                const st = endedEarly ? STATUS.ended_early : (STATUS[c.status] ?? STATUS.ready)
                // A GENUINELY completed loop reached its Definition of Done — show a full
                // ring. An ended-early one didn't, so its ring tracks actual cycle
                // progress (capped at 1), not a misleading full ring.
                const pct = c.status === 'complete' && !endedEarly
                  ? 1
                  : (c.max_cycles ? Math.min(1, c.total_cycles / c.max_cycles) : 0)
                const running = c.status === 'running'
                // While running, count the in-flight cycle so the list matches the
                // cockpit header (total_cycles is the COMPLETED count).
                const shownCycle = running ? c.total_cycles + 1 : c.total_cycles
                const latest = c.findings?.length ? c.findings[c.findings.length - 1] : null
                const latestText = latest?.key_insight || latest?.summary
                // Right-click / long-press → the SAME actions the row's click/hover
                // buttons already fire (peek-open, pause/resume/stop, delete), via the
                // shared ContextMenu primitive. Zero-arg onSelect → the (e,id) handlers
                // take an optional event, so there's no click to stopPropagation here.
                const menuItems: ContextMenuItem[] = [
                  { icon: <ExternalLink size={15} />, label: 'Open', onSelect: () => setPeekId(c.id) },
                  ...(running ? [{ icon: <Pause size={15} />, label: 'Pause', onSelect: () => act(undefined, c.id, 'pause') }] : []),
                  ...(['paused', 'stagnant', 'needs_input'].includes(c.status) ? [{ icon: <Play size={15} />, label: 'Resume', onSelect: () => act(undefined, c.id, 'resume') }] : []),
                  ...(ACTIVE_ST.includes(c.status) ? [{ icon: <Square size={15} />, label: 'Stop', onSelect: () => act(undefined, c.id, 'stop') }] : []),
                  ...(['complete', 'stopped', 'failed'].includes(c.status) ? [{ icon: <Trash2 size={15} />, label: 'Delete', danger: true, onSelect: () => del(undefined, c.id) }] : []),
                ]
                return (
                  <ContextMenu key={c.id} items={menuItems}>
                  <motion.div
                    initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: i * 0.03 }}
                    whileHover={{ y: -expr(3, 0.3), boxShadow: 'var(--shadow-lift)' }}
                    whileTap={{ scale: 1 - expr(0.008, 0.3) }}
                    onClick={() => setPeekId(c.id)}
                    className={`group relative flex items-center gap-l rounded-lg px-l py-l text-left cursor-pointer transition-colors overflow-hidden ${peekId === c.id ? 'bg-surface-high ring-1 ring-primary/40' : 'bg-surface-container hover:bg-surface-high'}`}
                  >
                    {/* running: faint left glow accent */}
                    {running && <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: 'var(--color-ok)' }} />}
                    <span className="shrink-0 inline-flex items-center justify-center size-10 rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)' }}>
                      {(() => { const KI = loopKindMeta((c as { kind?: string }).kind).icon; return <KI size={20} className="text-primary" /> })()}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-s">
                        <span className="size-1.5 rounded-pill shrink-0" style={{ background: st.tone }} />
                        <span className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{c.name || c.goal.slice(0, 70)}</span>
                        {/* kind chip: goal shows its goal-type glyph; general/design show the kind. */}
                        {(() => { const k = (c as { kind?: string }).kind
                          const label = k === 'design' ? 'design' : k === 'general' ? 'loop' : (GOAL_GLYPH[c.goal_type] ?? c.goal_type)
                          const title = k === 'design' ? 'design loop' : k === 'general' ? 'general loop' : `${c.goal_type} goal`
                          return <span className="shrink-0 rounded-pill px-1.5 h-4 inline-flex items-center text-[0.65rem] uppercase tracking-wide bg-surface-high text-on-surface-low" title={title}>{label}</span> })()}
                        <span className="shrink-0 text-on-surface-low text-[0.75rem]">· {st.label}{(running || c.status === 'paused') && (c.max_cycles === 0 ? ` · ongoing · cycle ${shownCycle}` : ` · cycle ${shownCycle}/${c.max_cycles}`)}</span>
                      </div>
                      <p className="mt-1 text-on-surface-low text-[0.8125rem] truncate">
                        {latestText ? <span className="text-on-surface-var">↳ {latestText}</span> : c.goal}
                      </p>
                    </div>

                    {/* hover quick-actions */}
                    <div className={`flex items-center gap-1 shrink-0 transition-opacity ${confirmDelete === c.id ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}>
                      {running && <IconButton icon={Pause} label="Pause" size={34} onClick={(e) => act(e, c.id, 'pause')} />}
                      {['paused', 'stagnant', 'needs_input'].includes(c.status) && <IconButton icon={Play} label="Resume" size={34} onClick={(e) => act(e, c.id, 'resume')} />}
                      {ACTIVE_ST.includes(c.status) && <IconButton icon={Square} label="Stop" size={34} onClick={(e) => act(e, c.id, 'stop')} />}
                      {['complete', 'stopped', 'failed'].includes(c.status) && (
                        <IconButton icon={Trash2} size={34}
                          label={confirmDelete === c.id ? 'Click again to delete' : 'Delete loop'}
                          onClick={(e) => del(e, c.id)}
                          className={confirmDelete === c.id ? 'text-danger' : undefined} />
                      )}
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      <ProgressRing pct={pct} tone={st.tone} />
                      <span className="text-on-surface-low text-[0.75rem] tabular-nums w-9">{c.findings?.length ?? 0} fnd</span>
                    </div>
                  </motion.div>
                  </ContextMenu>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </WorkbenchLayout>
  )
}

/** Quick-glance peek of a loop in the side panel: status, config, latest finding,
 *  and a jump to the full cockpit. Read-only — the cockpit owns the controls; this
 *  is for a fast look without leaving the list. Kind-neutral: the list shows
 *  goal/general/design (code has its own section), so labels say "loop" and the
 *  goal-type glyph only renders for the goal kind. */
function LoopPeek({ loop, onOpenFull }: { loop: GoalLoop; onOpenFull: () => void }) {
  const st = STATUS[loop.status] ?? STATUS.ready
  const running = loop.status === 'running'
  const kind = (loop as { kind?: string }).kind
  const shownCycle = running ? loop.total_cycles + 1 : loop.total_cycles
  const cycleLabel = loop.max_cycles === 0 ? `cycle ${shownCycle} · ongoing` : `cycle ${shownCycle}/${loop.max_cycles}`
  const latest = loop.findings?.length ? loop.findings[loop.findings.length - 1] : null
  const latestText = latest?.key_insight || latest?.summary
  return (
    <div className="flex flex-col gap-l">
      <Button onClick={onOpenFull}><ExternalLink size={15} /> Open full loop</Button>

      <div className="flex flex-wrap items-center gap-s text-[0.8125rem]">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: `color-mix(in srgb, ${st.tone} 16%, transparent)`, color: st.tone }}>
          <span className="size-1.5 rounded-pill" style={{ background: st.tone }} /> {st.label}
        </span>
        {/* goal-type glyph is meaningful only for the goal kind; general/design have none. */}
        {kind === 'design' ? <span className="text-on-surface-low">design</span>
          : kind === 'general' ? <span className="text-on-surface-low">loop</span>
          : <span className="text-on-surface-low">{GOAL_GLYPH[loop.goal_type] ?? loop.goal_type}</span>}
        {(running || loop.status === 'paused') && <span className="text-on-surface-low">· {cycleLabel}</span>}
      </div>

      <div>
        <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{kind === 'design' || kind === 'general' ? 'Task' : 'Goal'}</div>
        <div className="text-on-surface text-[0.9375rem]"><Markdown>{loop.goal}</Markdown></div>
        {loop.success_criteria && <p className="mt-2 text-on-surface-low text-[0.8125rem]"><span className="text-on-surface-var">Done when:</span> {loop.success_criteria}</p>}
      </div>

      {(loop.sub_goals?.length ?? 0) > 0 && (
        <div>
          <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">Sub-goals · {loop.sub_goals.length}</div>
          <ul className="flex flex-col gap-1.5">
            {loop.sub_goals.map((s, i) => (
              <li key={i} className="flex items-start gap-s text-on-surface-var text-[0.875rem]">
                <span className="mt-1.5 size-1 shrink-0 rounded-pill bg-primary" />{typeof s === 'string' ? s : JSON.stringify(s)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(() => {
        const plan = (loop.execution_plan ?? []) as Record<string, unknown>[]
        if (!plan.length) return null
        const active = activePhaseIndex(loop.total_cycles, plan)
        const isActive = (i: number) => active >= 0 && i === active && (running || loop.status === 'paused')
        const fnd = loop.findings ?? []
        const cyclesIn = (i: number) => fnd.filter((f) => phaseForCycle(f.cycle, plan) === i).length
        return (
          <div>
            <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">Execution plan · {plan.length} phases</div>
            <ol className="flex flex-col gap-1">
              {plan.map((p, i) => {
                const role = String(p.role || '').trim()
                const target = String(p.target || '').trim()
                const agent = String(p.agent_name || '').trim()
                const done = cyclesIn(i)
                const minC = phaseMinCycles(p)
                const count = done === 0 ? '' : done >= minC ? `${done} ${done === 1 ? 'cycle' : 'cycles'}` : `${done}/${minC}`
                return (
                  <li key={i} className={`flex items-start gap-s text-[0.8125rem] rounded-md px-2 py-1 -mx-2 ${isActive(i) ? 'bg-surface-high' : ''}`}>
                    <span className="shrink-0 mt-0.5 inline-flex size-4 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.65rem] tabular-nums">{i + 1}</span>
                    <span className="flex-1 min-w-0 text-on-surface-var">
                      {role && <span className="text-on-surface" style={{ fontVariationSettings: '"wght" 550' }}>{role}</span>}
                      {/* the agent definition backing the role this phase */}
                      <span className="text-on-surface-low"> · {agent || 'default worker'}</span>
                      <span>: {target || '(phase)'}</span>
                      {isActive(i) && <span className="ml-1.5 text-primary text-[0.6rem] uppercase tracking-wide">● active</span>}
                    </span>
                    {count && <span className="shrink-0 mt-0.5 text-on-surface-low text-[0.65rem] tabular-nums">{count}</span>}
                  </li>
                )
              })}
            </ol>
          </div>
        )
      })()}

      {latestText && (
        <div>
          <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">Latest finding · {loop.findings?.length ?? 0} total</div>
          <p className="text-on-surface-var text-[0.875rem]">{latestText}</p>
        </div>
      )}
    </div>
  )
}
