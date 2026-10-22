// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { readSource } from '../../test/sourceTree'
import {
  A1, A2, SITUATION_STATES, T, W1, W2, W3, at, run, type Reducer,
} from '../../test/buddyMachine'
import type { Beat, BuddyInput, Effect } from './inputs'
import {
  CANNOT_OCCUR, CannotOccur, EVENTS, SITUATIONS, reduce, viewOf,
  type BuddyState, type BuddyView, type MatrixEvent, type Presence, type Situation, type Truth,
} from './machine'
import type { TimerName } from './timing'

// ── Every shipping cell of the interruption matrix has a test (invariant 15) ───────────────────
//
// `docs/design/buddy/interruption-matrix.json` crosses the buddy's situations with the events that
// reach it. Every cell outside its `later` list (play, the drop and quick ask: #3894, #3895) has
// an entry below, keyed `situation/event`: the situation it starts from, the event as an input,
// and what its outcome says, as checks on what the buddy shows, holds and asks its host to do. The
// key check reds when the JSON gains a cell this table does not have. A cell the matrix says cannot
// occur is asserted to throw `CannotOccur`.
//
// The control: run against a reducer that returns its state unchanged, every cell whose outcome
// changes presence, truth or effects fails, so no cell's test passes by asserting nothing. The
// cells marked `same` are the matrix's own "no change" outcomes, checked to change nothing seen.

interface Matrix {
  situations: Record<string, string>
  events: Record<string, string>
  cells: Record<string, Record<string, string>>
  later: [string, string][]
}

const matrix: Matrix = JSON.parse(readSource(join(process.cwd(), '..', 'docs', 'design', 'buddy', 'interruption-matrix.json')))
const later = new Set(matrix.later.map(([s, e]) => `${s}/${e}`))
const shipping = Object.entries(matrix.cells).flatMap(([s, row]) => Object.keys(row).map((e) => `${s}/${e}`)).filter((k) => !later.has(k))
const cannotInJson = shipping.filter((k) => {
  const [s, e] = k.split('/')
  return matrix.cells[s][e].startsWith('cannot occur')
})

interface Ran {
  before: BuddyState
  beforeView: BuddyView
  after: BuddyState
  view: BuddyView
  effects: Effect[]
}

type Check = (r: Ran) => string | null

interface Cell {
  /** The state the cell starts from, when not its situation's own. */
  given?: () => BuddyState
  /** The event as an input, when not the event's own. */
  input?: (s: BuddyState) => BuddyInput
  /** Timers that then run out, in order, before the `after` checks. */
  then?: TimerName[]
  /** Checks on the step the event makes. */
  expect?: Check[]
  /** Checks once the `then` timers have run out, on every effect asked for on the way. */
  after?: Check[]
  /** The matrix says nothing seen changes. */
  same?: true
  cannot?: true
}

// ── The checks ─────────────────────────────────────────────────────────────────────────────────

const is = (what: string, ok: boolean, got: unknown): string | null => (ok ? null : `${what}, got ${JSON.stringify(got)}`)
const sit = (x: Situation): Check => (r) => is(`situation ${x}`, r.view.situation === x, r.view.situation)
const room = (x: Presence | null): Check => (r) => is(`room ${x}`, r.view.room === x, r.view.room)
const shows = (on: boolean): Check => (r) => is(on ? 'drawn' : 'nothing drawn', r.view.shows === on, r.view.shows)
const truth = (kind: Truth['kind']): Check => (r) => is(`truth ${kind}`, r.view.truth?.kind === kind, r.view.truth?.kind)
const family = (f: BuddyView['family']): Check => (r) => is(`family ${f}`, r.view.family === f, r.view.family)
const phaseShown = (id: string, phase: string): Check => (r) => {
  const t = r.view.truth
  return is(`${id} shown ${phase}`, t?.kind === 'phase' && t.work.id === id && t.phase === phase, t)
}
const alertShown = (id: string): Check => (r) => {
  const t = r.view.truth
  return is(`alert ${id} shown`, t?.kind === 'alert' && t.alert.id === id, t)
}
const starts = (name: TimerName): Check => (r) =>
  is(`starts ${name}`, r.effects.some((e) => e.kind === 'timer' && e.name === name && e.start), r.effects)
