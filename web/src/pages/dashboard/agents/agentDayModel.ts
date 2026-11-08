import type { AgentDayBlock, AgentDayRead, Loop, SpawnedAgent, WeekOccurrence } from '../../../lib/api'
import type { NeedsYouRow } from '../../../lib/needsYou'
import type { AgentActivityEntity } from '../../../lib/useAgentActivity'
import { previewLine } from '../../../lib/previewText'

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
/** A mark on a row's track, in epoch ms. A failure and a scheduled run are a moment (`end === start`);
 *  a schedule whose runs come too close to name each one is a strip, from its first run to its last. */
export interface Mark { kind: MarkKind; start: number; end: number; label: string }

export interface DayRow {
  /** The agent's name, or `scheduled` for the runs the schedule names no agent for. */
  key: string
  name: string
  state: RowState
  marks: Mark[]
  /** What the row says beside the now line, in a few words, as written. */
  say: string
  /** `say` in its parts: our words (`lead`, `tail`) around what an agent wrote (`content`), which is
   *  the one part that is markdown. An ask's title with " and 2 more", the title of the work it does
   *  now, or why it failed after "Failed at 16:12: ". A line the app builds (the Scheduled row's,
   *  "Last ran 17:40") is all `lead`, and so is an approval's title, a tool's name or the command
   *  itself, which is drawn as text. */
  line: RowLine
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

/** A row's line: our words around the one part an agent wrote. */
export interface RowLine { lead: string; content: string; tail: string }

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
  /** The schedules the week read cut short at its cap (`truncated`): past the last run it sent,
   *  each has more it did not send. */
  scheduledCut?: string[]
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

/** Runs of one schedule closer together than this fold into one strip on the track, from its first
 *  run to its last. Measured on the band's narrowest tracks: 288 px for six hours on a 360 px
 *  phone (48 px an hour), and 424 px for eight at 700 px, where the window widens (53 px an hour).
 *  A run's name gets at most its gap less 24 px (nameRoom's 0.5rem, the 12 px ring and the 4 px
 *  beside it), so an hour leaves about 24 px, three letters and the ellipsis ("Sel…"), and 45
 *  minutes leaves 12 px, the ellipsis alone. The model does not know the track's width, so it folds
 *  for the narrowest one. Every run of an hourly schedule keeps its mark; two names that face each
 *  other share their gap (nameRoom), so on the narrowest tracks (a phone, or a 700 px window) those
 *  two can show their rings alone. */
export const FOLD_MS = 60 * 60_000

/** One schedule's runs in the window, in order. `open` when the week read stopped inside the window
 *  (`scheduledCut`) on a run that fires: its runs go on past the last one the read sent. */
interface Schedule { name: string; runs: number[]; open: boolean }

/** How often a schedule runs, from its own runs: '1 min', '1 min 30 s', '30 s', or '' when their
 *  gaps differ. */
function cadence(runs: number[]): string {
  if (runs.length < 2) return ''
  const gap = runs[1] - runs[0]
  if (runs.some((t, i) => i > 0 && Math.abs(t - runs[i - 1] - gap) >= 1000)) return ''
  const secs = Math.round(gap / 1000)
  if (secs < 60) return `${secs} s`
  return secs % 60 === 0 ? `${secs / 60} min` : `${Math.floor(secs / 60)} min ${secs % 60} s`
}

/** A schedule's runs as the track draws them. A run with room for its name keeps its mark; runs
 *  closer together than `FOLD_MS` are one strip with one name and how often it runs, which goes on
 *  to the window's edge when the read stopped inside it. */
function scheduleMarks(s: Schedule, until: number): Mark[] {
  const marks: Mark[] = []
  let from = 0
  for (let i = 1; i <= s.runs.length; i++) {
    if (i < s.runs.length && s.runs[i] - s.runs[i - 1] < FOLD_MS) continue
    const runs = s.runs.slice(from, i)
    if (runs.length === 1) {
      marks.push({ kind: 'scheduled', start: runs[0], end: runs[0], label: s.name })
    } else {
      const every = cadence(runs)
      const end = i === s.runs.length && s.open ? until : runs[runs.length - 1]
      marks.push({ kind: 'scheduled', start: runs[0], end, label: every ? `${s.name} · every ${every}` : s.name })
    }
    from = i
  }
  return marks
}

/** A schedule in words, at its first run in the window: once, '19:00 Water the plants'; drawn as one
 *  strip, how often to its last, '18:25 Check mail, every 5 min to 22:20', or with no last when the
 *  read stopped inside the window, '17:24 Heartbeat tasks, every 1 min'; otherwise how many runs to
 *  its last, '18:23 Self-remediation, 3 runs to 20:23'. */
function scheduleWords(s: Schedule, now: number): string {
  const head = `${whenNext(s.runs[0], now)} ${s.name}`
  if (s.runs.length === 1) return head
  const last = whenNext(s.runs[s.runs.length - 1], now)
  const every = cadence(s.runs)
  if (every && s.runs.every((t, i) => i === 0 || t - s.runs[i - 1] < FOLD_MS)) {
    return s.open ? `${head}, every ${every}` : `${head}, every ${every} to ${last}`
  }
  return `${head}, ${s.runs.length} runs to ${last}${s.open ? ' and more' : ''}`
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
  const cut = new Set(input.scheduledCut ?? [])
  // Where the read stopped for each schedule it cut: the last run it sent, a quiet or skipped one
  // too. Past that run the schedule goes on, so it runs on to the window's edge only when that run
  // is in the window and fires: past a quiet one, when it fires again is not known.
  const readEnd = new Map<string, { at: number; fires: boolean }>()
  for (const o of input.scheduled) {
    if (!cut.has(o.trigger_id)) continue
    const r = readEnd.get(o.trigger_id)
    if (!r || o.at * 1000 > r.at) readEnd.set(o.trigger_id, { at: o.at * 1000, fires: !o.suppressed_by })
  }
  const stopsInWindow = (id: string) => { const r = readEnd.get(id); return r !== undefined && r.at <= until && r.fires }
  // Each schedule once, in the order of its first run in the window.
  const schedules = new Map<string, Schedule>()
  for (const { o, at } of inWindow) {
    const s = schedules.get(o.trigger_id)
    if (s) s.runs.push(at)
    else schedules.set(o.trigger_id, { name: o.trigger_name, runs: [at], open: stopsInWindow(o.trigger_id) })
  }
  // The first run after the window is known only up to where the read stopped for every schedule
  // it cut: past that, a run it did not send can come first.
  const knownTo = Math.min(Infinity, ...[...readEnd.values()].map((r) => r.at))
  const first = due.find(({ at }) => at > until)
  const nextAfter = first && first.at <= knownTo ? first : undefined
  if (inWindow.length > 0 || (nextAfter && drafts.size > 0)) {
    const d = draft(SCHEDULED)
    for (const s of schedules.values()) d.marks.push(...scheduleMarks(s, until))
  }

  // The rows.
  const rows: DayRow[] = []
  for (const d of drafts.values()) {
    const scheduled = d.key === SCHEDULED
    const state: RowState = d.asks.length > 0 ? 'waiting' : d.working ? 'working' : d.failure ? 'failed' : 'idle'
    const after = scheduled && nextAfter ? whenNext(nextAfter.at, now) : ''
    const before = d.before !== null ? clock(d.before) : ''
    const ask = d.asks[0]
    const failedLead = d.failure ? `Failed at ${clock(d.failure.at)}: ` : ''
    const failed = d.failure ? `${failedLead}${d.failure.why}` : ''
    const run = ({ o, at }: { o: Occurrence; at: number }) => `${whenNext(at, now)} ${o.trigger_name}`
    let line: RowLine
    let saysBefore = false
    if (scheduled) {
      // The row names its next runs in order, so the track's marks can stay short and nothing sits
      // at the track's edge. Past two in the window, it names each schedule once, with how often
      // or how many runs and its last (`scheduleWords`), in its line and its sentence alike: a
      // schedule every minute is 240 runs in four hours. Two or fewer, it names them and the one
      // after the window too, which is then the next.
      line = { lead: inWindow.length > 2
        ? [...schedules.values()].map((s) => scheduleWords(s, now)).join(' · ')
        : [...inWindow, ...(nextAfter ? [nextAfter] : [])].map(run).join(' · '), content: '', tail: '' }
    } else if (ask) {
      // An approval's title is a tool's name or the command itself (`Running: …`): what will run, as
      // written. Every other ask's title is an agent's line.
      const more = d.asks.length > 1 ? ` and ${d.asks.length - 1} more` : ''
      line = ask.origin === 'waiting' && ask.item.kind === 'approval'
        ? { lead: ask.title, content: '', tail: more }
        : { lead: '', content: ask.title, tail: more }
    } else if (d.working) {
      line = { lead: '', content: d.working.title, tail: '' }
    } else if (d.failure) {
      line = { lead: failedLead, content: d.failure.why, tail: '' }
    } else {
      const last = d.marks.filter((m) => m.kind === 'done').reduce((t, m) => Math.max(t, m.end), d.before ?? 0)
      line = { lead: last ? `Last ran ${clock(last)}` : '', content: '', tail: '' }
      saysBefore = d.before !== null && last === d.before
    }
    const say = `${line.lead}${line.content}${line.tail}`
    // The sentence reads what an agent wrote as plain text, and our own words as they are.
    const plainSay = `${line.lead}${previewLine(line.content)}${line.tail}`
    const name = scheduled ? 'Scheduled' : d.key
    const askedAt = ask?.origin === 'waiting' ? ms(ask.item.asked_at) : null
    const asked = askedAt !== null ? ago(now - askedAt) : ''
    const parts = [`${name}, ${scheduled ? AGENT_NOT_NAMED : STATE_WORD[state]}: ${plainSay || 'nothing to say'}.`]
    if (asked) parts.push(`Asked ${asked} ago.`)
    if (failed && say !== failed) parts.push(`${failedLead}${previewLine(d.failure?.why ?? '')}.`)
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
      line,
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
