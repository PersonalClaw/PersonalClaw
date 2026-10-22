// ── The buddy's state machine (`docs/design/buddy/state-machine.md`, #3890) ────────────────────
//
// One pure reducer, `reduce(state, input, now)`, returning the next state and what the host is to
// do: start or cancel a named timer, re-read its levels, play a beat, or send an answer on the
// item's own route. No I/O, no React, no fetch: recorded inputs replay through it, and its tests
// feed it directly (`machine.*.test.ts`).
//
// v0.3.0 ships two layers with a fixed priority, truth and presence; play is later work (#3894).
// Truth says what the orb and its one line say; presence decides how much room it has and never
// changes the words. Priority, highest first: listening, the shown alert, the truth of the work in
// focus, then idle and sleep.
//
// The interruption matrix (`docs/design/buddy/interruption-matrix.json`) names the situations the
// buddy can be in and the events that reach it; `situationOf` and `eventOf` read them off a state
// and an input. A cell the matrix says cannot occur throws `CannotOccur` in development and tests,
// and is ignored in a production build, so a shell bug cannot freeze the buddy.

import { WORK_PHASE_LOOKS, type PhaseFamily, type WorkPhase } from '../workPhase'
import { dropped, queued } from './alertQueue'
import { inSight, keptFocus } from './focus'
import type { Alert, AnswerChoice, Beat, BuddyInput, Effect, Work } from './inputs'
import type { TimerName } from './timing'

/** How much room the orb has. */
export type Presence = 'hidden' | 'peek' | 'compact' | 'expanded' | 'floating' | 'sleeping'

/** The matrix's situations the v0.3.0 machine is ever in (play and file are later work). */
export const SITUATIONS = [
  'hidden', 'peek', 'compact', 'expanded', 'alert', 'finished', 'floating', 'sleeping', 'listening',
  'dragging', 'muted', 'offline',
] as const
export type Situation = (typeof SITUATIONS)[number]

/** The matrix's events. */
export const EVENTS = [
  'start', 'phase', 'other', 'alert', 'answered', 'elsewhere', 'done', 'failed', 'lost', 'back',
  'listen', 'unlisten', 'hover', 'click', 'hits', 'file', 'away', 'return', 'collapse', 'drag',
] as const
export type MatrixEvent = (typeof EVENTS)[number]

/** The shipping cells the matrix says cannot occur: the machine asserts each is never delivered.
 *  `machine.matrix.test.ts` holds this table to the JSON's. */
export const CANNOT_OCCUR: Readonly<Record<Situation, readonly MatrixEvent[]>> = {
  hidden: ['answered', 'unlisten', 'click', 'hits', 'collapse', 'drag'],
  peek: ['answered', 'unlisten', 'return'],
  compact: ['answered', 'unlisten', 'return'],
  expanded: ['answered', 'unlisten', 'return'],
  alert: ['unlisten'],
  finished: ['phase', 'answered', 'unlisten', 'return'],
  floating: ['answered', 'unlisten', 'return'],
  sleeping: ['phase', 'answered', 'unlisten'],
  listening: ['listen', 'away', 'return'],
  dragging: ['answered', 'hover', 'click', 'hits', 'away', 'return', 'collapse', 'drag'],
  muted: ['answered', 'click', 'hits', 'drag'],
  offline: ['start', 'phase', 'other', 'alert', 'answered', 'elsewhere', 'done', 'failed', 'lost'],
}

/** A matrix cell that cannot occur was delivered: a host or shell bug. */
export class CannotOccur extends Error {
  constructor(readonly situation: Situation, readonly event: MatrixEvent) {
    super(`the buddy cannot be told "${event}" while ${situation}: the interruption matrix says it never happens`)
  }
}

export type Connection = 'up' | 'lost' | 'offline' | 'restarting'

/** What happened while the owner was away, for the return summary. */
export interface AwaySummary { done: number; failed: number; started: number }