const cancels = (name: TimerName): Check => (r) =>
  is(`cancels ${name}`, r.effects.some((e) => e.kind === 'timer' && e.name === name && !e.start), r.effects)
const noTimerStarts: Check = (r) => is('no timer started', !r.effects.some((e) => e.kind === 'timer' && e.start), r.effects)
const beats = (b: Beat): Check => (r) => is(`plays ${b}`, r.effects.some((e) => e.kind === 'beat' && e.beat === b), r.effects)
const noBeat = (b: Beat): Check => (r) => is(`no ${b}`, !r.effects.some((e) => e.kind === 'beat' && e.beat === b), r.effects)
const rereads: Check = (r) => is('re-reads', r.effects.some((e) => e.kind === 'reread'), r.effects)
const answers = (route: string): Check => (r) =>
  is(`answers on ${route}`, r.effects.some((e) => e.kind === 'answer' && e.route === route), r.effects)
const noAnswer: Check = (r) => is('no answer', !r.effects.some((e) => e.kind === 'answer'), r.effects)
const levelIs = (id: string, phase: string): Check => (r) =>
  is(`${id} at ${phase}`, r.after.live.find((w) => w.id === id)?.phase === phase, r.after.live)
const ended = (id: string): Check => (r) => is(`${id} no longer live`, !r.after.live.some((w) => w.id === id), r.after.live)
const liveCount = (n: number): Check => (r) => is(`${n} live`, r.after.live.length === n, r.after.live.length)
const inSight = (n: number): Check => (r) => is(`${n} in sight`, r.view.inSight === n, r.view.inSight)
const waiting = (n: number): Check => (r) => is(`${n} waiting`, r.view.waiting === n, r.view.waiting)
const queued = (id: string): Check => (r) => is(`${id} queued`, r.after.queue.some((a) => a.id === id), r.after.queue)
const shownId = (id: string | null): Check => (r) => is(`shown ${id}`, r.after.shown === id, r.after.shown)
const actions = (a: BuddyView['actions']): Check => (r) => is(`actions ${a}`, r.view.actions === a, r.view.actions)
const offlineMark: Check = (r) => is('the offline mark', r.view.offlineMark, r.view.offlineMark)
const field = <K extends keyof BuddyState>(k: K, v: BuddyState[K]): Check => (r) =>
  is(`${String(k)} ${JSON.stringify(v)}`, JSON.stringify(r.after[k]) === JSON.stringify(v), r.after[k])
const focus = (id: string): Check => (r) => is(`focus ${id}`, r.after.focus === id, r.after.focus)
const card = (c: BuddyState['card']): Check => (r) => is(`card ${c}`, r.after.card === c, r.after.card)
const summaryDone = (n: number): Check => (r) => is(`${n} done while away`, r.after.summary?.done === n, r.after.summary)

// ── The events as inputs, and the states the cells need beyond their situation's own ───────────

const other = (s: BuddyState) => s.live.find((w) => w.id !== s.focus)?.id ?? 'w9'
const EVENT_INPUTS: Readonly<Record<MatrixEvent, (s: BuddyState) => BuddyInput>> = {
  start: () => ({ type: 'start', work: W3 }),
  phase: (s) => ({ type: 'phase', id: s.queue.find((a) => a.id === s.shown)?.workId ?? s.focus ?? 'w1', phase: 'searching', line: 'the web' }),
  other: (s) => ({ type: 'phase', id: other(s), phase: 'planning', line: '' }),
  alert: () => ({ type: 'alert', alert: A2 }),
  answered: (s) => ({ type: 'answer', id: s.shown ?? 'a1', choice: { kind: 'allow' } }),
  elsewhere: (s) => ({ type: 'resolved', id: s.shown ?? 'a9', how: 'elsewhere' }),
  done: (s) => ({ type: 'finish', id: s.finished ? (s.live[0]?.id ?? 'w9') : (s.focus ?? 'w1'), how: 'done' }),
  failed: (s) => ({ type: 'finish', id: other(s) === 'w9' ? (s.focus ?? 'w1') : other(s), how: 'error', reason: 'the site refused' }),
  lost: () => ({ type: 'connection', state: 'lost' }),
  back: () => ({ type: 'connection', state: 'back' }),
  listen: () => ({ type: 'listen' }),
  unlisten: () => ({ type: 'unlisten' }),
  hover: (s) => (s.presence === 'hidden' && !s.listening && !s.shown ? { type: 'pointer', at: 'zone' } : { type: 'timer', name: 'hoverExpand' }),
  click: () => ({ type: 'click' }),
  hits: () => ({ type: 'hits' }),
  file: () => ({ type: 'click' }),
  away: () => ({ type: 'away' }),
  return: () => ({ type: 'return' }),
  collapse: (s) => ({ type: 'timer', name: s.finished ? 'finishedHold' : s.presence === 'peek' ? 'peekLeave' : 'collapse' }),
  drag: () => ({ type: 'drag', phase: 'start' }),
}

