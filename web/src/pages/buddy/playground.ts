// ── The buddy playground's model (#3892) ────────────────────────────────────────────────────────
//
// The playground drives the real state machine (`lib/buddy/machine.ts`) and mocks only what it
// hears: the work and its phases, the waiting list, the connection, push-to-talk, the shell's
// pointer, drag, idle and the owner's own controls, and a clock. Each control is one input; the
// clock fires the machine's named timers when they are due, at their lengths in `timing.ts`.
// Nothing here reaches the network. An answer the machine asks to send is shown in the log and
// never sent.
//
// Pure: the page holds one `Playground` and replaces it with what `send`, `advance` and `fire`
// return, and the scripted day (`scriptedDay.ts`) and its tests run the same functions.

import { CANNOT_OCCUR, CannotOccur, INITIAL, eventOf, focusedWork, reduce, situationOf, type BuddyState, type MatrixEvent, type Situation } from '../../lib/buddy/machine'
import type { Alert, AlertKind, BuddyInput, Effect, Work } from '../../lib/buddy/inputs'
import { stepsOnInput, type StepWindows } from '../../lib/buddy/steps'
import { TIMINGS, type TimerName } from '../../lib/buddy/timing'
import type { WorkPhase } from '../../lib/workPhase'

/** One input the machine heard, and what it asked for. */
export interface LogEntry {
  n: number
  /** The clock when it landed (ms since the playground started). */
  at: number
  /** The control's label, or the timer that fired. */
  label: string
  input: BuddyInput
  event: MatrixEvent | null
  from: Situation
  to: Situation
  effects: readonly Effect[]
}

export interface Playground {
  machine: BuddyState
  /** The playground's clock, in ms since it started. */
  now: number
  /** When each running timer is due. */
  due: Partial<Record<TimerName, number>>
  log: readonly LogEntry[]
  /** How many pieces of work and alerts it has made, for their ids and names. */
  made: number
  /** The phase the phase control sets. */
  phase: WorkPhase
  /** Each piece of work's step window, folded from the same inputs (`lib/buddy/steps.ts`). */
  steps: StepWindows
}

/** The playground's first state: Buddy status on and nothing running, at the clock's start. */
export function startPlayground(): Playground {
  const start: Playground = { machine: INITIAL, now: 0, due: {}, log: [], made: 0, phase: 'searching', steps: {} }
  return send(start, { type: 'enabled', on: true }, 'Buddy status on')
}

/** Whether the matrix says `input` cannot reach the machine as it stands: the control is off. */
export function cannotOccur(machine: BuddyState, input: BuddyInput): boolean {
  const event = eventOf(machine, input)
  return Boolean(event && CANNOT_OCCUR[situationOf(machine)].includes(event))
}

/** `pg` after the machine hears `input` now. A cell that cannot occur changes nothing; its
 *  control is off, so only a script can send one. */
export function send(pg: Playground, input: BuddyInput, label: string): Playground {
  if (cannotOccur(pg.machine, input)) return pg
  let step
  try {
    step = reduce(pg.machine, input, pg.now)
  } catch (e) {
    if (e instanceof CannotOccur) return pg
    throw e
  }
  const due = { ...pg.due }
  for (const e of step.effects) {
    if (e.kind !== 'timer') continue
    if (e.start) due[e.name] = pg.now + TIMINGS[e.name]
    else delete due[e.name]
  }
  if (input.type === 'timer') delete due[input.name]
  const entry: LogEntry = {
    n: pg.log.length + 1, at: pg.now, label, input, event: eventOf(pg.machine, input),
    from: situationOf(pg.machine), to: situationOf(step.state), effects: step.effects,
  }
  return { ...pg, machine: step.state, due, log: [...pg.log, entry], steps: stepsOnInput(pg.steps, input) }
}

/** The timer due next, and when. */
export function nextDue(pg: Playground): { name: TimerName; at: number } | null {
  let next: { name: TimerName; at: number } | null = null
  for (const [name, at] of Object.entries(pg.due) as [TimerName, number][]) {
    if (!next || at < next.at) next = { name, at }
  }
  return next
}

/** The clock moved on to the next timer, which fires. */
export function fire(pg: Playground): Playground {
  const next = nextDue(pg)
  if (!next) return pg
  return send({ ...pg, now: Math.max(pg.now, next.at) }, { type: 'timer', name: next.name }, `${next.name} timer`)
}

/** The clock moved on `ms`, every timer due on the way firing at its time, in order. */
export function advance(pg: Playground, ms: number): Playground {
  const until = pg.now + ms
  let p = pg
  for (let next = nextDue(p); next && next.at <= until; next = nextDue(p)) p = fire(p)
  return { ...p, now: until }
}

// ── The controls ──────────────────────────────────────────────────────────────────────────────

/** The names the playground gives the work it starts. */
const NAMES = ['Release notes', 'Nightly digest', 'Tidy downloads', 'Inbox sweep', 'Weekly report', 'Photo backup']