export interface BuddyState {
  /** Buddy status, the owner's setting: off, nothing shows. */
  enabled: boolean
  presence: Presence
  /** On the top edge, or undocked (floating, sleeping). */
  docked: boolean
  /** Floating: the hover card or the open card beside the orb. */
  card: 'hover' | 'open' | null
  /** Hidden by the owner, for an hour or until tomorrow. */
  muted: boolean
  away: boolean
  listening: boolean
  dragging: boolean
  quietHours: boolean
  connection: Connection
  lostAt: number | null
  /** "Connected again", until the next change of truth. */
  recovered: boolean
  /** Every piece of live work, in start order. */
  live: readonly Work[]
  focus: string | null
  /** Every alert waiting, in the waiting list's order, the shown one among them. */
  queue: readonly Alert[]
  shown: string | null
  /** What was under the shown alert. */
  beneath: Presence
  /** The pause after one alert leaves, before the next enters. */
  gap: boolean
  /** A broad-reach approval waiting on its confirmation. */
  confirming: string | null
  /** The line an alert leaves with, for one beat. */
  leaving: { kind: 'elsewhere' | 'expired'; work: string } | null
  /** The done beat and its hold: the work, how many finished in it, and when. */
  finished: { work: Work; count: number; at: number } | null
  /** The card opened on finished work, with Open. */
  opened: Work | null
  /** New work, shown only once `showDelay` has passed, so fast work does not flash. */
  appearing: boolean
  /** The open card's countdown line, before it folds. */
  countdown: boolean
  /** What happened while the owner was away; shown on return, then cleared. */
  summary: AwaySummary | null
  returning: boolean
  /** The timers the host is running for it. */
  running: readonly TimerName[]
}

/** The machine's first state: Buddy status off until the owner turns it on, docked and hidden. */
export const INITIAL: BuddyState = {
  enabled: false, presence: 'hidden', docked: true, card: null, muted: false, away: false,
  listening: false, dragging: false, quietHours: false, connection: 'up', lostAt: null,
  recovered: false, live: [], focus: null, queue: [], shown: null, beneath: 'hidden', gap: false,
  confirming: null, leaving: null, finished: null, opened: null, appearing: false, countdown: false,
  summary: null, returning: false, running: [],
}

const SHOWS_OFFLINE: readonly Presence[] = ['peek', 'compact', 'expanded', 'floating']

/** The matrix situation `s` is in. */
export function situationOf(s: BuddyState): Situation {
  if (s.dragging) return 'dragging'
  if (s.listening) return 'listening'
  if (s.muted) return 'muted'
  if (s.shown) return 'alert'
  if (s.connection === 'offline' && SHOWS_OFFLINE.includes(s.presence)) return 'offline'
  if (s.finished) return 'finished'
  return s.presence
}

const shownAlert = (s: BuddyState): Alert | null => s.queue.find((a) => a.id === s.shown) ?? null

/** The work in focus: the shown alert's while one shows, else the focused live work. */
export function focusedWork(s: BuddyState): Work | null {
  const id = shownAlert(s)?.workId ?? s.focus
  return s.live.find((w) => w.id === id) ?? null
}

/** The matrix event `input` is in `s`, or null for an input no cell names (a re-read, a pointer
 *  move, a timer that folds nothing). */
export function eventOf(s: BuddyState, input: BuddyInput): MatrixEvent | null {
  switch (input.type) {
    case 'start': return 'start'
    case 'phase': return input.id === (focusedWork(s)?.id ?? s.focus) ? 'phase' : 'other'
    case 'finish':
      if (input.how === 'error') return 'failed'
      // During the done beat's hold the focus is the finished work, and the next to finish counts.
      return input.id === s.focus || s.finished ? 'done' : 'other'
    case 'alert': return 'alert'
    case 'resolved': return 'elsewhere'
    case 'answer': return 'answered'
    case 'key': return input.key === 'ModEnter' && input.windowFocused && input.onAlert && s.shown ? 'answered' : null
    case 'connection': return input.state === 'lost' ? 'lost' : input.state === 'back' ? 'back' : null
    case 'listen': return 'listen'
    case 'unlisten': return 'unlisten'
    case 'pointer': return input.at === 'zone' && s.presence === 'hidden' ? 'hover' : null
    case 'click': return 'click'
    case 'hits': return 'hits'
    case 'away': return 'away'
    case 'return': return 'return'
    case 'drag': return input.phase === 'start' ? 'drag' : null
    case 'timer':
      if (input.name === 'hoverExpand') return 'hover'
      if (input.name === 'collapse' || input.name === 'peekLeave' || input.name === 'finishedHold') return 'collapse'
      return null
    default: return null
  }
}

