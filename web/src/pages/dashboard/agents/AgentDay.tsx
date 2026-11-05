import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Users } from 'lucide-react'
import { api, type AgentDayRead, type Loop, type SpawnedAgent, type UsageBudget } from '../../../lib/api'
import { useQuery } from '../../../lib/data/useQuery'
import { useNeedsYou } from '../../../lib/useNeedsYou'
import { useAgentActivity } from '../../../lib/useAgentActivity'
import { useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { Button } from '../../../ui/Button'
import { ButtonLink } from '../../../ui/ButtonLink'
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
//
// What you act on comes first: the rows that wait on you or have failed today sit on top,
// under a count of them, each on a ground of its state's colour (a tone, never a stripe: web/DESIGN.md's
// Tone-Not-Line rule), its state's dot and word in bold.
// Every other row is drawn plainly below them, on the same axis and the same now line. A row that
// failed today and waits or works now says when it failed after its line, so the count's failures
// are all on the band, those before the hours it draws too.

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
  // Until Needs You has answered, the band can't say what waits on you, so it says nothing yet.
  const settling = (loading && !data) || feed.loading || needs.loading
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
      <TextLink href="#/agents" ink="emphasis" className={`inline-flex items-center ${TOUCH_TARGET}`}>Manage agents</TextLink>
    </div>
  )

  let body
  if (failed) {
    body = <SlotEmptyState icon={Users}>Couldn&rsquo;t read what your agents did, so this is unknown right now.</SlotEmptyState>
  } else if (settling) {
    body = null
  } else {
    const hours = narrow ? 3 : 4
    const rows = shown(view.rows)
    const lifted = rows.filter(isLifted)
    const head = liftedHead(all, view)
    body = (
      <div className="flex min-w-0 flex-col gap-s rounded-lg bg-surface-low px-m py-s">
        {head && <p data-type="label-m" className={`text-on-surface ${INSET}`} style={fvs(600)}>{head}</p>}
        <Axis view={view} lifted={lifted.length > 0} />
        {view.empty && (
          <p data-type="body-m" className="text-on-surface-var">
            {view.empty === 'today' ? 'Nothing ran today.' : `Nothing ran in the last ${hours} hours.`}
          </p>
        )}
        {all.length > 0 && <ul className="flex min-w-0 flex-col" aria-label="Your agents">
          {lifted.map((r) => <LiftedRow key={r.key} row={r} view={view} />)}
          {rows.filter((r) => !isLifted(r)).map((r) => <Row key={r.key} row={r} view={view} />)}
          {view.idle.length > 0 && (filter === 'all' || filter === 'idle') && (
            idleOpen
              ? view.idle.map((r) => <Row key={r.key} row={r} view={view} />)
              : (
                <li className="py-s">
                  <Button variant="ghost" size="sm" className={TOUCH_TARGET} ariaExpanded={false} onClick={() => setIdleOpen(true)}>
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
            {/* "more" only beside an ask a row holds: with none, these are all there is. */}
            {`${view.unattributed} ${view.waitingCount > view.unattributed ? 'more ' : ''}${view.unattributed === 1 ? 'ask' : 'asks'}`} in{' '}
            <TextLink href="#/dashboard?view=attention" ink="emphasis" className={`inline-flex items-center ${TOUCH_TARGET}`}>Attention</TextLink>
          </p>
        )}
      </div>
    )
  }
  return <Section label="Your agents today" icon={Users} actions={actions}>{body}</Section>
}

const pct = (t: number, view: DayView) => `${(((t - view.since) / (view.until - view.since)) * 100).toFixed(2)}%`

/** Every row's inset: a lifted row's ground has padding inside it, so a plain row and the axis keep
 *  the same, and the tracks and the now line line up through both. */
const INSET = 'px-s'

function Axis({ view, lifted }: { view: DayView; lifted: boolean }) {
  const ticks: number[] = []
  const first = new Date(view.since)
  first.setMinutes(0, 0, 0)
  for (let t = first.getTime() + 3600_000; t < view.until; t += 3600_000) ticks.push(t)
  return (
    // Under a lifted row, "now" needs room below it, or it sits on that row's ground.
    <div aria-hidden="true" className={`grid grid-cols-[minmax(0,12rem)_1fr] gap-m max-sm:grid-cols-1 ${INSET} ${lifted ? 'mb-s' : ''}`}>
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
const isLate = (m: Mark, view: DayView) => (m.start - view.since) / (view.until - view.since) > LATE

/** How wide a scheduled run's name may be, so two names never draw over each other: up to its
 *  neighbour's mark, or half the gap when the two names face each other, and never back past now.
 *  The name is cut short there; the row's sentence says each name whole, and its line under the
 *  track the first two. */
export function nameRoom(marks: Mark[], i: number, view: DayView): string | undefined {
  const m = marks[i]
  if (m.kind !== 'scheduled') return undefined
  const runs = marks.filter((x) => x.kind === 'scheduled')
  const j = runs.indexOf(m)
  let span: number
  if (isLate(m, view)) {
    const prev = runs[j - 1]
    const from = prev ? (isLate(prev, view) ? prev.start : (prev.start + m.start) / 2) : view.since
    span = m.start - Math.max(from, view.now)
  } else {
    const next = runs[j + 1]
    span = (next ? (isLate(next, view) ? (m.start + next.start) / 2 : next.start) : view.until) - m.start
  }
  return `calc(${((span / (view.until - view.since)) * 100).toFixed(2)}% - 0.5rem)`
}

function markStyle(m: Mark, view: DayView): CSSProperties {
  const left = pct(m.start, view)
  if (m.kind === 'failed' || m.kind === 'scheduled') return { left }
  const width = `max(${(((m.end - m.start) / (view.until - view.since)) * 100).toFixed(2)}%, 4px)`
  return { left, width }
}

function NameCell({ row, strong = false }: { row: DayRow; strong?: boolean }) {
  // On a touch screen a linked name is a 44 px target, as the band's other controls are, and an
  // unlinked one takes the same box, so every row's state word sits at the same height. The link
  // fills a wrapper that cuts what overflows it, so its focus ring is drawn inside its own edge.
  const name = row.link
    ? <a href={row.link} className={`inline-flex max-w-full items-center hover:underline focus-visible:-outline-offset-2 max-lg:flex ${TOUCH_TARGET}`}><span className="truncate">{row.name}</span></a>
    : <span className="max-lg:flex max-lg:min-h-11 max-lg:items-center"><span className="truncate">{row.name}</span></span>
  return (
    <div className="flex min-w-0 flex-col">
      <span data-type="body-m" className="truncate text-on-surface" style={fvs(500)}>{name}</span>
      {row.key === 'scheduled'
        ? <span data-type="body-s" className="text-on-surface-var">Agent not named</span>
        : (
          <StateMark family={FAMILY[row.state]} className={strong ? 'text-on-surface' : 'text-on-surface-var'}>
            <span data-type="body-s" style={strong ? fvs(600) : undefined}>{STATE_WORD[row.state]}{row.asks > 1 ? ` · ${row.asks} asks` : ''}</span>
          </StateMark>
        )}
    </div>
  )
}

/** A row's track: its baseline, the latest work before the window, its marks and the now line.
 *  `ground` is what the row is painted on, so the label before the window hides the baseline
 *  behind it on a lifted row's tint as on the band's surface. */
function Track({ row, view, line = 'bg-outline-variant/35', ground = 'var(--color-surface-low)' }: {
  row: DayRow
  view: DayView
  line?: string
  ground?: string
}) {
  return (
    <div aria-hidden="true" className="relative h-7">
      <span className={`absolute inset-x-0 top-1/2 h-px ${line}`} />
      {row.before && <span data-type="body-s" className="absolute left-0 top-1/2 -translate-y-1/2 pr-xs text-on-surface-low" style={{ background: ground }}>&larr; {row.before}</span>}
      {row.marks.map((m, i) => <MarkView key={i} mark={m} view={view} room={nameRoom(row.marks, i, view)} />)}
      <span className="absolute -inset-y-1 left-1/2 w-px bg-on-surface-var" />
    </div>
  )
}

function Row({ row, view }: { row: DayRow; view: DayView }) {
  return (
    <li className={`grid min-w-0 grid-cols-[minmax(0,12rem)_1fr] items-center gap-m border-t border-outline-variant/30 py-s first:border-t-0 max-sm:grid-cols-1 ${INSET}`}
      aria-label={row.sentence}>
      <NameCell row={row} />
      <div className="relative min-w-0">
        <Track row={row} view={view} />
        <p data-type="body-s" className="flex min-w-0 overflow-hidden text-on-surface-var max-sm:flex-wrap">
          <span className="min-w-0 truncate max-sm:whitespace-normal">{row.say}</span>
          <FailedAt row={row} />
        </p>
      </div>
    </li>
  )
}

/** When a row that waits or works now failed today, after its line, in the age's style: the count
 *  says it failed, and this says where, whether or not its mark fell in the hours drawn. */
function FailedAt({ row }: { row: DayRow }) {
  if (!row.failed || row.say === row.failed) return null
  return <span className="shrink-0 whitespace-pre text-on-surface-var">{` · failed at ${row.failedAt}`}</span>
}

// ── What you act on: the rows that wait on you and the rows that failed ────────────────────────────

const isLifted = (r: DayRow) => r.key !== 'scheduled' && (r.state === 'waiting' || r.state === 'failed')

/** The count over the band: '2 asks wait on you · 1 failed today'. The asks are the ones Attention
 *  counts, those on no row too (a proposal, a note), so the two always agree; the failures are the
 *  rows that failed today, whatever they do now, as each one's sentence says. Whatever the filter
 *  shows, as the Show menu's counts are. '' when neither. */
function liftedHead(rows: DayRow[], view: DayView): string {
  const asks = view.waitingCount
  const failed = rows.filter((r) => r.key !== 'scheduled' && r.failed !== '').length
  return [
    asks ? `${asks} ${asks === 1 ? 'ask waits' : 'asks wait'} on you` : '',
    failed ? `${failed} failed today` : '',
  ].filter(Boolean).join(' · ')
}

/** A lifted row's ground, in its state's colour: 9% stays inside the ink-contrast budget `tokens.css`
 *  documents. The tone is the ground, the dot and the word; never a side-stripe. */
const GROUND: Record<'waiting' | 'failed', string> = {
  waiting: 'color-mix(in oklab, var(--color-state-waiting) 9%, var(--color-surface-low))',
  failed: 'color-mix(in oklab, var(--color-state-blocked) 9%, var(--color-surface-low))',
}

function LiftedRow({ row, view }: { row: DayRow; view: DayView }) {
  const tone = row.state === 'waiting' ? 'waiting' : 'failed'
  return (
    <li aria-label={row.sentence} data-lifted={tone}
      className={`mb-xs grid min-w-0 grid-cols-[minmax(0,12rem)_1fr] items-center gap-m rounded-md py-s max-sm:grid-cols-1 ${INSET}`}
      style={{ background: GROUND[tone] }}>
      <NameCell row={row} strong />
      <div className="relative min-w-0">
        <Track row={row} view={view} line="bg-outline-variant/50" ground={GROUND[tone]} />
        <div className="flex min-w-0 items-center justify-between gap-s max-sm:flex-col max-sm:items-start">
          {/* The ask's words give way before its age does: the age is what says how long it waited. */}
          <p data-type="body-s" className="flex min-w-0 overflow-hidden text-on-surface max-sm:flex-wrap">
            <span className="min-w-0 truncate max-sm:whitespace-normal">{row.say}</span>
            {row.asked && <span className="shrink-0 whitespace-pre text-on-surface-var">{` · asked ${row.asked} ago`}</span>}
            <FailedAt row={row} />
          </p>
          {row.attention && (
            // Each row's link opens its own ask, so each is named for it, its visible words first.
            <ButtonLink href={row.attention} variant="tonal" size="sm" className={TOUCH_TARGET}
              ariaLabel={`Answer in Attention: ${row.name}’s ${row.asks > 1 ? `first of ${row.asks} asks` : 'ask'}`}>
              Answer in Attention
            </ButtonLink>
          )}
        </div>
      </div>
    </li>
  )
}

function MarkView({ mark, view, room }: { mark: Mark; view: DayView; room?: string }) {
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
      const late = isLate(mark, view)
      return (
        <span data-late={late || undefined} style={{ ...style, maxWidth: room }}
          className={`${base} flex items-center gap-xs whitespace-nowrap ${late ? '-translate-x-full flex-row-reverse' : ''}`}>
          <span className={`size-3 shrink-0 rounded-pill border border-dashed border-on-surface-var ${late ? 'translate-x-1/2' : '-translate-x-1/2'}`} />
          <span data-type="body-s" className="min-w-0 truncate text-on-surface-var">{mark.label}</span>
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
