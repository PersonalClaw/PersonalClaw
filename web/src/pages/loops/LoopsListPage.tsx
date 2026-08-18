import { useState } from 'react'
import { reportingWrite } from '../../app/reportingWrite'
import { fvs } from '../../design/fontWeight'
import { motion } from 'framer-motion'
import { Plus, Pause, Play, Square, Trash2, ExternalLink, Filter, Repeat } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { ListControls } from '../../ui/ListControls'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { SidePanel } from '../../ui/SidePanel'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { FeedbackThumbs } from '../../ui/FeedbackThumbs'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { Markdown } from '../../ui/Markdown'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { ProgressRing } from '../../ui/ProgressRing'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { spring, expr } from '../../design/motion'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { api, type GoalLoop } from '../../lib/api'
import { loopKindMeta } from '../../lib/loopKind'
import { loopToGoalLoop } from './goalAdapter'
import { rowSubject } from '../../lib/rowSubject'
import { activePhaseIndex, phaseMinCycles, phaseForCycle, hasDistinctName } from './loopPhases'
import { LOOP_STATUS } from './loopStatusMeta'
import { PageTitle } from '../../ui/PageTitle'
import { notify } from '../../app/appSdk'

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

// `ProgressRing` moved to ui/ProgressRing. This copy matched the dashboard's on every number and
// differed only in setting strokeDashoffset directly — so the ring JUMPED here while the row around
// it animated. Adopting the primitive is what gives these rows the spring the dashboard already had.

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
  // No `.catch(() => [])` here, deliberately. Swallowing the rejection into an empty
  // array made `useCachedData`'s `error` permanently null, so a failed `GET /api/loops`
  // was indistinguishable from having none and the page said "No loops yet — Start a
  // loop" to a user whose loops were merely unreachable. The rejection propagates so the
  // one condition below (`error` with no data) can tell the two facts apart.
  const { data: loops, error: loopsErr, refresh } = useCachedData<GoalLoop[]>('loops', () => api.uLoops().then((ls) => ls.filter((l) => l.kind !== 'code').map(loopToGoalLoop).sort(order)), { persist: false })
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
    // Data-driven: the row's status comes from `refresh()`, not a local flip. A swallowed rejection
    // left the row exactly as it was with nothing said — and a silent "stop" is the shape whose
    // failure a user ACTS on, because the next assumption is that the loop is no longer running.
    if (!(await reportingWrite(`${action} this loop`, () => api.uLoopAction(id, action)))) return
    invalidateCache('loops'); refresh()
  }

  // Delete a terminal loop from the list — two-step (arm, then confirm) like the
  // cockpit, so a hover misclick can't destroy a finished loop's history.
  async function del(e: React.MouseEvent | undefined, id: string) {
    e?.stopPropagation()
    if (confirmDelete !== id) { setConfirmDelete(id); window.setTimeout(() => setConfirmDelete((c) => (c === id ? null : c)), 4000); return }
    setConfirmDelete(null)
    // Swallowing this made the row vanish and then come back on the refetch, unexplained. Say why.
    try { await api.deleteULoop(id) }
    catch (e) { notify(`Couldn't delete this loop: ${String((e as Error)?.message || e)}`, 'error') }
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
          left={<PageTitle>Loops</PageTitle>}
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
        <ListControls results={{ count: (loops ?? []).filter(matches).length, noun: 'loops', active: filter !== 'active' }}>
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
          {loops === undefined && loopsErr ? (
            // "We couldn't load them" is a different fact from "you have none", and it is
            // the one that must NOT read as an invitation to create your first loop.
            <LoadError what="loops" error={loopsErr} onRetry={refresh} />
          ) : loops === undefined ? (
            <ListSkeleton rows={6} what="loops" />
          ) : loops.length === 0 ? (
            <EmptyState
              icon={Repeat}
              title="No loops yet"
              hint="Describe a task and let an agent classify, plan, and pursue it autonomously."
              action={{ label: 'Start a loop', onClick: onCreate, icon: Plus }}
            />
          ) : loops.filter(matches).length === 0 ? (
            // Loops exist, but none match the active facet — say so through the SHARED
            // primitive rather than a hand-rolled centered <p>, so this reads like every
            // other narrowed-to-nothing list. Reachable only with `filter !== 'all'`
            // (the 'all' facet matches everything and the list is non-empty here), so
            // "View all loops" is always a real escape. No create affordance: the user
            // has loops, so offering to make one answers a question they did not ask.
            <EmptyState
              icon={Filter}
              title={filter === 'active' ? 'No active loops right now'
                : filter === 'ongoing' ? 'No ongoing loops'
                : filter === 'done' ? 'No finished loops yet'
                : 'No loops match this filter'}
              hint={`You have ${loops.length} loop${loops.length === 1 ? '' : 's'} — just none in this view.`}
              action={{ label: 'View all loops', onClick: () => setFilter('all') }}
            />
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
                // 🔴 THE ROW PRINTED THE SAME SENTENCE TWICE. The title is the loop's name and the
                // line beneath it was `c.goal` — but a loop with no explicit name IS named from its
                // goal, so both lines carried one truncated sentence. The second line's job is "the
                // latest on this loop"; with no finding to report and no name of its own, it has
                // nothing to add, and filler is worse than a shorter row.
                const title = c.name || c.goal
                const goalEarnsItsLine = hasDistinctName(c.name, c.goal)
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
                    // 🔴 THIS ROW WAS A FOCUSABLE THAT DID NOTHING — the worse half of the tasks-list
                    // defect. `whileHover`/`whileTap` make Motion add its own `tabindex="0"`, so the
                    // keyboard DID land here (measured: a 2px outline on a `div` with no role), and
                    // then Enter and Space were both dead — driven on `#/loops/history`, body text
                    // frozen at 533 chars for each, while a mouse click opened the peek panel (864).
                    // `tabIndex={-1}` retires that nameless stop and `RowHitTarget` puts a real,
                    // named button in its place.
                    tabIndex={-1}
                    className={`group relative flex items-center gap-l rounded-lg px-l py-l text-left cursor-pointer transition-colors overflow-hidden has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary/50 ${peekId === c.id ? 'bg-surface-high ring-1 ring-primary/40' : 'bg-surface-container hover:bg-surface-high'}`}
                  >
                    <RowHitTarget label={rowSubject([title])} />
                    {/* running: faint left glow accent */}
                    {running && <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: 'var(--color-ok)' }} />}
                    <span className="shrink-0 inline-flex items-center justify-center size-10 rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)' }}>
                      {(() => { const KI = loopKindMeta((c as { kind?: string }).kind).icon; return <KI size={20} className="text-primary" /> })()}
                    </span>
                    {/* The floor keeps every row the same height whether or not the second line has
                        content — a row with nothing to add stays in the list's rhythm, and no empty
                        element is left behind to hold the space. 2.875rem = the MEASURED two-line
                        block: 22.5px title line + 23.5px sub-line including its `mt-1`. If the type
                        scale moves, this is the number to re-measure (a row without the second line
                        rendered 76px against its siblings' 78px before it was pinned). */}
                    <div className="flex-1 min-w-0 min-h-[2.875rem]">
                      <div className="flex items-center gap-s">
                        <span className="size-1.5 rounded-pill shrink-0" style={{ background: st.tone }} />
                        <span className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{title}</span>
                        {/* kind chip: goal shows its goal-type glyph; general/design show the kind. */}
                        {(() => { const k = (c as { kind?: string }).kind
                          const label = k === 'design' ? 'design' : k === 'general' ? 'loop' : (GOAL_GLYPH[c.goal_type] ?? c.goal_type)
                          const title = k === 'design' ? 'design loop' : k === 'general' ? 'general loop' : `${c.goal_type} goal`
                          return <span className="shrink-0 rounded-pill px-1.5 h-4 inline-flex items-center text-[0.75rem] uppercase tracking-wide bg-surface-high text-on-surface-low" title={title}>{label}</span> })()}
                        <span className="shrink-0 text-on-surface-low text-[0.75rem]">· {st.label}{(running || c.status === 'paused') && (c.max_cycles === 0 ? ` · ongoing · cycle ${shownCycle}` : ` · cycle ${shownCycle}/${c.max_cycles}`)}</span>
                      </div>
                      {(latestText || goalEarnsItsLine) && (
                        <p className="mt-1 text-on-surface-low text-[0.8125rem] truncate">
                          {latestText ? <span className="text-on-surface-var">↳ {latestText}</span> : c.goal}
                        </p>
                      )}
                    </div>

                    {/* hover quick-actions */}
                    <div className={`flex items-center gap-1 shrink-0 transition-opacity ${confirmDelete === c.id ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
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
                      <ProgressRing pct={pct} tone={st.tone} label={`Cycle progress: ${shownCycle}${c.max_cycles ? ` of ${c.max_cycles}` : ''}`} />
                      {/* 🪤 "fnd" IS AN ABBREVIATION NOTHING ELSE IN THE APP USES, and a screen reader
                          reads it literally. The visible form cannot grow — the box is `w-9` (36px), and
                          widening it reflows the row — so the abbreviation stays for the eye and the full
                          word is added for assistive tech, through the `sr-only` idiom this app already
                          uses in 19 places. `title` gives a sighted user the same expansion on hover. */}
                      <span className="text-on-surface-low text-[0.75rem] tabular-nums w-9"
                        title={`${c.findings?.length ?? 0} findings`}>
                        <span aria-hidden="true">{c.findings?.length ?? 0} fnd</span>
                        <span className="sr-only">{c.findings?.length ?? 0} findings</span>
                      </span>
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
        <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{kind === 'design' || kind === 'general' ? 'Task' : 'Goal'}</div>
        <div className="text-on-surface text-[0.9375rem]"><Markdown>{loop.goal}</Markdown></div>
        {loop.success_criteria && <p className="mt-2 text-on-surface-low text-[0.8125rem]"><span className="text-on-surface-var">Done when:</span> {loop.success_criteria}</p>}
      </div>

      {(loop.sub_goals?.length ?? 0) > 0 && (
        <div>
          <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">Sub-goals · {loop.sub_goals.length}</div>
          <ul className="flex flex-col gap-1.5">
            {loop.sub_goals.map((s, i) => (
              <li key={i} className="flex items-start gap-s text-on-surface-var text-[0.8125rem]">
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
            <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">Execution plan · {plan.length} phases</div>
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
                    <span className="shrink-0 mt-0.5 inline-flex size-4 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.75rem] tabular-nums">{i + 1}</span>
                    <span className="flex-1 min-w-0 text-on-surface-var">
                      {role && <span className="text-on-surface" style={fvs(550)}>{role}</span>}
                      {/* the agent definition backing the role this phase */}
                      <span className="text-on-surface-low"> · {agent || 'default worker'}</span>
                      <span>: {target || '(phase)'}</span>
                      {isActive(i) && <span className="ml-1.5 text-primary text-[0.75rem] uppercase tracking-wide">● active</span>}
                    </span>
                    {count && <span className="shrink-0 mt-0.5 text-on-surface-low text-[0.75rem] tabular-nums">{count}</span>}
                  </li>
                )
              })}
            </ol>
          </div>
        )
      })()}

      {latestText && (
        <div>
          <div className="mb-1.5 flex items-center gap-s">
            <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Latest finding · {loop.findings?.length ?? 0} total</span>
            {/* Findings are AI judgments (plan 58): thumbs attribute to the
                per-kind loop judge. Target = this loop's latest finding cycle. */}
            <FeedbackThumbs targetKind="loop_finding"
              targetId={`${loop.id}:${latest?.cycle ?? loop.findings!.length}`}
              producer={loop.feedback_producer}
              snapshot={{ key_insight: (latestText ?? '').slice(0, 200) }} />
            {/* Investigate (plan 60): chat about this finding with the loop's goal,
                the finding, and its judge verdict pre-loaded (fenced, ask mode). */}
            <InvestigateButton kind="loop_finding" id={`${loop.id}:${latest?.cycle ?? ''}`} backLink={`#/loops/${loop.id}`} />
          </div>
          <p className="text-on-surface-var text-[0.8125rem]">{latestText}</p>
        </div>
      )}
    </div>
  )
}
