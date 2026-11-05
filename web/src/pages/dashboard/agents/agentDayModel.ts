import type { AgentDayBlock, AgentDayRead, Loop, SpawnedAgent, WeekOccurrence } from '../../../lib/api'
import type { NeedsYouRow } from '../../../lib/needsYou'
import type { AgentActivityEntity } from '../../../lib/useAgentActivity'

// ── Home's "Your agents today": the band's model ─────────────────────────────────────────────────
//
// A row per agent on a window around now: the last four hours and the next four (three each on a
// narrow screen). Pure, so every rule here is tested without a gateway:
//
// - what an agent DID is its model calls, folded by the gateway (`GET /api/agents/day`);
// - what it is DOING is the live feed (`useAgentActivity`);
// - what WAITS on you is Needs You, the read Attention counts, so the two agree at every moment.
//   An agent with an open ask is waiting on you, never working;
// - what FAILED today is its helpers' and loops' ends;
// - what COMES NEXT is the schedule. A run whose agent the schedule does not name sits on its own
//   "Scheduled" row, never guessed onto an agent.
//
// No time is written that no source gave: a loop with only a cycle budget runs "to cycle 8", open to
// the window's edge, and never "until about 21:00".

/** One run the schedule says is due (`GET /api/triggers/week`); `at` is epoch seconds. A run the
 *  schedule says will not fire carries why in `suppressed_by`. */
export type Occurrence = Pick<WeekOccurrence, 'trigger_id' | 'trigger_name' | 'at'> & { suppressed_by?: string }

export type RowState = 'waiting' | 'failed' | 'working' | 'idle'
export type MarkKind = 'done' | 'working' | 'waiting' | 'failed' | 'scheduled' | 'tail'
/** A mark on a row's track, in epoch ms. A failure and a scheduled run are a moment (`end === start`). */
export interface Mark { kind: MarkKind; start: number; end: number; label: string }

export interface DayRow {
  /** The agent's name, or `scheduled` for the runs the schedule names no agent for. */
  key: string
  name: string
  state: RowState
  marks: Mark[]
  /** What the row says beside the now line, in a few words. */
  say: string
  /** The Attention address that answers its ask, '' when it asks nothing. */
  attention: string
  asks: number
  /** What failed today and when ('Failed at 16:12: …'), '' when nothing did. A row that waits on
   *  you or works now can have failed earlier: its sentence says so, its mark shows it when it fell
   *  in the window, and its line says when (`failedAt`) whether or not it did. */
  failed: string
  /** When it failed today ('09:10'), '' when nothing did. */
  failedAt: string
  /** How long ago its first ask was asked ('2 min', '1 h 10 min'), '' when it asks nothing or the ask
   *  carries no time. Measured from the ask, never from its mark, which the window cuts. */
  asked: string
  /** The latest work before the window today ('HH:MM'), '' when none. */
  before: string
  /** The next run after the window ('21:30', 'tomorrow 07:30'), '' when none. */
  after: string
  /** Where its work opens, '' when nothing does. */
  link: string
  /** The row as one sentence: its text for a screen reader. */
  sentence: string
  latest: number
  /** Its first ask's place in Attention's order, so waiting rows read in the order Attention does. */
  rank: number
}

export interface DayView {
  now: number
  since: number
  until: number
  rows: DayRow[]
  /** Idle agents folded into one line, once there are more than `MAX_ROWS` rows. */
  idle: DayRow[]
  /** 'window' when nothing ran in it, 'today' when nothing ran today at all. */
  empty: '' | 'window' | 'today'
  /** What waits on you: exactly the rows Attention counts. */
  waitingCount: number
  /** Asks no agent's row holds (a skill proposal, a note, an ask of work nothing runs now). */
  unattributed: number
  /** Blocks the day read left out to keep to its cap, the oldest: the track says less than ran. */
  cut: number
}

export interface DayInput {
  now: number
  narrow: boolean
  /** The default agent's name (`GET /api/agents` `default_agent`): the ledger's unnamed agent. */
  defaultAgent: string
  day: AgentDayRead | null
  live: AgentActivityEntity[]
  asks: NeedsYouRow[]
  helpers: SpawnedAgent[]
  loops: Loop[]
  scheduled: Occurrence[]
}