const busy = { live: [W1, W2], focus: 'w1' } as const
const hiddenAway = () => at({ ...busy, away: true, summary: { done: 0, failed: 0, started: 0 } })
const lostFrom = (s: () => BuddyState) => () => ({ ...s(), connection: 'lost' as const, lostAt: T - 1_000, running: [...s().running, 'offlineGrace' as const] })

// ── The cells ──────────────────────────────────────────────────────────────────────────────────

const CELLS: Record<string, Cell> = {
  'hidden/start': { expect: [sit('compact'), starts('showDelay'), shows(false)], then: ['showDelay'], after: [shows(true), phaseShown('w3', 'thinking')] },
  'hidden/phase': { given: hiddenAway, expect: [sit('hidden'), shows(false), levelIs('w1', 'searching'), noTimerStarts] },
  'hidden/other': { given: hiddenAway, expect: [sit('hidden'), shows(false), levelIs('w2', 'planning'), noTimerStarts] },
  'hidden/alert': { given: hiddenAway, expect: [sit('alert'), room('expanded'), alertShown('a2'), beats('alertEntrance')] },
  'hidden/answered': { cannot: true },
  'hidden/elsewhere': { same: true },
  'hidden/done': { given: hiddenAway, expect: [sit('hidden'), shows(false), ended('w1'), summaryDone(1)] },
  'hidden/failed': { given: hiddenAway, expect: [sit('alert'), truth('alert'), family('blocked'), ended('w2')] },
  'hidden/lost': { expect: [sit('hidden'), shows(false), field('connection', 'offline'), noTimerStarts] },
  'hidden/back': { given: () => at({ connection: 'offline', lostAt: T - 60_000 }), expect: [sit('hidden'), field('connection', 'up'), rereads, noTimerStarts] },
  'hidden/listen': { expect: [sit('listening'), room('compact'), truth('listening')] },
  'hidden/unlisten': { cannot: true },
  'hidden/hover': { expect: [sit('peek'), room('peek')] },
  'hidden/click': { cannot: true },
  'hidden/hits': { cannot: true },
  'hidden/away': { same: true },
  'hidden/return': { given: () => ({ ...hiddenAway(), summary: { done: 1, failed: 0, started: 0 } }), expect: [sit('compact'), truth('returnSummary'), starts('finishedHold')] },
  'hidden/collapse': { cannot: true },
  'hidden/drag': { cannot: true },

  'peek/start': { expect: [sit('compact'), liveCount(2)] },
  'peek/phase': { expect: [sit('peek'), phaseShown('w1', 'searching'), beats('phase')] },
  'peek/other': { given: () => at({ presence: 'peek', ...busy }), same: true },
  'peek/alert': { expect: [sit('alert'), room('expanded'), alertShown('a2')] },
  'peek/answered': { cannot: true },
  'peek/elsewhere': { same: true },
  'peek/done': { expect: [sit('finished'), truth('done'), starts('finishedHold'), beats('done')] },
  'peek/failed': { expect: [sit('alert'), family('blocked')] },
  'peek/lost': { expect: [starts('offlineGrace')], then: ['offlineGrace'] },
  'peek/back': { given: lostFrom(SITUATION_STATES.peek), expect: [sit('peek'), phaseShown('w1', 'thinking'), cancels('offlineGrace'), rereads] },
  'peek/listen': { expect: [sit('listening'), truth('listening')] },
  'peek/unlisten': { cannot: true },
  'peek/hover': { expect: [sit('expanded'), starts('collapse')] },
  'peek/click': { expect: [sit('expanded')] },
  'peek/hits': { expect: [sit('expanded')] },
  'peek/away': { expect: [sit('hidden'), shows(false)] },
  'peek/return': { cannot: true },
  'peek/collapse': { expect: [sit('hidden'), shows(false)] },
  'peek/drag': { expect: [sit('dragging')] },

  'compact/start': { expect: [sit('compact'), inSight(2), focus('w1')] },
  'compact/phase': { expect: [sit('compact'), phaseShown('w1', 'searching'), beats('phase')] },
  'compact/other': { expect: [sit('compact'), phaseShown('w1', 'thinking'), levelIs('w2', 'planning'), noBeat('phase')] },
  'compact/alert': { expect: [sit('alert'), room('expanded'), alertShown('a2'), beats('alertEntrance')] },
  'compact/answered': { cannot: true },
  'compact/elsewhere': { same: true },
  'compact/done': { expect: [sit('finished'), truth('done'), starts('finishedHold'), focus('w1')], then: ['finishedHold'], after: [sit('compact'), focus('w2'), phaseShown('w2', 'tool')] },
  'compact/failed': { expect: [sit('alert'), family('blocked'), alertShown('error:w2')] },
  'compact/lost': { expect: [starts('offlineGrace'), phaseShown('w1', 'thinking')], then: ['offlineGrace'] },
  'compact/back': { given: lostFrom(SITUATION_STATES.compact), expect: [sit('compact'), field('connection', 'up'), rereads, phaseShown('w1', 'thinking')] },
  'compact/listen': { expect: [sit('listening'), truth('listening'), family('active')] },
  'compact/unlisten': { cannot: true },
  'compact/hover': { expect: [sit('expanded'), starts('collapse')] },
  'compact/click': { expect: [sit('expanded')] },
  'compact/hits': { expect: [sit('expanded')] },
  'compact/away': { expect: [sit('hidden'), shows(false)] },
  'compact/return': { cannot: true },
  'compact/collapse': { same: true },
  'compact/drag': { expect: [sit('dragging'), field('dragging', true)] },

  'expanded/start': { expect: [sit('expanded'), inSight(2)] },
  'expanded/phase': { expect: [sit('expanded'), phaseShown('w1', 'searching'), beats('phase')] },
  'expanded/other': { expect: [sit('expanded'), levelIs('w2', 'planning'), noBeat('phase')] },
  'expanded/alert': { expect: [sit('alert'), alertShown('a2'), inSight(1), cancels('collapse')] },
  'expanded/answered': { cannot: true },
  'expanded/elsewhere': { same: true },
  'expanded/done': { expect: [sit('finished'), truth('done'), cancels('collapse')] },
  'expanded/failed': { expect: [sit('alert'), family('blocked')] },
  'expanded/lost': { expect: [starts('offlineGrace')], then: ['offlineGrace'] },
  'expanded/back': { given: lostFrom(SITUATION_STATES.expanded), expect: [sit('expanded'), field('connection', 'up'), rereads] },
  'expanded/listen': { expect: [sit('listening'), room('expanded'), truth('listening')] },
  'expanded/unlisten': { cannot: true },
  'expanded/hover': { same: true },
  'expanded/away': { expect: [sit('hidden'), shows(false), cancels('collapse')] },
  'expanded/return': { cannot: true },
  'expanded/collapse': { expect: [starts('collapseCountdown'), field('countdown', true)], then: ['collapseCountdown'], after: [sit('compact')] },
  'expanded/drag': { expect: [sit('dragging'), card(null)] },

  'alert/start': { expect: [sit('alert'), inSight(2), alertShown('a1')] },
  'alert/phase': { expect: [sit('alert'), alertShown('a1'), levelIs('w1', 'searching')] },
  'alert/other': { expect: [sit('alert'), alertShown('a1'), levelIs('w2', 'planning')] },
  'alert/alert': { expect: [sit('alert'), alertShown('a1'), waiting(1), beats('countPop')] },
  'alert/answered': { expect: [answers(A1.route), beats('nod'), sit('compact'), shownId(null), phaseShown('w1', 'thinking')] },
  'alert/elsewhere': { expect: [truth('elsewhere'), beats('leave'), starts('beat'), shownId(null)], then: ['beat'], after: [sit('compact'), field('leaving', null)] },
  'alert/done': { expect: [sit('alert'), alertShown('a1'), ended('w1')] },
  'alert/failed': { expect: [sit('alert'), alertShown('a1'), queued('error:w2'), waiting(1)] },
  'alert/lost': { expect: [sit('alert'), alertShown('a1'), actions('offline'), starts('offlineGrace')] },
  'alert/back': { given: lostFrom(SITUATION_STATES.alert), expect: [sit('alert'), actions('on'), rereads] },
  'alert/listen': { expect: [sit('listening'), shownId('a1'), actions('listening'), noAnswer] },
  'alert/unlisten': { cannot: true },
  'alert/hover': { same: true },
  'alert/click': { same: true },
  'alert/hits': { same: true },
  'alert/away': { same: true },
  'alert/return': { given: () => ({ ...SITUATION_STATES.alert(), away: true }), same: true },
  'alert/collapse': { same: true },
  'alert/drag': { expect: [sit('dragging'), shownId('a1'), alertShown('a1')] },

  'finished/start': { expect: [sit('compact'), field('finished', null), cancels('finishedHold')] },
  'finished/phase': { cannot: true },
  'finished/other': { same: true },
  'finished/alert': { expect: [sit('alert'), alertShown('a2'), cancels('finishedHold'), field('finished', null)] },
  'finished/answered': { cannot: true },
  'finished/elsewhere': { same: true },
  'finished/done': { expect: [sit('finished'), starts('finishedHold'), (r) => is('two finished', r.after.finished?.count === 2, r.after.finished)] },
  'finished/failed': { expect: [sit('alert'), family('blocked'), field('finished', null)] },
  'finished/lost': { expect: [starts('offlineGrace')], then: ['offlineGrace'] },
  'finished/back': { given: lostFrom(SITUATION_STATES.finished), same: true },
  'finished/listen': { expect: [sit('listening'), field('finished', null), cancels('finishedHold')] },
  'finished/unlisten': { cannot: true },
  'finished/hover': { expect: [sit('expanded'), truth('done'), field('opened', W1)] },
  'finished/click': { expect: [sit('expanded'), truth('done'), field('opened', W1)] },
  'finished/hits': { expect: [sit('expanded')] },
  'finished/away': { expect: [sit('hidden'), shows(false), summaryDone(1)] },
  'finished/return': { cannot: true },
  'finished/collapse': { expect: [sit('compact'), focus('w2'), phaseShown('w2', 'tool')] },
  'finished/drag': { expect: [sit('dragging'), field('finished', null)] },

  'floating/start': { expect: [sit('floating'), inSight(2)] },
  'floating/phase': { expect: [sit('floating'), phaseShown('w1', 'searching'), beats('phase')] },
  'floating/other': { same: true },
  'floating/alert': { expect: [sit('alert'), room('floating'), alertShown('a2')] },
  'floating/answered': { cannot: true },
  'floating/elsewhere': { same: true },
  'floating/done': { expect: [sit('floating'), beats('done'), field('finished', null), ended('w1')] },
  'floating/failed': { expect: [sit('alert'), room('floating'), family('blocked')] },
  'floating/lost': { expect: [starts('offlineGrace')], then: ['offlineGrace'] },
  'floating/back': { given: lostFrom(SITUATION_STATES.floating), expect: [sit('floating'), rereads, field('connection', 'up')] },
  'floating/listen': { expect: [sit('listening'), room('floating')] },
  'floating/unlisten': { cannot: true },
  'floating/hover': { expect: [sit('floating'), card('hover')] },
  'floating/click': { expect: [sit('floating'), card('open')] },
  'floating/away': { expect: [sit('sleeping'), shows(true)] },
  'floating/return': { cannot: true },
  'floating/collapse': { given: () => ({ ...SITUATION_STATES.floating(), card: 'open' as const }), expect: [sit('floating'), card(null)] },
  'floating/drag': { expect: [sit('dragging')] },

  'sleeping/start': { expect: [sit('floating'), beats('wake'), phaseShown('w3', 'thinking')] },
  'sleeping/phase': { given: () => ({ ...SITUATION_STATES.sleeping(), focus: 'w1' }), cannot: true },
  'sleeping/other': { same: true },
  'sleeping/alert': { expect: [sit('alert'), room('floating'), beats('wake')] },
  'sleeping/answered': { cannot: true },
  'sleeping/elsewhere': { same: true },
  'sleeping/done': { given: () => at({ presence: 'sleeping', docked: false, live: [W1], focus: 'w1', away: true }), expect: [sit('sleeping'), ended('w1'), summaryDone(1)] },
  'sleeping/failed': { given: () => at({ presence: 'sleeping', docked: false, live: [W1], focus: 'w1', away: true }), expect: [sit('alert'), room('floating'), family('blocked'), beats('wake')] },
  'sleeping/lost': { expect: [sit('sleeping'), offlineMark] },
  'sleeping/back': { given: () => ({ ...SITUATION_STATES.sleeping(), connection: 'offline' as const, lostAt: T - 60_000 }), expect: [sit('sleeping'), field('connection', 'up'), rereads] },
  'sleeping/listen': { expect: [sit('listening')] },
  'sleeping/unlisten': { cannot: true },
  'sleeping/hover': { expect: [sit('sleeping'), card('hover')] },
  'sleeping/click': { expect: [sit('floating'), card('open'), beats('wake')] },
  'sleeping/away': { same: true },
  'sleeping/return': { given: () => at({ presence: 'sleeping', docked: false, away: true, summary: { done: 2, failed: 0, started: 0 } }), expect: [sit('floating'), truth('returnSummary'), beats('wake')] },
  'sleeping/collapse': { same: true },
  'sleeping/drag': { expect: [sit('dragging')] },

  'listening/start': { expect: [sit('listening'), inSight(2), truth('listening')] },
  'listening/phase': { expect: [sit('listening'), truth('listening'), levelIs('w1', 'searching')] },
  'listening/other': { same: true },
  'listening/alert': { expect: [sit('listening'), queued('a2'), shownId(null), waiting(1)] },
  'listening/answered': { given: () => ({ ...SITUATION_STATES.listening(), queue: [A1], shown: 'a1', beneath: 'compact' as const }), expect: [noAnswer, shownId('a1')], same: true },
  'listening/elsewhere': { same: true },
  'listening/done': { expect: [sit('listening'), ended('w1')] },
  'listening/failed': { expect: [sit('listening'), queued('error:w2'), shownId(null)] },
  'listening/lost': { expect: [sit('listening'), starts('offlineGrace')], then: ['offlineGrace'], after: [sit('listening'), offlineMark] },
  'listening/back': { same: true },
  'listening/listen': { cannot: true },
  'listening/unlisten': { given: () => ({ ...SITUATION_STATES.listening(), queue: [A2] }), expect: [sit('alert'), alertShown('a2'), field('listening', false)] },
  'listening/hover': { same: true },
  'listening/click': { same: true },
  'listening/hits': { same: true },
  'listening/away': { cannot: true },
  'listening/return': { cannot: true },
  'listening/collapse': { same: true },
  'listening/drag': { expect: [sit('dragging'), field('listening', true), truth('listening')] },

  'dragging/start': { expect: [sit('dragging'), liveCount(3)] },
  'dragging/phase': { expect: [sit('dragging'), phaseShown('w1', 'searching'), noBeat('phase')] },
  'dragging/other': { same: true },
  'dragging/alert': { expect: [sit('dragging'), shownId('a2'), alertShown('a2')] },
  'dragging/answered': { cannot: true },
  'dragging/elsewhere': { same: true },
  'dragging/done': { expect: [sit('dragging'), ended('w1'), noBeat('done')] },
  'dragging/failed': { expect: [sit('dragging'), shownId('error:w2'), family('blocked')] },
  'dragging/lost': { expect: [sit('dragging'), starts('offlineGrace')], then: ['offlineGrace'], after: [sit('dragging'), offlineMark] },
  'dragging/back': { given: lostFrom(SITUATION_STATES.dragging), expect: [sit('dragging'), field('connection', 'up')] },
  'dragging/listen': { expect: [sit('dragging'), truth('listening')] },
  'dragging/unlisten': { given: () => ({ ...SITUATION_STATES.dragging(), listening: true }), expect: [sit('dragging'), phaseShown('w1', 'thinking')] },
  'dragging/hover': { cannot: true },
  'dragging/click': { cannot: true },
  'dragging/hits': { cannot: true },
  'dragging/away': { cannot: true },
  'dragging/return': { cannot: true },
  'dragging/collapse': { cannot: true },
  'dragging/drag': { cannot: true },

  'muted/start': { expect: [sit('muted'), shows(false), liveCount(3)] },
  'muted/phase': { expect: [sit('muted'), shows(false), levelIs('w1', 'searching')] },
  'muted/other': { expect: [sit('muted'), shows(false), levelIs('w2', 'planning')] },
  'muted/alert': { expect: [sit('muted'), shows(false), queued('a2')] },
  'muted/answered': { cannot: true },
  'muted/elsewhere': { same: true },
  'muted/done': { expect: [sit('muted'), shows(false), ended('w1')] },
  'muted/failed': { expect: [sit('muted'), shows(false), queued('error:w2')] },
  'muted/lost': { expect: [sit('muted'), shows(false), field('connection', 'offline'), noTimerStarts] },
  'muted/back': { given: () => ({ ...SITUATION_STATES.muted(), connection: 'offline' as const, lostAt: T - 60_000 }), expect: [sit('muted'), shows(false), field('connection', 'up')] },
  'muted/listen': { expect: [sit('listening'), shows(true), truth('listening')] },
  'muted/unlisten': { given: () => ({ ...SITUATION_STATES.muted(), listening: true }), expect: [sit('muted'), shows(false)] },
  'muted/hover': { same: true },
  'muted/click': { cannot: true },
  'muted/hits': { cannot: true },
  'muted/away': { same: true },
  'muted/return': { same: true },
  'muted/collapse': { same: true },
  'muted/drag': { cannot: true },

  'offline/start': { cannot: true },
  'offline/phase': { cannot: true },
  'offline/other': { cannot: true },
  'offline/alert': { cannot: true },
  'offline/answered': { cannot: true },
  'offline/elsewhere': { cannot: true },
  'offline/done': { cannot: true },
  'offline/failed': { cannot: true },
  'offline/lost': { cannot: true },
  'offline/back': { expect: [sit('compact'), truth('recovery'), rereads, field('connection', 'up')] },
  'offline/listen': { expect: [sit('listening'), offlineMark] },
  'offline/unlisten': { given: () => ({ ...SITUATION_STATES.offline(), listening: true }), expect: [sit('offline'), truth('offline')] },
  'offline/hover': { expect: [sit('offline'), room('expanded'), truth('offline')] },
  'offline/click': { expect: [sit('offline'), room('expanded'), truth('offline')] },
  'offline/away': { expect: [sit('hidden'), shows(false)] },
  'offline/return': { given: () => ({ ...SITUATION_STATES.offline(), away: true }), expect: [sit('offline'), truth('offline'), field('away', false)] },
  'offline/collapse': { given: () => ({ ...SITUATION_STATES.offline(), presence: 'expanded' as const }), expect: [sit('offline'), room('compact')] },
  'offline/drag': { expect: [sit('dragging')] },
}