/** Development builds and the tests throw on a cell that cannot occur; a production build skips it. */
const STRICT = import.meta.env?.DEV ?? true

export interface Step { state: BuddyState; effects: Effect[] }

const NO_SUMMARY: AwaySummary = { done: 0, failed: 0, started: 0 }
const nothingUnder = (s: BuddyState): Presence => (s.live.length && !s.away ? 'compact' : 'hidden')
const counted = (s: BuddyState, field: keyof AwaySummary): AwaySummary =>
  ({ ...(s.summary ?? NO_SUMMARY), [field]: (s.summary?.[field] ?? 0) + 1 })

/** The next state and what to do, for one input at `now` (ms). */
export function reduce(s: BuddyState, input: BuddyInput, now: number): Step {
  const situation = situationOf(s)
  const event = eventOf(s, input)
  if (event && CANNOT_OCCUR[situation].includes(event)) {
    if (STRICT) throw new CannotOccur(situation, event)
    return { state: s, effects: [{ kind: 'ignored', situation, event }] }
  }
  const effects: Effect[] = []
  const timer = (name: TimerName, start = true) => effects.push({ kind: 'timer', name, start })
  const beat = (b: Beat) => effects.push({ kind: 'beat', beat: b })
  const n: BuddyState = { ...s }

  /** The alert takes the orb: expanded on the top edge (a quiet peek in quiet hours), or the
   *  floating orb's bubble. */
  const showAlert = (alert: Alert) => {
    n.shown = alert.id
    // The next alert after a gap keeps what was under the first: the card the alerts opened is not.
    if (!s.gap) n.beneath = n.finished || n.presence === 'hidden' ? nothingUnder(n) : n.presence
    if (n.finished) timer('finishedHold', false)
    n.finished = null
    n.opened = null
    n.countdown = false
    n.recovered = false
    if (n.docked) n.presence = n.quietHours ? 'peek' : 'expanded'
    else {
      n.presence = 'floating'
      n.card = null
    }
    if (s.presence === 'sleeping') beat('wake')
    beat('alertEntrance')
  }
  /** The shown alert leaves: the next enters after `alertGap`, or what was under it returns. */
  const leave = (id: string) => {
    n.queue = dropped(n.queue, id)
    n.shown = null
    n.confirming = null
    if (n.queue.length) {
      n.gap = true
      timer('alertGap')
    } else n.presence = n.docked ? (n.beneath === 'hidden' ? nothingUnder(n) : n.beneath) : 'floating'
  }
  /** An explicit answer: the answer on the item's own route, its beat, then the alert leaves. A
   *  stale, disabled or unconfirmed broad-reach answer sends nothing. */
  const answer = (id: string, choice: AnswerChoice, confirmed: boolean) => {
    const alert = shownAlert(n)
    if (!alert || alert.id !== id || n.listening || alert.kind === 'error') return
    if (n.connection === 'lost' || n.connection === 'offline') return
    if (alert.broadReach && choice.kind === 'allow' && !confirmed) {
      n.confirming = alert.id
      return
    }
    effects.push({ kind: 'answer', id: alert.id, route: alert.route, choice })
    beat(choice.kind === 'deny' ? 'shake' : 'nod')
    leave(alert.id)
  }
  /** An alert arrives, or a failure raises one: shown, queued behind the shown one, or held. */
  const arrive = (alert: Alert) => {
    const had = n.queue.some((a) => a.id === alert.id)
    n.queue = queued(n.queue, alert)
    if (n.shown) {
      if (!had) beat('countPop')
      return
    }
    if (n.listening || n.muted || !n.enabled || n.gap) return
    if (n.dragging) {
      n.shown = alert.id
      n.beneath = nothingUnder(n)
      return
    }
    showAlert(alert)
  }
  /** The focused work finishes: the done beat and its hold, or, if it never showed, nothing. */
  const finishFocused = (work: Work) => {
    if (n.appearing) {
      n.appearing = false
      timer('showDelay', false)
      n.presence = nothingUnder(n)
      return
    }
    switch (situation) {
      case 'peek': case 'compact': case 'expanded':
        n.finished = { work, count: 1, at: now }
        n.opened = null
        n.countdown = false
        timer('finishedHold')
        beat('done')
        return
      case 'finished':
        n.finished = { work, count: (s.finished?.count ?? 0) + 1, at: now }
        timer('finishedHold')
        return
      case 'floating': beat('done'); return
      case 'hidden': case 'sleeping': n.summary = counted(n, 'done'); return
      default: return
    }
  }

  switch (input.type) {
    case 'start': {
      n.live = [...s.live.filter((w) => w.id !== input.work.id), input.work]
      n.focus = keptFocus(s.focus, n.live)
      n.recovered = false
      switch (situation) {
        case 'hidden':
          if (s.away) n.summary = counted(s, 'started')
          else if (s.enabled) {
            n.presence = 'compact'
            n.appearing = true
            timer('showDelay')
          }
          break
        case 'peek': n.presence = 'compact'; break
        case 'finished':
          n.finished = null
          timer('finishedHold', false)
          n.presence = 'compact'
          break
        case 'sleeping': n.presence = 'floating'; beat('wake'); break
        default: break
      }
      break
    }
    case 'phase': {
      const at = s.live.findIndex((w) => w.id === input.id)
      if (at < 0) break
      n.live = s.live.map((w, i) => (i === at ? { ...w, phase: input.phase, line: input.line } : w))
      n.recovered = false
      if (event === 'phase' && ['peek', 'compact', 'expanded', 'floating'].includes(situation)) beat('phase')
      break
    }
    case 'finish': {
      const work = s.live.find((w) => w.id === input.id)
      if (!work) break
      n.live = s.live.filter((w) => w.id !== input.id)
      n.focus = keptFocus(s.focus === input.id ? null : s.focus, n.live)
      n.recovered = false
      if (input.how === 'error') {
        if (s.away) n.summary = counted(s, 'failed')
        arrive({
          id: `error:${work.id}`, kind: 'error', workId: work.id, work: work.name, brief: input.reason ?? '',
          route: '', consequence: 0, raisedAt: now,
        })
      } else if (event === 'done') {
        finishFocused(work)
        // The finished work keeps the focus through its hold; it moves on as the hold ends.
        if (n.finished) n.focus = n.finished.work.id
      }
      break
    }
    case 'alert':
      n.recovered = false
      arrive(input.alert)
      break
    case 'resolved': {
      if (input.id !== s.shown) {
        n.queue = dropped(s.queue, input.id)
        break
      }
      const alert = shownAlert(s)!
      if (input.how !== 'answered') {
        n.leaving = { kind: input.how === 'expired' ? 'expired' : 'elsewhere', work: alert.work }
        beat('leave')
        timer('beat')
      }
      leave(alert.id)
      break
    }
    case 'answer':
      answer(input.id, input.choice, Boolean(input.confirmed))
      break
    case 'key':
      if (event === 'answered' && s.shown) answer(s.shown, { kind: 'allow' }, false)
      else if (input.key === 'Escape') {
        if (s.shown) n.confirming = null
        else if (situation === 'expanded') {
          n.presence = nothingUnder(s)
          n.opened = null
        }
      }
      break
    case 'connection':
      if (input.state === 'lost') {
        if (s.connection === 'lost' || s.connection === 'offline') break
        n.lostAt = now
        n.recovered = false
        if (situation === 'hidden' || situation === 'muted' || situation === 'sleeping') n.connection = 'offline'
        else {
          n.connection = 'lost'
          timer('offlineGrace')
        }
      } else if (input.state === 'restarting') {
        n.connection = 'restarting'
        timer('offlineGrace', false)
      } else {
        n.recovered = situation === 'offline'
        n.connection = 'up'
        n.lostAt = null
        timer('offlineGrace', false)
        effects.push({ kind: 'reread' })
        if (situation === 'offline' && s.queue.length && !s.shown) showAlert(s.queue[0])
      }
      break
    case 'listen':
      n.listening = true
      if (s.finished) {
        n.finished = null
        timer('finishedHold', false)
      }
      beat('listen')
      break
    case 'unlisten':
      n.listening = false
      if (!s.shown && s.queue.length && !s.muted) showAlert(s.queue[0])
      break
    case 'pointer':
      if (input.at === 'zone') {
        if (event === 'hover' && s.enabled && !s.muted && s.docked) n.presence = 'peek'
      } else if (input.at === 'orb') timer('hoverExpand')
      else {
        timer('hoverExpand', false)
        if (s.presence === 'peek') timer('peekLeave')
        if (s.card === 'hover') n.card = null
      }
      break
    case 'click': case 'hits':
      switch (situation) {
        case 'peek': case 'compact': case 'offline': n.presence = 'expanded'; break
        case 'finished':
          n.opened = input.type === 'click' ? s.finished!.work : null
          n.finished = null
          timer('finishedHold', false)
          n.presence = 'expanded'
          break
        case 'floating': if (input.type === 'click') n.card = 'open'; break
        case 'sleeping':
          if (input.type === 'click') {
            n.presence = 'floating'
            n.card = 'open'
            beat('wake')
          }
          break
        default: break
      }
      break
    case 'away':
      n.away = true
      n.summary = s.summary ?? { ...NO_SUMMARY }
      switch (situation) {
        case 'peek': case 'compact': case 'expanded': n.presence = 'hidden'; break
        case 'finished':
          n.summary = { ...n.summary, done: n.summary.done + (s.finished?.count ?? 0) }
          n.finished = null
          n.presence = 'hidden'
          break
        case 'floating': n.presence = 'sleeping'; n.card = null; break
        case 'offline': n.presence = s.docked ? 'hidden' : 'sleeping'; break
        default: break
      }
      break
    case 'return': {
      n.away = false
      const happened = Boolean(s.summary && (s.summary.done || s.summary.failed || s.summary.started)) || s.queue.length > 0
      if (situation === 'hidden' && happened && s.enabled) {
        n.presence = 'compact'
        n.returning = true
        timer('finishedHold')
      } else if (situation === 'sleeping' && happened) {
        n.presence = 'floating'
        n.returning = true
        beat('wake')
        timer('finishedHold')
      } else if (situation === 'offline' && s.presence === 'hidden') n.presence = 'compact'
      if (!n.returning) n.summary = null
      break
    }
    case 'drag':
      if (input.phase === 'start') {
        n.dragging = true
        n.card = null
        n.opened = null
        if (s.finished) timer('finishedHold', false)
        n.finished = null
        if (s.presence === 'sleeping') {
          n.presence = 'floating'
          beat('wake')
        }
      } else {
        n.dragging = false
        n.docked = input.docked ?? false
        if (!n.docked) n.presence = 'floating'
        else n.presence = n.shown ? (n.quietHours ? 'peek' : 'expanded') : nothingUnder(n)
        if (n.shown && !n.docked) beat('alertEntrance')
      }
      break
    case 'hide':
      n.muted = true
      break
    case 'show':
      n.muted = false
      n.presence = n.docked ? nothingUnder(n) : 'floating'
      if (!n.shown && n.queue.length && !n.listening) showAlert(n.queue[0])
      break
    case 'enabled':
      n.enabled = input.on
      if (input.on) {
        n.presence = n.docked ? nothingUnder(n) : 'floating'
        if (!n.shown && n.queue.length && !n.listening && !n.muted) showAlert(n.queue[0])
      } else {
        n.presence = 'hidden'
        n.shown = null
      }
      break
    case 'quietHours':
      n.quietHours = input.on
      break
    case 'timer':
      n.running = s.running.filter((t) => t !== input.name)
      switch (input.name) {
        case 'showDelay': n.appearing = false; break
        case 'hoverExpand':
          switch (situation) {
            case 'peek': case 'compact': case 'offline': n.presence = 'expanded'; break
            case 'finished':
              n.opened = s.finished!.work
              n.finished = null
              timer('finishedHold', false)
              n.presence = 'expanded'
              break
            case 'floating': case 'sleeping': if (!s.card) n.card = 'hover'; break
            default: break
          }
          break
        case 'peekLeave': if (s.presence === 'peek') n.presence = 'hidden'; break
        case 'collapse':
          if (situation === 'expanded') {
            n.countdown = true
            timer('collapseCountdown')
          } else if (situation === 'floating') n.card = null
          else if (situation === 'offline') n.presence = s.presence === 'expanded' ? 'compact' : 'hidden'
          break
        case 'collapseCountdown':
          if (situation === 'expanded') {
            n.countdown = false
            n.opened = null
            n.presence = nothingUnder(s)
          }
          break
        case 'finishedHold':
          if (s.finished) {
            n.finished = null
            n.presence = s.docked ? nothingUnder(n) : 'floating'
          }
          if (s.returning) {
            n.returning = false
            n.summary = null
          }
          break
        case 'offlineGrace':
          if (s.connection === 'lost') {
            n.connection = 'offline'
            n.finished = null
          }
          break
        case 'alertGap':
          n.gap = false
          if (!n.shown && n.queue.length && !n.listening && !n.muted) showAlert(n.queue[0])
          break
        case 'beat': n.leaving = null; break
        case 'sleep': if (situation === 'floating' && !s.live.length) n.presence = 'sleeping'; break
        default: break
      }
      break
    default:
      break
  }

  // Once no done beat holds it, the focus is the live work's (`focus.ts`).
  if (!n.finished) n.focus = keptFocus(n.focus, n.live)

  // An open card folds on `collapse`; a floating orb with nothing live sleeps after `sleep`.
  const folds = (x: BuddyState) => situationOf(x) === 'expanded' && !x.countdown
  if (folds(n) && !folds(s)) timer('collapse')
  if (!folds(n) && folds(s) && !n.countdown) timer('collapse', false)
  const dozes = (x: BuddyState) => situationOf(x) === 'floating' && !x.live.length && !x.card
  if (dozes(n) && !dozes(s)) timer('sleep')
  if (!dozes(n) && dozes(s)) timer('sleep', false)

  return settled(n, effects)
}

