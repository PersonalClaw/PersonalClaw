import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Users } from 'lucide-react'
import { api, type AgentDayRead, type Loop, type SpawnedAgent, type UsageBudget } from '../../../lib/api'
import { useQuery } from '../../../lib/data/useQuery'
import { useNeedsYou } from '../../../lib/useNeedsYou'
import { useAgentActivity } from '../../../lib/useAgentActivity'
import { useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { Button } from '../../../ui/Button'
import { FilterMenu } from '../../../ui/FilterMenu'
import { TextLink } from '../../../ui/TextLink'
import { StateMark } from '../../../ui/StateMark'
import { TOUCH_TARGET } from '../../../ui/Toggle'
import { fvs } from '../../../design/fontWeight'
import type { PhaseFamily } from '../../../lib/workPhase'
import { SlotEmptyState } from '../widgets/kit'
import { Section } from '../Section'
import { buildDay, clock, type DayRow, type DayView, type Mark, type Occurrence, type RowState } from './agentDayModel'

// ── Home › Overview: "Your agents today" ─────────────────────────────────────────────────────────
//
// A row per agent on a window around now (`agentDayModel.ts` holds every rule). Nothing moves: the
// window steps once a minute, which only re-draws, so reduced motion needs nothing of its own. The
// band reads again when the gateway rings that work changed, at most once every 30 seconds, so a
// burst of rings makes one request. An ask is answered in Attention, never here: its row links there.

type Filter = 'all' | RowState
const NARROW = '(max-width: 699px)'
const STEP_MS = 60_000
/** The least time between two reads of the band: the gateway's rings wait for it. */
export const READ_GAP_MS = 30_000
/** How long a ring waits for the rest of its burst before the band reads. */
const SETTLE_MS = 600
const STATE_WORD: Record<RowState, string> = { waiting: 'Waiting on you', failed: 'Failed', working: 'Working', idle: 'Idle' }
const FAMILY: Record<RowState, PhaseFamily> = { waiting: 'waiting', failed: 'blocked', working: 'active', idle: 'quiet' }

interface DaySources {
  day: AgentDayRead
  helpers: SpawnedAgent[]
  loops: Loop[]
  scheduled: Occurrence[]
  defaultAgent: string
}

async function readDay(): Promise<DaySources> {
  const now = Date.now()
  const [day, helpers, loops, week, agents] = await Promise.all([
    api.agentsDay(new Date(now - 4 * 3600_000).toISOString(), new Date(now).toISOString()),
    api.spawnedAgents(),
    api.uLoops(),
    api.triggersWeek(new Date(now).toISOString(), 2),
    api.agents(),
  ])
  return { day, helpers, loops, scheduled: week.occurrences, defaultAgent: agents.default_agent }
}

/** The spend meter is read on its own: a band whose meter cannot be read still says what ran, and
 *  says the spend is unknown. */
const readBudget = () => api.usageBudget()

function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), STEP_MS)
    return () => window.clearInterval(id)
  }, [])
  return now
}

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(NARROW).matches)
  useEffect(() => {
    const mq = window.matchMedia?.(NARROW)
    if (!mq) return
    const on = () => setNarrow(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return narrow
}

/** *refresh*, called at most once every `READ_GAP_MS` from the band's first read: a ring waits
 *  `SETTLE_MS` for the rest of its burst, or until the gap since the last read has passed, and
 *  every ring in that wait is the one read. */
function useReadAgain(refresh: () => void): () => void {
  const last = useRef(Date.now())
  const waiting = useRef<number | undefined>(undefined)
  useEffect(() => () => { if (waiting.current !== undefined) window.clearTimeout(waiting.current) }, [])
  return useCallback(() => {
    if (waiting.current !== undefined) return
    const wait = Math.max(SETTLE_MS, last.current + READ_GAP_MS - Date.now())
    waiting.current = window.setTimeout(() => {
      waiting.current = undefined
      last.current = Date.now()
      refresh()
    }, wait)
  }, [refresh])
}

/** The band: its reads, its clock and its filter, around `AgentDayView`. */
export function AgentDay() {
  const now = useNow()
  const narrow = useNarrow()
  const feed = useAgentActivity()
  const needs = useNeedsYou()
  const { data, error, loading, refresh } = useQuery<DaySources>('dashboard:agent-day', readDay)
  const meter = useQuery<UsageBudget>('dashboard:agent-day-spend', readBudget)
  const refreshMeter = meter.refresh
  const readAgain = useReadAgain(useCallback(() => { refresh(); refreshMeter() }, [refresh, refreshMeter]))
  const onMessage = useCallback((m: WsMessage) => {
    if (m.type === 'running_changed' || m.type === 'subagent_done') readAgain()
  }, [readAgain])
  useChatSocket(onMessage, readAgain)
  const [filter, setFilter] = useState<Filter>('all')

  const view = useMemo(() => buildDay({
    now, narrow,
    defaultAgent: data?.defaultAgent ?? '',
    day: data?.day ?? null,
    live: feed.entities,
    asks: needs.rows,
    helpers: data?.helpers ?? [],
    loops: data?.loops ?? [],
    scheduled: data?.scheduled ?? [],
  }), [now, narrow, data, feed.entities, needs.rows])

  const failed = error ?? feed.error ?? needs.error
  const settling = (loading && !data) || feed.loading
  return (
    <AgentDayView view={view} failed={!!failed} settling={settling} narrow={narrow}
      budget={meter.error ? 'unknown' : meter.data ?? null} filter={filter} onFilter={setFilter} />
  )
}

/** What it has spent on its own today against the daily cap (the meter counts no chat turn). Nothing
 *  when nothing is spent: a zero is no news on a band whose job is to be calm. A meter that could
 *  not be read says so. */
function spendLine(b: UsageBudget | 'unknown' | null): string {
  if (b === 'unknown') return 'On its own today: spend unknown'
  if (!b || !(b.spent_dollars > 0)) return ''
  const spent = `$${b.spent_dollars.toFixed(2)}`
  return b.max_dollars_per_day ? `On its own today: ${spent} of $${b.max_dollars_per_day}` : `On its own today: ${spent}`
}

export function AgentDayView({ view, failed, settling, narrow, budget, filter, onFilter }: {
  view: DayView
  failed: boolean
  settling: boolean
  narrow: boolean
  budget: UsageBudget | 'unknown' | null
  filter: Filter
  onFilter: (f: Filter) => void
}) {
  const [idleOpen, setIdleOpen] = useState(false)
  const all = [...view.rows, ...view.idle]
  const count = (s: RowState) => all.filter((r) => r.key !== 'scheduled' && r.state === s).length
  const shown = (rows: DayRow[]) => (filter === 'all' ? rows : rows.filter((r) => r.key !== 'scheduled' && r.state === filter))
  const actions = (
    <div className="flex shrink-0 items-center gap-m">
      {spendLine(budget) && <span data-type="body-s" className="text-on-surface-var">{spendLine(budget)}</span>}
      <FilterMenu label="Show" className={TOUCH_TARGET} sections={[{
        title: 'Agents', value: filter, defaultKey: 'all', onChange: (k) => onFilter(k as Filter),
        options: [
          { key: 'all', label: 'All', count: all.filter((r) => r.key !== 'scheduled').length },
          { key: 'waiting', label: 'Waiting on you', count: view.waitingCount },
          { key: 'working', label: 'Working', count: count('working') },
          { key: 'failed', label: 'Failed', count: count('failed') },
          { key: 'idle', label: 'Idle', count: count('idle') },
        ],
      }]} />
      <TextLink href="#/agents" className={`inline-flex items-center ${TOUCH_TARGET}`}>Manage agents</TextLink>
    </div>
  )

  let body
  if (failed) {
    body = <SlotEmptyState icon={Users}>Couldn&rsquo;t read what your agents did, so this is unknown right now.</SlotEmptyState>
  } else if (settling) {
    body = null
  } else {
    const hours = narrow ? 3 : 4
    body = (
      <div className="flex min-w-0 flex-col gap-s rounded-lg bg-surface-low px-m py-s">
        <Axis view={view} />
        {view.empty && (
          <p data-type="body-m" className="text-on-surface-var">
            {view.empty === 'today' ? 'Nothing ran today.' : `Nothing ran in the last ${hours} hours.`}
          </p>
        )}
        {all.length > 0 && <ul className="flex min-w-0 flex-col" aria-label="Your agents">
          {shown(view.rows).map((r) => <Row key={r.key} row={r} view={view} />)}
          {view.idle.length > 0 && (filter === 'all' || filter === 'idle') && (
            idleOpen
              ? view.idle.map((r) => <Row key={r.key} row={r} view={view} />)
              : (
                <li className="py-s">
                  <Button variant="ghost" size="sm" ariaExpanded={false} onClick={() => setIdleOpen(true)}>
                    {view.idle.length} idle
                  </Button>
                </li>
              )
          )}
        </ul>}
        {view.cut > 0 && (
          <p data-type="body-s" className="text-on-surface-var">
            {view.cut === 1 ? 'The oldest block' : `The ${view.cut} oldest blocks`} of work in these hours {view.cut === 1 ? 'is' : 'are'} not drawn.
          </p>
        )}
        {view.unattributed > 0 && (
          <p data-type="body-s" className="text-on-surface-var">
            {view.unattributed === 1 ? '1 more ask' : `${view.unattributed} more asks`} in{' '}
            <TextLink href="#/dashboard?view=attention">Attention</TextLink>
          </p>
        )}
      </div>
    )
  }
  return <Section label="Your agents today" icon={Users} actions={actions}>{body}</Section>
}

const pct = (t: number, view: DayView) => `${(((t - view.since) / (view.until - view.since)) * 100).toFixed(2)}%`

function Axis({ view }: { view: DayView }) {
  const ticks: number[] = []
  const first = new Date(view.since)
  first.setMinutes(0, 0, 0)
  for (let t = first.getTime() + 3600_000; t < view.until; t += 3600_000) ticks.push(t)
  return (
    <div aria-hidden="true" className="grid grid-cols-[minmax(0,12rem)_1fr] gap-m max-sm:grid-cols-1">
      <span className="max-sm:hidden" />
      <div className="relative h-5" data-type="body-s">
        {ticks.map((t) => (
          <span key={t} className="absolute -translate-x-1/2 text-on-surface-low" style={{ left: pct(t, view) }}>{clock(t)}</span>
        ))}
        <span className="absolute top-5 -translate-x-1/2 whitespace-nowrap text-on-surface-var" style={{ left: '50%' }}>now {clock(view.now)}</span>
      </div>
    </div>
  )
}

/** Past this share of the track, a scheduled run's name is drawn before its mark. */
const LATE = 0.75

function markStyle(m: Mark, view: DayView): CSSProperties {
  const left = pct(m.start, view)
  if (m.kind === 'failed' || m.kind === 'scheduled') return { left }
  const width = `max(${(((m.end - m.start) / (view.until - view.since)) * 100).toFixed(2)}%, 4px)`
  return { left, width }
}

function Row({ row, view }: { row: DayRow; view: DayView }) {
  const name = row.link ? <a href={row.link} className="hover:underline">{row.name}</a> : <span>{row.name}</span>
  return (
    <li className="grid min-w-0 grid-cols-[minmax(0,12rem)_1fr] items-center gap-m border-t border-outline-variant/30 py-s first:border-t-0 max-sm:grid-cols-1"
      aria-label={row.sentence}>
      <div className="flex min-w-0 flex-col">
        <span data-type="body-m" className="truncate text-on-surface" style={fvs(500)}>{name}</span>
        {row.key === 'scheduled'
          ? <span data-type="body-s" className="text-on-surface-var">Agent not named</span>
          : (
            <StateMark family={FAMILY[row.state]} className="text-on-surface-var">
              <span data-type="body-s">{STATE_WORD[row.state]}{row.asks > 1 ? ` · ${row.asks} asks` : ''}</span>
            </StateMark>
          )}
      </div>
      <div className="relative min-w-0">
        <div aria-hidden="true" className="relative h-7">
          <span className="absolute inset-x-0 top-1/2 h-px bg-outline-variant/35" />
          {row.before && <span data-type="body-s" className="absolute left-0 top-1/2 -translate-y-1/2 bg-surface-low pr-xs text-on-surface-low">&larr; {row.before}</span>}
          {row.after && <span data-type="body-s" className="absolute right-0 top-1/2 -translate-y-1/2 bg-surface-low pl-xs text-on-surface-low">{row.after} &rarr;</span>}
          {row.marks.map((m, i) => <MarkView key={i} mark={m} view={view} />)}
          <span className="absolute -inset-y-1 left-1/2 w-px bg-on-surface-var" />
        </div>
        <p data-type="body-s" className="truncate text-on-surface-var">
          {row.say}
          {row.attention && <>{' · '}<TextLink href={row.attention}>Answer in Attention</TextLink></>}
        </p>
      </div>
    </li>
  )
}

function MarkView({ mark, view }: { mark: Mark; view: DayView }) {
  const base = 'absolute top-1/2 -translate-y-1/2'
  const style = markStyle(mark, view)
  switch (mark.kind) {
    case 'done':
      return <span className={`${base} h-2 rounded-pill`} style={{ ...style, background: 'color-mix(in oklab, var(--color-state-quiet) 38%, var(--color-surface))' }} />
    case 'working':
      return <span className={`${base} h-3 rounded-pill`} style={{ ...style, background: 'color-mix(in oklab, var(--color-state-active) 60%, var(--color-surface))' }} />
    case 'waiting':
      return <span className={`${base} h-3 rounded-pill`} style={{ ...style, background: 'var(--color-state-waiting)' }} />
    case 'failed':
      return <span className={`${base} size-3 -translate-x-1/2 rounded-pill`} style={{ ...style, background: 'var(--color-state-blocked)' }} />
    case 'scheduled': {
      // A run late in the window says its name before its mark, so the track's edge never cuts it.
      const late = (mark.start - view.since) / (view.until - view.since) > LATE
      return (
        <span data-late={late || undefined} style={style}
          className={`${base} flex items-center gap-xs whitespace-nowrap ${late ? '-translate-x-full flex-row-reverse' : ''}`}>
          <span className={`size-3 rounded-pill border border-dashed border-on-surface-var ${late ? 'translate-x-1/2' : '-translate-x-1/2'}`} />
          <span data-type="body-s" className="text-on-surface-var">{mark.label}</span>
        </span>
      )
    }
    case 'tail':
      return (
        <span className={`${base} flex h-4 items-center overflow-hidden rounded-pill border border-dashed border-on-surface-var/60`} style={style}>
          <span data-type="body-s" className="truncate px-xs text-on-surface-var">{mark.label}</span>
        </span>
      )
  }
}