// The lost cells go offline once `offlineGrace` runs out.
for (const s of ['peek', 'compact', 'expanded', 'floating'] as const) {
  CELLS[`${s}/lost`].after = [sit('offline'), truth('offline'), family('blocked'), offlineMark]
}
CELLS['finished/lost'].after = [sit('offline'), truth('offline'), field('finished', null)]

// ── Running a cell ─────────────────────────────────────────────────────────────────────────────

/** What a cell's checks found wrong under `reducer`: an empty list passes. */
function failures(key: string, cell: Cell, reducer: Reducer): string[] {
  const [situation, event] = key.split('/') as [Situation, MatrixEvent]
  const before = (cell.given ?? SITUATION_STATES[situation])()
  const input = (cell.input ?? EVENT_INPUTS[event])(before)
  const first = run(before, [input], reducer)
  const timers = (cell.then ?? []).map((name): BuddyInput => ({ type: 'timer', name }))
  const rest = run(first.state, timers, reducer, T + 1_000)
  const beforeView = viewOf(before)
  const atEvent: Ran = { before, beforeView, after: first.state, view: viewOf(first.state), effects: first.effects }
  const atEnd: Ran = { before, beforeView, after: rest.state, view: viewOf(rest.state), effects: [...first.effects, ...rest.effects] }
  const checks: [Check, Ran][] = [
    ...(cell.expect ?? []).map((c): [Check, Ran] => [c, atEvent]),
    ...(cell.after ?? []).map((c): [Check, Ran] => [c, atEnd]),
    ...(cell.same ? [[sameSeen, atEvent] as [Check, Ran]] : []),
  ]
  return checks.map(([check, ran]) => check(ran)).filter((f): f is string => f !== null)
}