/** The step's timers kept true to what runs: a hidden buddy runs none (invariant 7), so its timers
 *  are cancelled as it hides and none starts while it stays hidden. */
function settled(n: BuddyState, effects: Effect[]): Step {
  const hidden = isHidden(n)
  const running = new Set(n.running)
  const out: Effect[] = []
  for (const e of effects) {
    if (e.kind !== 'timer') out.push(e)
    else if (e.start) {
      if (hidden) continue
      running.add(e.name)
      out.push(e)
    } else if (running.delete(e.name)) out.push(e)
  }
  if (hidden) {
    for (const name of running) out.push({ kind: 'timer', name, start: false })
    running.clear()
  }
  return { state: { ...n, running: [...running] }, effects: out }
}

/** Whether the buddy is hidden: off, hidden by the owner (but for the listening orb), or with
 *  nothing to show. New work waiting out `showDelay` is not hidden: it is about to show. */
export function isHidden(s: BuddyState): boolean {
  if (!s.enabled) return true
  if (s.listening || s.dragging || s.shown) return false
  return s.muted || s.presence === 'hidden'
}

/** Whether anything of the buddy is drawn. Hidden by the owner shows only the listening orb. */
export function shows(s: BuddyState): boolean {
  if (!s.enabled) return false
  if (s.listening) return true
  if (s.muted) return false
  if (s.dragging || s.shown) return true
  return s.presence !== 'hidden' && !s.appearing
}