/** What a phase's work says it is doing, for the phases whose line carries a detail. */
const LINES: Partial<Record<WorkPhase, string>> = {
  searching: 'changelog style', tool: 'Reading the changelog', approval: 'git push', input: 'Which branch?',
  error: 'The tests failed', delegating: '',
}

/** The work and alert ids the next start or alert takes. */
const nextId = (pg: Playground, prefix: string) => `${prefix}${pg.made + 1}`

export type ControlGroup = 'work' | 'waiting' | 'connection' | 'voice' | 'shell' | 'owner'

export interface Control {
  id: string
  group: ControlGroup
  label: string
  /** The input it sends now, or null when there is nothing for it to act on. */
  input: (pg: Playground) => BuddyInput | null
}

const focused = (pg: Playground): Work | null => focusedWork(pg.machine)
const shownAlert = (pg: Playground): Alert | null => pg.machine.queue.find((a) => a.id === pg.machine.shown) ?? null
const otherWork = (pg: Playground): Work | null => pg.machine.live.find((w) => w.id !== focused(pg)?.id) ?? null

/** An alert on the focused work, or on work in the background when nothing runs. */
function alertOf(pg: Playground, kind: AlertKind, brief: string, broadReach = false): Alert {
  const id = nextId(pg, 'a')
  const work = focused(pg) ?? pg.machine.live[0]
  return {
    id, kind, workId: work?.id ?? 'scheduled', work: work?.name ?? 'Scheduled task', brief,
    route: `/api/approvals/${id}/answer`, consequence: broadReach ? 3 : 2, raisedAt: pg.now, broadReach,
  }
}

const answer = (pg: Playground, choice: Extract<BuddyInput, { type: 'answer' }>['choice'], confirmed = false): BuddyInput | null => {
  const alert = shownAlert(pg)
  if (!alert || alert.kind === 'error') return null
  if ((choice.kind === 'allow' || choice.kind === 'deny') !== (alert.kind === 'approval')) return null
  return { type: 'answer', id: alert.id, choice, confirmed }
}

const resolved = (pg: Playground, how: 'elsewhere' | 'expired' | 'withdrawn'): BuddyInput | null =>
  pg.machine.shown ? { type: 'resolved', id: pg.machine.shown, how } : null

/** Every control the playground offers, in its panel's order. Pointer follow, the hits' play and
 *  a file drop are later work (#3894, #3895). */