/** The matrix's "no change": what the buddy shows is as it was, and it plays nothing. */
const sameSeen: Check = (r) => {
  const seen = (v: BuddyView) => JSON.stringify({ ...v, inSight: undefined })
  if (seen(r.view) !== seen(r.beforeView)) return `nothing seen changes, got ${seen(r.view)} from ${seen(r.beforeView)}`
  return is('no beat and no answer', !r.effects.some((e) => e.kind === 'beat' || e.kind === 'answer'), r.effects)
}

const identity: Reducer = (state) => ({ state, effects: [] })

describe('the interruption matrix', () => {
  it('names the situations and the events the machine knows', () => {
    expect(Object.keys(matrix.situations).filter((s) => s !== 'play' && s !== 'file')).toEqual([...SITUATIONS])
    expect(Object.keys(matrix.events)).toEqual([...EVENTS])
  })

  it('has a test for exactly its shipping cells, every cell outside its later list', () => {
    expect(shipping.length, 'the shipping cells').toBe(223)
    expect(Object.keys(CELLS).sort()).toEqual([...shipping].sort())
  })

  it("holds the machine's cannot-occur table to the JSON's", () => {
    const table = Object.entries(CANNOT_OCCUR).flatMap(([s, events]) => events.map((e) => `${s}/${e}`)).sort()
    expect(table).toEqual([...cannotInJson].sort())
    expect(Object.entries(CELLS).filter(([, c]) => c.cannot).map(([k]) => k).sort()).toEqual(table)
  })
})

describe('every shipping cell', () => {
  for (const [key, cell] of Object.entries(CELLS)) {
    const [s, e] = key.split('/')
    it(`${key}: ${matrix.cells[s][e]}`, () => {
      if (cell.cannot) {
        const before = (cell.given ?? SITUATION_STATES[s as Situation])()
        expect(() => reduce(before, EVENT_INPUTS[e as MatrixEvent](before), T)).toThrow(CannotOccur)
        return
      }
      expect(failures(key, cell, reduce)).toEqual([])
    })
  }
})

describe('the control: a reducer that changes nothing', () => {
  const changing = Object.entries(CELLS).filter(([, c]) => !c.cannot && !c.same)

  it('leaves no cell whose outcome changes anything unchecked', () => {
    expect(changing.length, 'the outcome cells that change something').toBeGreaterThan(130)
    const passed = changing.filter(([key, cell]) => failures(key, cell, identity).length === 0).map(([key]) => key)
    expect(passed, 'cells whose checks pass with nothing changed').toEqual([])
  })

  it('fails a planted cell whose checks assert nothing', () => {
    expect(failures('compact/hover', { expect: [] }, identity)).toEqual([])
    expect(failures('compact/hover', CELLS['compact/hover'], identity)).not.toEqual([])
  })
})