// ── What it shows ────────────────────────────────────────────────────────────────────────────

/** What the line says, as data; `lines.ts` puts it in copy.md's words. */
export type Truth =
  | { kind: 'phase'; phase: WorkPhase; work: Work }
  | { kind: 'alert'; alert: Alert }
  | { kind: 'listening' }
  | { kind: 'done'; work: Work; count: number; at: number }
  | { kind: 'idle' }
  | { kind: 'restarting' }
  | { kind: 'offline'; since: number | null }
  | { kind: 'recovery' }
  | { kind: 'elsewhere' }
  | { kind: 'expired'; work: string }
  | { kind: 'returnSummary'; done: number; needYou: number }

export interface BuddyView {
  situation: Situation
  /** Whether anything is drawn; nothing else here is shown when it is false. */
  shows: boolean
  room: Presence | null
  family: PhaseFamily | null
  truth: Truth | null
  /** The live work in sight beside the focused one. */
  inSight: number
  /** The alerts waiting behind the shown one. */
  waiting: number
  /** Whether the shown alert's controls act, and why not. */
  actions: 'on' | 'listening' | 'offline' | 'confirm' | null
  offlineMark: boolean
  countdown: boolean
}

/** The truth the orb and its line show, highest priority first. */
export function truthOf(s: BuddyState): Truth {
  if (s.listening) return { kind: 'listening' }
  const alert = shownAlert(s)
  if (alert) return { kind: 'alert', alert }
  if (s.leaving) return s.leaving.kind === 'expired' ? { kind: 'expired', work: s.leaving.work } : { kind: 'elsewhere' }
  if (s.connection === 'offline') return { kind: 'offline', since: s.lostAt }
  if (s.connection === 'restarting') return { kind: 'restarting' }
  if (s.finished) return { kind: 'done', work: s.finished.work, count: s.finished.count, at: s.finished.at }
  if (s.recovered) return { kind: 'recovery' }
  if (s.returning && s.summary) return { kind: 'returnSummary', done: s.summary.done, needYou: s.queue.length }
  if (s.opened) return { kind: 'done', work: s.opened, count: 1, at: 0 }
  const work = focusedWork(s)
  if (work) return { kind: 'phase', phase: work.phase, work }
  return { kind: 'idle' }
}