export const MAX_ROWS = 8
const HOUR = 3600_000
const SCHEDULED = 'scheduled'
/** What the Scheduled row says of whose runs they are: the schedule names no agent for them. */
export const AGENT_NOT_NAMED = 'agent not named'
const STATE_WORD: Record<RowState, string> = { waiting: 'waiting on you', failed: 'failed', working: 'working', idle: 'idle' }
const ORDER: Record<RowState, number> = { waiting: 0, failed: 1, working: 2, idle: 3 }

/** A time as its clock reads it on this machine: '18:20'. */
export function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

function startOfDay(ms: number): number {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/** How long ago, as words: '2 min', '1 h 10 min', '3 h', '2 days'. */
export function ago(ms: number): string {
  const min = Math.max(1, Math.round(ms / 60_000))
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  if (h < 24) return min % 60 ? `${h} h ${min % 60} min` : `${h} h`
  const days = Math.floor(h / 24)
  return days === 1 ? '1 day' : `${days} days`
}

/** The runs in the window, each trigger once at its first run, in that order, with its count and its
 *  last run when it runs more than once: '18:25 Check mail, 48 runs to 22:20 · 19:00 Water the plants'. */
function byTrigger(runs: { o: Occurrence; at: number }[], now: number): string {
  const seen = new Map<string, { name: string; first: number; last: number; count: number }>()
  for (const { o, at } of runs) {
    const t = seen.get(o.trigger_id)
    if (t) { t.count += 1; t.last = Math.max(t.last, at); t.first = Math.min(t.first, at) } else {
      seen.set(o.trigger_id, { name: o.trigger_name, first: at, last: at, count: 1 })
    }
  }
  return [...seen.values()].sort((a, b) => a.first - b.first)
    .map((t) => `${whenNext(t.first, now)} ${t.name}${t.count > 1 ? `, ${t.count} runs to ${whenNext(t.last, now)}` : ''}`)
    .join(' · ')
}

/** When a run after the window is, as words: '23:15', 'tomorrow 07:30', 'Fri 09:00'. */
function whenNext(ms: number, now: number): string {
  const days = Math.round((startOfDay(ms) - startOfDay(now)) / (24 * HOUR))
  if (days <= 0) return clock(ms)
  if (days === 1) return `tomorrow ${clock(ms)}`
  return `${new Date(ms).toLocaleDateString([], { weekday: 'short' })} ${clock(ms)}`
}

function ms(ts: string | null | undefined): number | null {
  if (!ts) return null
  const t = Date.parse(ts)
  return Number.isFinite(t) ? t : null
}

function firstLine(s: string | null | undefined): string {
  return (s ?? '').split('\n')[0].trim()
}

/** Where a block of work opens: a chat's own page, '' for kinds a block cannot name a page for. */
function blockLink(b: AgentDayBlock): string {
  if (b.source !== 'chat') return ''
  const key = b.session.startsWith('dashboard:') ? b.session.slice('dashboard:'.length) : b.session
  return key ? `#/chat/${encodeURIComponent(key)}` : ''
}

interface Draft {
  key: string
  marks: Mark[]
  asks: NeedsYouRow[]
  working: AgentActivityEntity | null
  failure: { at: number; why: string } | null
  before: number | null
  link: string
  latest: number
  tail: string
  rank: number
}

export function buildDay(input: DayInput): DayView {
  const { now, defaultAgent } = input
  const half = (input.narrow ? 3 : 4) * HOUR
  const since = now - half
  const until = now + half
  const today = startOfDay(now)
  const named = (agent: string | undefined) => (agent ? agent : defaultAgent || 'Default agent')

  const drafts = new Map<string, Draft>()
  const draft = (key: string): Draft => {
    let d = drafts.get(key)
    if (!d) {
      d = { key, marks: [], asks: [], working: null, failure: null, before: null, link: '', latest: 0, tail: '', rank: Infinity }
      drafts.set(key, d)
    }
    return d
  }
  const touch = (d: Draft, t: number) => { d.latest = Math.max(d.latest, t) }

  // What it did: the day read's blocks, and the latest work before the window.
  for (const a of input.day?.agents ?? []) {
    const d = draft(named(a.agent))
    for (const b of a.blocks) {
      const s = ms(b.start), e = ms(b.end)
      if (s === null || e === null || e < since || s > now) continue
      d.marks.push({ kind: 'done', start: Math.max(s, since), end: Math.min(e, now), label: '' })
      touch(d, e)
      d.link = d.link || blockLink(b)
    }
    const before = ms(a.before)
    if (before !== null && before >= today && before < since) {
      d.before = Math.max(d.before ?? 0, before)
      touch(d, before)
    }
  }

  // What it is doing: the live feed. A working agent's work runs from when it began to now.
  const byWork = new Map<string, AgentActivityEntity>()
  const bySession = new Map<string, AgentActivityEntity>()
  for (const e of input.live) {
    if (e.work) byWork.set(e.work, e)
    if (e.refs.session) bySession.set(e.refs.session, e)
    if (e.state !== 'working' && e.state !== 'blocked') continue
    const d = draft(named(e.agent))
    d.working = d.working ?? e
    d.link = e.refs.link || d.link
    const began = ms(e.since)
    d.marks.push({ kind: 'working', start: Math.max(began ?? now, since), end: now, label: '' })
    touch(d, now)
    // A running loop with a cycle budget runs on past now, to a cycle, never to a guessed time.
    if (e.kind === 'loop' && e.work) {
      const l = input.loops.find((x) => `loop:${x.id}` === e.work)
      if (l && l.max_cycles > 0) {
        d.tail = `Runs to cycle ${l.max_cycles}`
        d.marks.push({ kind: 'tail', start: now, end: until, label: d.tail })
      }
    }
  }

  // What waits on you: Needs You's rows, each on the row of the agent whose work asks it.
  let unattributed = 0
  for (const [index, row] of input.asks.entries()) {
    const owner = row.origin === 'waiting' ? (byWork.get(row.item.work) ?? (row.item.session ? bySession.get(row.item.session) : undefined)) : undefined
    if (!owner) { unattributed += 1; continue }
    const d = draft(named(owner.agent))
    d.asks.push(row)
    d.rank = Math.min(d.rank, index)
    const asked = row.origin === 'waiting' ? ms(row.item.asked_at) : null
    d.marks.push({ kind: 'waiting', start: Math.max(asked ?? now, since), end: now, label: '' })
    d.link = owner.refs.link || d.link
    touch(d, now)
  }

  // What failed today: a helper's end, a loop's end.
  const noteFailure = (key: string, at: number, why: string) => {
    if (at < today || at > now) return
    const d = draft(key)
    if (!d.failure || at > d.failure.at) d.failure = { at, why }
    touch(d, at)
  }
  for (const h of input.helpers) {
    if (h.done && h.error && typeof h.ended === 'number') noteFailure(named(h.agent), h.ended * 1000, firstLine(h.error))
  }
  for (const l of input.loops) {
    if (l.status === 'failed' && typeof l.completed_at === 'number') {
      noteFailure(named(l.agent), l.completed_at * 1000, firstLine(l.error_message) || 'The loop failed')
    }
  }
  for (const d of drafts.values()) {
    if (d.failure && d.failure.at >= since) d.marks.push({ kind: 'failed', start: d.failure.at, end: d.failure.at, label: '' })
  }

  // What comes next: the schedule, on a row of its own.
  const due = input.scheduled
    .filter((o) => !o.suppressed_by)
    .map((o) => ({ o, at: o.at * 1000 }))
    .filter(({ at }) => at > now)
    .sort((a, b) => a.at - b.at)
  const inWindow = due.filter(({ at }) => at <= until)
  const nextAfter = due.find(({ at }) => at > until)
  if (inWindow.length > 0 || (nextAfter && drafts.size > 0)) {
    const d = draft(SCHEDULED)
    for (const { o, at } of inWindow) d.marks.push({ kind: 'scheduled', start: at, end: at, label: o.trigger_name })
  }

  // The rows.
  const rows: DayRow[] = []
  for (const d of drafts.values()) {
    const scheduled = d.key === SCHEDULED
    const state: RowState = d.asks.length > 0 ? 'waiting' : d.working ? 'working' : d.failure ? 'failed' : 'idle'
    const after = scheduled && nextAfter ? whenNext(nextAfter.at, now) : ''
    const before = d.before !== null ? clock(d.before) : ''
    const ask = d.asks[0]
    const failed = d.failure ? `Failed at ${clock(d.failure.at)}: ${d.failure.why}` : ''
    const run = ({ o, at }: { o: Occurrence; at: number }) => `${whenNext(at, now)} ${o.trigger_name}`
    let say: string
    let saysBefore = false
    if (scheduled) {
      // The row names its next runs in order, so the track's marks can stay short and nothing sits
      // at the track's edge: the first two in the window and how many more, or, when two or fewer
      // fall in it, the one after the window too, which is then the next.
      say = inWindow.length > 2
        ? `${inWindow.slice(0, 2).map(run).join(' · ')} and ${inWindow.length - 2} more`
        : [...inWindow, ...(nextAfter ? [nextAfter] : [])].map(run).join(' · ')
    } else if (ask) {
      say = d.asks.length > 1 ? `${ask.title} and ${d.asks.length - 1} more` : ask.title
    } else if (d.working) {
      say = d.working.title
    } else if (failed) {
      say = failed
    } else {
      const last = d.marks.filter((m) => m.kind === 'done').reduce((t, m) => Math.max(t, m.end), d.before ?? 0)
      say = last ? `Last ran ${clock(last)}` : ''
      saysBefore = d.before !== null && last === d.before
    }
    const name = scheduled ? 'Scheduled' : d.key
    // Spoken, the Scheduled row names every trigger in the window once, in the order of its first
    // run, with how many runs and the last when it repeats: the track that draws them is hidden from
    // a screen reader, and a trigger every 5 minutes is 48 runs in four hours.
    const spoken = scheduled && inWindow.length > 2 ? byTrigger(inWindow, now) : say
    const askedAt = ask?.origin === 'waiting' ? ms(ask.item.asked_at) : null
    const asked = askedAt !== null ? ago(now - askedAt) : ''
    const parts = [`${name}, ${scheduled ? AGENT_NOT_NAMED : STATE_WORD[state]}: ${spoken || 'nothing to say'}.`]
    if (asked) parts.push(`Asked ${asked} ago.`)
    if (failed && say !== failed) parts.push(`${failed}.`)
    if (d.tail) parts.push(`${d.tail}.`)
    if (before && !saysBefore) parts.push(`Earlier today ${before}.`)
    // The run after the window is in the line when two or fewer fall in it, and said after them when
    // more do, unless it only goes on with a trigger already named there with its runs and its last.
    const goesOn = nextAfter !== undefined
      && inWindow.filter(({ o }) => o.trigger_id === nextAfter.o.trigger_id).length > 1
    if (nextAfter && after && inWindow.length > 2 && !goesOn) parts.push(`Then ${run(nextAfter)}.`)
    rows.push({
      key: d.key,
      name,
      state: scheduled ? 'idle' : state,
      marks: d.marks.sort((a, b) => a.start - b.start),
      say,
      attention: ask ? `#/dashboard?view=attention&open=${encodeURIComponent(ask.key)}` : '',
      asks: d.asks.length,
      failed,
      failedAt: d.failure ? clock(d.failure.at) : '',
      asked,
      before,
      after,
      link: d.link,
      sentence: parts.join(' '),
      latest: d.latest,
      rank: d.rank,
    })
  }
  rows.sort((a, b) =>
    Number(a.key === SCHEDULED) - Number(b.key === SCHEDULED)
    || ORDER[a.state] - ORDER[b.state] || (a.rank === b.rank ? 0 : a.rank < b.rank ? -1 : 1)
    || b.latest - a.latest || a.name.localeCompare(b.name))

  const ranInWindow = rows.some((r) => r.marks.some((m) => m.kind !== 'scheduled' && m.kind !== 'tail'))
  // A failure today is something that ran today, so the band never says nothing did beside it.
  const ranToday = ranInWindow || rows.some((r) => r.before !== '' || r.failed !== '')
  const folded = rows.length > MAX_ROWS
  return {
    now,
    since,
    until,
    rows: folded ? rows.filter((r) => r.key === SCHEDULED || r.state !== 'idle') : rows,
    idle: folded ? rows.filter((r) => r.key !== SCHEDULED && r.state === 'idle') : [],
    empty: ranInWindow ? '' : ranToday ? 'window' : 'today',
    waitingCount: input.asks.length,
    unattributed,
    cut: input.day?.cut ?? 0,
  }
}