export const CONTROLS: readonly Control[] = [
  { id: 'start', group: 'work', label: 'Start work', input: (pg) => ({ type: 'start', work: { id: nextId(pg, 'w'), name: NAMES[pg.made % NAMES.length], phase: 'thinking', line: '', startedAt: pg.now } }) },
  { id: 'phase', group: 'work', label: 'Set its phase', input: (pg) => { const w = focused(pg); return w ? { type: 'phase', id: w.id, phase: pg.phase, line: LINES[pg.phase] ?? '' } : null } },
  { id: 'other', group: 'work', label: 'Other work moves on', input: (pg) => { const w = otherWork(pg); return w ? { type: 'phase', id: w.id, phase: w.phase === 'tool' ? 'streaming' : 'tool', line: 'Reading the inbox' } : null } },
  { id: 'done', group: 'work', label: 'It finishes', input: (pg) => { const w = focused(pg); return w ? { type: 'finish', id: w.id, how: 'done' } : null } },
  { id: 'failed', group: 'work', label: 'It fails', input: (pg) => { const w = focused(pg); return w ? { type: 'finish', id: w.id, how: 'error', reason: 'The tests failed' } : null } },
  { id: 'approval', group: 'waiting', label: 'An approval arrives', input: (pg) => ({ type: 'alert', alert: alertOf(pg, 'approval', 'git push') }) },
  { id: 'broad', group: 'waiting', label: 'A broad-reach approval arrives', input: (pg) => ({ type: 'alert', alert: alertOf(pg, 'approval', 'Delete 14 files', true) }) },
  { id: 'question', group: 'waiting', label: 'A question arrives', input: (pg) => ({ type: 'alert', alert: alertOf(pg, 'input', 'Which branch should I use?') }) },
  { id: 'allow', group: 'waiting', label: 'Allow', input: (pg) => answer(pg, { kind: 'allow' }) },
  { id: 'confirm', group: 'waiting', label: 'Allow, confirmed', input: (pg) => (pg.machine.confirming ? answer(pg, { kind: 'allow' }, true) : null) },
  { id: 'deny', group: 'waiting', label: 'Deny', input: (pg) => answer(pg, { kind: 'deny' }) },
  { id: 'reply', group: 'waiting', label: 'Send an answer', input: (pg) => answer(pg, { kind: 'send', text: 'main' }) },
  { id: 'modEnter', group: 'waiting', label: '⌘Enter on the alert', input: (pg) => (pg.machine.shown ? { type: 'key', key: 'ModEnter', windowFocused: true, onAlert: true } : null) },
  { id: 'elsewhere', group: 'waiting', label: 'Answered elsewhere', input: (pg) => resolved(pg, 'elsewhere') },
  { id: 'expired', group: 'waiting', label: 'It expires', input: (pg) => resolved(pg, 'expired') },
  { id: 'withdrawn', group: 'waiting', label: 'It is withdrawn', input: (pg) => resolved(pg, 'withdrawn') },
  { id: 'lost', group: 'connection', label: 'Lose the connection', input: () => ({ type: 'connection', state: 'lost' }) },
  { id: 'back', group: 'connection', label: 'Connection back', input: () => ({ type: 'connection', state: 'back' }) },
  { id: 'restarting', group: 'connection', label: 'The gateway restarts', input: () => ({ type: 'connection', state: 'restarting' }) },
  { id: 'listen', group: 'voice', label: 'Start listening', input: () => ({ type: 'listen' }) },
  { id: 'unlisten', group: 'voice', label: 'Stop listening', input: (pg) => (pg.machine.listening ? { type: 'unlisten' } : null) },
  { id: 'zone', group: 'shell', label: 'Pointer on the top edge', input: () => ({ type: 'pointer', at: 'zone' }) },
  { id: 'orb', group: 'shell', label: 'Pointer on the orb', input: () => ({ type: 'pointer', at: 'orb' }) },
  { id: 'leave', group: 'shell', label: 'Pointer leaves', input: () => ({ type: 'pointer', at: 'away' }) },
  { id: 'click', group: 'shell', label: 'Click the orb', input: () => ({ type: 'click' }) },
  { id: 'hits', group: 'shell', label: 'Quick hits on the orb', input: () => ({ type: 'hits' }) },
  { id: 'drag', group: 'shell', label: 'Start a drag', input: (pg) => (pg.machine.dragging ? null : { type: 'drag', phase: 'start' }) },
  { id: 'dropDocked', group: 'shell', label: 'Drop on the top edge', input: (pg) => (pg.machine.dragging ? { type: 'drag', phase: 'end', docked: true } : null) },
  { id: 'dropFloating', group: 'shell', label: 'Drop anywhere else', input: (pg) => (pg.machine.dragging ? { type: 'drag', phase: 'end', docked: false } : null) },
  { id: 'escape', group: 'shell', label: 'Escape', input: () => ({ type: 'key', key: 'Escape', windowFocused: true, onAlert: false }) },
  { id: 'away', group: 'owner', label: 'The owner goes away', input: (pg) => (pg.machine.away ? null : { type: 'away' }) },
  { id: 'return', group: 'owner', label: 'The owner comes back', input: (pg) => (pg.machine.away ? { type: 'return' } : null) },
  { id: 'hide', group: 'owner', label: 'Hide the buddy', input: (pg) => (pg.machine.muted ? null : { type: 'hide' }) },
  { id: 'show', group: 'owner', label: 'Show the buddy', input: (pg) => (pg.machine.muted ? { type: 'show' } : null) },
  { id: 'off', group: 'owner', label: 'Buddy status off', input: (pg) => (pg.machine.enabled ? { type: 'enabled', on: false } : null) },
  { id: 'on', group: 'owner', label: 'Buddy status on', input: (pg) => (pg.machine.enabled ? null : { type: 'enabled', on: true }) },
  { id: 'quietOn', group: 'owner', label: 'Quiet hours start', input: (pg) => (pg.machine.quietHours ? null : { type: 'quietHours', on: true }) },
  { id: 'quietOff', group: 'owner', label: 'Quiet hours end', input: (pg) => (pg.machine.quietHours ? { type: 'quietHours', on: false } : null) },
]

export const CONTROL_GROUPS: Readonly<Record<ControlGroup, string>> = {
  work: 'Work', waiting: 'Waiting on you', connection: 'Connection', voice: 'Push-to-talk', shell: 'Pointer and drag', owner: 'The owner',
}

/** The control `id`, which must exist. */
export function controlOf(id: string): Control {
  const control = CONTROLS.find((c) => c.id === id)
  if (!control) throw new Error(`no playground control "${id}"`)
  return control
}

/** The input `control` sends now, or null while it is off. */
export function inputOf(pg: Playground, control: Control): BuddyInput | null {
  const input = control.input(pg)
  return input && !cannotOccur(pg.machine, input) ? input : null
}

/** `pg` after the control `id` is used, or unchanged while it is off. The work and alerts it makes
 *  are counted, so each gets its own id. */
export function use(pg: Playground, id: string): Playground {
  const control = controlOf(id)
  const input = inputOf(pg, control)
  if (!input) return pg
  const next = send(pg, input, control.label)
  return input.type === 'start' || input.type === 'alert' ? { ...next, made: next.made + 1 } : next
}