/** The family a truth reads as (`state-machine.md`, "The four families"). */
export function familyOf(t: Truth, s: BuddyState): PhaseFamily {
  switch (t.kind) {
    case 'phase': return WORK_PHASE_LOOKS[t.phase].family
    case 'alert': return t.alert.kind === 'error' ? 'blocked' : 'waiting'
    case 'listening': case 'restarting': return 'active'
    case 'offline': return 'blocked'
    case 'recovery': {
      const work = focusedWork(s)
      return work ? WORK_PHASE_LOOKS[work.phase].family : 'quiet'
    }
    default: return 'quiet'
  }
}

/** What the buddy shows in `s`. */
export function viewOf(s: BuddyState): BuddyView {
  const drawn = shows(s)
  const truth = truthOf(s)
  const alert = shownAlert(s)
  return {
    situation: situationOf(s),
    shows: drawn,
    room: !drawn ? null : s.presence === 'hidden' ? 'compact' : s.presence,
    family: drawn ? familyOf(truth, s) : null,
    truth: drawn ? truth : null,
    inSight: inSight(s.live, focusedWork(s)?.id ?? s.focus).length,
    waiting: Math.max(0, s.queue.length - (alert ? 1 : 0)),
    actions: !alert ? null
      : s.listening ? 'listening'
        : s.connection === 'lost' || s.connection === 'offline' ? 'offline'
          : s.confirming === alert.id ? 'confirm' : 'on',
    // A short reconnect never flashes: the mark shows once `offlineGrace` has passed.
    offlineMark: s.connection === 'offline',
    countdown: s.countdown,
  }
}
