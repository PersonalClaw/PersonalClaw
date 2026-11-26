// ── The buddy's host (#3832): the machine's inputs from the gateway, and the one effect it sends ──
//
// The state machine (`machine.ts`) is pure; this is what feeds it and runs what it asks. Its truth
// inputs are levels (`docs/design/buddy/state-machine.md`, "Inputs"): what is running
// (`GET /api/running`, #3889), what is waiting on you (`GET /api/waiting`, #3888) and whether the
// microphone is live (`GET /api/listening`, #3833), all read again in full whenever the doorbell
// rings, so a missed frame can never leave a stale face. A burst of
// rings is one read of each list. Each read is diffed against the machine's own state and handed
// to it as inputs: work that started, changed phase or ended, and alerts that arrived or left.
//
// The one effect that reaches the network is an answer, and it goes only to the item's own route,
// with the ids read off that route as every other surface reads them (`waitingAnswer.ts`): an
// approval through `api.resolveApproval`, the phone's call, and an agent's question through
// `api.answerChatQuestion`, the chat's. The machine raises that effect only for the owner's explicit
// answer, and the host adds two refusals of its own: nothing is sent for an alert that is not the
// one showing, or for an item past answering (`isStale`), which leaves as expired instead.
//
// Timers run only while the buddy shows: hidden, the host holds none and wakes only on a ring.
// While the owner holds the card (the pointer or the keyboard focus on it), its folds wait
// ("No timed loss of content"), and they start over when it is let go.

import { api, type BuddyPolicy, type QuestionAnswer, type RunningList, type RunningRow, type WaitingItem, type WaitingList } from '../api'
import { approvalOf, isStale, waitingText, WAITING_KIND_LABEL } from '../waitingList'
import { waitingQuestionRef } from '../waitingAnswer'
import { confirmsOf } from '../../pages/chat/approvalMeta'
import { CANNOT_OCCUR, eventOf, INITIAL, isHidden, reduce, situationOf, type BuddyState } from './machine'
import type { Alert, AnswerChoice, Beat, BuddyInput, Effect, Work } from './inputs'
import { byWaitingOrder } from './alertQueue'
import { stepsOnInput, type StepWindows } from './steps'
import { TIMINGS, type TimerName } from './timing'

/** An agent's question's answer, as the chat's question card sends it. */
export type QuestionBody = { answers: QuestionAnswer[] } | { skip: true }

/** What the host reads, sends and schedules with. The page passes the real ones; a test, fakes. */
export interface HostDeps {
  running(): Promise<RunningList>
  waiting(): Promise<WaitingList>
  /** Whether the microphone is live (`GET /api/listening`). */
  listening(): Promise<{ on: boolean }>
  /** Buddy status and the surfaces it shows on (`GET /api/buddy/policy`, #3901), for the buddy's
   *  own page (`BUDDY_DEPS`). A host without it, the phone's one-work view, is always on. */
  policy?(): Promise<Pick<BuddyPolicy, 'show' | 'surfaces'>>
  resolveApproval(id: string, action: 'approve' | 'reject'): Promise<unknown>
  answerChatQuestion(session: string, question: string, body: QuestionBody): Promise<unknown>
  now(): number
  setTimer(fire: () => void, ms: number): unknown
  clearTimer(handle: unknown): void
}

/** The host as the renderer reads it: the machine, each work's step window, and the rows and items
 *  the card draws from, by the machine's ids. */
export interface HostSnapshot {
  machine: BuddyState
  steps: StepWindows
  rows: ReadonlyMap<string, RunningRow>
  items: ReadonlyMap<string, WaitingItem>
  /** The beats the last change asked for, for the orb to play. */
  beats: readonly Beat[]
  /** The lists have been read at least once, so what the machine says is the gateway's. */
  read: boolean
}

export interface BuddyHost {
  /** An input from the shell or the owner's controls (a hover, a click, a key, listening, …). */
  send(input: BuddyInput): void
  /** A change frame rang: read both lists again. */
  ring(): void
  /** The owner answered the showing alert with an explicit control: `true` once it landed. */
  answer(id: string, choice: AnswerChoice, opts?: { reply?: QuestionBody; confirmed?: boolean }): Promise<boolean>
  /** The pointer or the keyboard focus is on the card, or has left it. */
  hold(held: boolean): void
  /** One of the app's own windows went full screen, or left it. */
  fullScreen(on: boolean): void
  snapshot(): HostSnapshot
  /** How many timers the host holds: none while hidden. */
  timers(): number
  stop(): void
}

/** The real reads and answers: the same `api` calls the lists' other readers and answerers make. */
export const LIVE_DEPS: Omit<HostDeps, 'now' | 'setTimer' | 'clearTimer'> = {
  running: () => api.running(),
  waiting: () => api.waiting(),
  listening: () => api.listening(),
  resolveApproval: (id, action) => api.resolveApproval(id, action),
  answerChatQuestion: (session, question, body) => api.answerChatQuestion(session, question, body),
}

/** The buddy page's: the live reads and Buddy status as the gateway keeps it. */
export const BUDDY_DEPS: Omit<HostDeps, 'now' | 'setTimer' | 'clearTimer'> = {
  ...LIVE_DEPS,
  policy: () => api.buddyPolicy(),
}

/** The folds that wait while the owner holds the card. */
const FOLDS: readonly TimerName[] = ['collapse', 'collapseCountdown', 'finishedHold']

/** The waiting list's order, as the machine compares it: the greater consequence first. */
const CONSEQUENCE: Record<WaitingItem['consequence'], number> = { destructive: 3, changes: 2, asks: 1 }

function epoch(iso: string | null, fallback: number): number {
  const at = iso ? Date.parse(iso) : NaN
  return Number.isFinite(at) ? at : fallback
}

/** A running row as the machine's work. */
export function workOf(row: RunningRow, now: number): Work {
  return { id: row.work, name: row.name || row.work, phase: row.phase, line: row.line, startedAt: epoch(row.started_at, now) }
}

/** A waiting item as the machine's alert, or `null` for one the buddy cannot answer in its card: a
 *  kind other than an approval or an agent's question, or one whose route is not its kind's shape
 *  (`waitingAnswer.ts`), so nothing could be sent for it. Those still show in PersonalClaw. */
export function alertOf(item: WaitingItem, workName: string, now: number): Alert | null {
  const approval = item.kind === 'approval' ? approvalOf(item) : null
  if (item.kind === 'approval' ? !approval : !(item.kind === 'question' && waitingQuestionRef(item))) return null
  return {
    id: item.id,
    kind: item.kind === 'approval' ? 'approval' : 'input',
    workId: item.work,
    work: workName || WAITING_KIND_LABEL[item.kind],
    brief: waitingText(item),
    route: item.answer.path,
    consequence: CONSEQUENCE[item.consequence] ?? 0,
    raisedAt: epoch(item.asked_at, now),
    // Broad reach is what the card's prompt asks a second confirmation for, read the same way.
    broadReach: approval ? confirmsOf(approval.confirms, approval.risk).length > 0 : undefined,
  }
}

/** A chat's own name, as a helper's row names the chat it works for (`session`, no `dashboard:`). */
const chatName = (session: string): string => session.replace(/^dashboard:/, '')

/** How a piece of work that left the running list ended, from its row among the endings. */
function howOf(ended: RunningRow | undefined): 'done' | 'error' | 'stopped' {
  return ended?.phase === 'error' ? 'error' : ended?.phase === 'done' ? 'done' : 'stopped'
}

/** The step windows of the work the card can still show (#3902): live work, the finished work in
 *  its hold, and the work the card was opened on. A window for work the machine has let go of is
 *  dropped, or a day's errands would each keep one forever (the twelve-hour day's test). */
function keptSteps(steps: StepWindows, machine: BuddyState): StepWindows {
  const kept = new Set<string>(machine.live.map((w) => w.id))
  if (machine.finished) kept.add(machine.finished.work.id)
  if (machine.opened) kept.add(machine.opened.id)
  const ids = Object.keys(steps)
  return ids.every((id) => kept.has(id)) ? steps : Object.fromEntries(ids.filter((id) => kept.has(id)).map((id) => [id, steps[id]]))
}

export function createBuddyHost(deps: HostDeps, onChange: (s: HostSnapshot) => void): BuddyHost {
  let machine: BuddyState = INITIAL
  let steps: StepWindows = {}
  let rows = new Map<string, RunningRow>()
  let items = new Map<string, WaitingItem>()
  let beats: Beat[] = []
  let held = false
  let stopped = false
  let readOnce = false
  /** Whether the first waiting alert may interrupt you, from the last read; `null` with none. */
  let firstMayInterrupt: boolean | null = null
  /** One of the app's own windows is full screen (the desktop shell's). */
  let fullScreen = false
  const timers = new Map<TimerName, unknown>()
  /** The folds asked for while held, started when the card is let go. */
  const waitingFolds = new Set<TimerName>()
  let reading = false
  let again = false
  /** The request the last `answer` effect started, for the control that asked. */
  let sending: Promise<boolean> | null = null
  const replies = new Map<string, QuestionBody>()
  /** Each error alert's work's chat (its ending row's `session`), and since when the error has been
   *  on screen without a break: an error leaves once that work or its chat has gone on, and only
   *  after it has been on screen for a `finishedHold`, so no failure is dropped unseen. */
  const errorChat = new Map<string, string>()
  const firstShown = new Map<string, number>()
  const goneOn = new Set<string>()
  let goneOnTimer: unknown = null

  const clear = (name: TimerName) => {
    if (timers.has(name)) deps.clearTimer(timers.get(name))
    timers.delete(name)
    waitingFolds.delete(name)
  }
  const start = (name: TimerName) => {
    clear(name)
    if (held && FOLDS.includes(name)) {
      waitingFolds.add(name)
      return
    }
    timers.set(name, deps.setTimer(() => {
      timers.delete(name)
      apply({ type: 'timer', name })
      publish()
    }, TIMINGS[name]))
  }

  /** Sends the answer on the item's own route; `true` once it landed. */
  const post = async (id: string, choice: AnswerChoice): Promise<boolean> => {
    const item = items.get(id)
    if (!item || isStale(item, deps.now())) return false
    try {
      if (item.kind === 'approval') {
        const ap = approvalOf(item)
        if (!ap || (choice.kind !== 'allow' && choice.kind !== 'deny')) return false
        await deps.resolveApproval(ap.id, choice.kind === 'allow' ? 'approve' : 'reject')
      } else {
        const ref = waitingQuestionRef(item)
        const body = replies.get(id)
        if (!ref || !body) return false
        await deps.answerChatQuestion(ref.session, ref.question, body)
      }
      return true
    } catch {
      return false
    } finally {
      replies.delete(id)
      ring()
    }
  }

  const run = (e: Effect) => {
    if (e.kind === 'timer') {
      if (e.start) start(e.name)
      else clear(e.name)
    } else if (e.kind === 'answer') sending = post(e.id, e.choice)
    else if (e.kind === 'reread') ring()
    else if (e.kind === 'beat') beats.push(e.beat)
  }

  /** Whether the machine can take `input` where it stands: a cell the matrix rules out is never
   *  sent, so the host never leans on the machine's production fallback. */
  const possible = (input: BuddyInput) => {
    const event = eventOf(machine, input)
    return !event || !CANNOT_OCCUR[situationOf(machine)].includes(event)
  }

  function apply(input: BuddyInput) {
    if (stopped || !possible(input)) return
    const step = reduce(machine, input, deps.now())
    machine = step.state
    // A hold counts only unbroken time on screen: an alert hidden by the owner, behind listening, with
    // Buddy status off, or sent back behind one that took its place starts over when it shows again.
    const shownNow = machine.shown
    const onScreen = shownNow !== null && machine.enabled && !machine.muted && !machine.listening
    for (const id of [...firstShown.keys()]) if (!onScreen || id !== shownNow) firstShown.delete(id)
    if (onScreen && shownNow !== null && !firstShown.has(shownNow)) firstShown.set(shownNow, deps.now())
    steps = keptSteps(stepsOnInput(steps, input), machine)
    for (const e of step.effects) run(e)
    if (isHidden(machine)) {
      for (const name of [...timers.keys(), ...waitingFolds]) clear(name)
      if (goneOnTimer !== null) deps.clearTimer(goneOnTimer)
      goneOnTimer = null
    }
  }

  const publish = () => {
    if (stopped) return
    onChange({ machine, steps, rows, items, beats, read: readOnce })
    beats = []
  }

  /** The machine's quiet hours: the gateway's decision for the first alert, or the app full screen,
   *  over which only alerts appear, as a quiet peek. With no alert and no full screen, it stands. */
  const syncQuiet = () => {
    const quiet = fullScreen || firstMayInterrupt === false
    if ((fullScreen || firstMayInterrupt !== null || machine.quietHours) && quiet !== machine.quietHours) apply({ type: 'quietHours', on: quiet })
  }

  /** One read of each list, as levels. */
  const levels = (running: RunningList, waiting: WaitingList) => {
    const now = deps.now()
    const nextRows = new Map(running.running.map((r) => [r.work, r]))
    const ended = new Map(running.finished.map((r) => [r.work, r]))
    for (const row of running.running) {
      const live = machine.live.find((w) => w.id === row.work)
      if (!live) apply({ type: 'start', work: workOf(row, now) })
      else if (live.phase !== row.phase || live.line !== row.line) apply({ type: 'phase', id: row.work, phase: row.phase, line: row.line })
    }
    for (const w of machine.live) {
      if (nextRows.has(w.id)) continue
      const end = ended.get(w.id)
      if (howOf(end) === 'error' && end?.session) errorChat.set(`error:${w.id}`, chatName(end.session))
      // A chat whose turn ends after one of its helpers failed has said the failure in its answer.
      if (end?.kind === 'chat') {
        for (const a of machine.queue) if (a.kind === 'error' && errorChat.get(a.id) === chatName(end.session || w.id)) goneOn.add(a.id)
      }
      apply({ type: 'finish', id: w.id, how: howOf(end), reason: end?.line || undefined })
    }
    const nameOf = (work: string, item: WaitingItem) =>
      nextRows.get(work)?.name || ended.get(work)?.name || String((item.brief as { session_title?: unknown }).session_title ?? '')
    const before = items
    const nextItems = new Map<string, WaitingItem>()
    const alerts: Alert[] = []
    for (const item of waiting.waiting) {
      if (isStale(item, now)) continue
      const alert = alertOf(item, nameOf(item.work, item), now)
      if (!alert) continue
      nextItems.set(item.id, item)
      alerts.push(alert)
    }
    rows = nextRows
    items = nextItems
    // Quiet hours, as the gateway decided for what would interrupt now: an alert arrives as a quiet
    // peek when the first of them may not interrupt you (`interrupt`, #3888).
    const first = [...alerts].sort(byWaitingOrder).map((a) => nextItems.get(a.id))[0]
    firstMayInterrupt = first ? first.interrupt === 'deliver' : null
    syncQuiet()
    // In the list's one order whatever order they came in, so the first to show is the first due.
    alerts.sort(byWaitingOrder)
    for (const alert of alerts) {
      const had = machine.queue.find((a) => a.id === alert.id)
      if (!had || had.brief !== alert.brief || had.work !== alert.work) apply({ type: 'alert', alert })
    }
    for (const a of machine.queue) {
      if (a.kind === 'error' || nextItems.has(a.id)) continue
      const was = before.get(a.id)
      apply({ type: 'resolved', id: a.id, how: was && isStale(was, now) ? 'expired' : 'elsewhere' })
    }
    settleErrors(now)
  }

  /** An error alert whose work has gone on (the same work running again, or the chat it worked for
   *  running a turn begun after it failed, or ending one) leaves once it has shown for a
   *  `finishedHold`; one not shown yet waits for its turn, so the owner always sees it once. A
   *  re-read is asked for when the first such hold ends. */
  const settleErrors = (now: number) => {
    const live = [...rows.values()]
    let soonest = Infinity
    for (const a of machine.queue) {
      if (a.kind !== 'error') continue
      const chat = errorChat.get(a.id)
      const again = rows.has(a.workId)
        || (chat !== undefined && live.some((r) => r.kind === 'chat' && chatName(r.session || r.work) === chat && epoch(r.started_at, 0) > a.raisedAt))
      if (again) goneOn.add(a.id)
      if (!goneOn.has(a.id)) continue
      const shown = firstShown.get(a.id)
      if (shown === undefined) continue
      const due = shown + TIMINGS.finishedHold
      if (now >= due) apply({ type: 'resolved', id: a.id, how: 'withdrawn' })
      else soonest = Math.min(soonest, due)
    }
    const inQueue = new Set(machine.queue.map((a) => a.id))
    for (const id of [...firstShown.keys()]) if (!inQueue.has(id)) firstShown.delete(id)
    for (const id of [...errorChat.keys()]) if (!inQueue.has(id) && !machine.live.some((w) => `error:${w.id}` === id)) errorChat.delete(id)
    for (const id of [...goneOn]) if (!inQueue.has(id)) goneOn.delete(id)
    if (goneOnTimer !== null) deps.clearTimer(goneOnTimer)
    goneOnTimer = null
    if (soonest < Infinity && !isHidden(machine)) {
      goneOnTimer = deps.setTimer(() => {
        goneOnTimer = null
        ring()
      }, Math.max(0, soonest - now))
    }
  }

  async function read() {
    if (reading) {
      again = true
      return
    }
    reading = true
    try {
      do {
        again = false
        // Read beside the lists, settled on its own: a policy read that fails leaves Buddy status
        // as the last one said it, and the lists are still read.
        const policyRead = deps.policy ? Promise.allSettled([Promise.resolve().then(() => deps.policy!())]) : null
        const [running, waiting, listening] = await Promise.all([deps.running(), deps.waiting(), deps.listening()])
        const [policy] = policyRead ? await policyRead : []
        if (stopped) return
        // Buddy status is the gateway's (`buddy.show`), and the window's surface is off while it
        // is: the policy says both, as the shell, the menu bar and Settings read them.
        if (policy?.status === 'fulfilled') {
          const on = policy.value.show && policy.value.surfaces.window
          if (on !== machine.enabled) apply({ type: 'enabled', on })
        }
        if (listening.on !== machine.listening) apply({ type: listening.on ? 'listen' : 'unlisten' })
        levels(running, waiting)
        readOnce = true
        publish()
      } while (again)
    } catch {
      // A read that failed changes nothing: the faces stay as the last read left them, and the
      // connection's own input says when the gateway cannot be reached.
    } finally {
      reading = false
    }
  }

  /** A burst of rings in one turn of the event loop is one read. */
  let rung = false
  function ring() {
    if (stopped || rung) return
    rung = true
    queueMicrotask(() => {
      rung = false
      void read()
    })
  }

  apply({ type: 'enabled', on: true })
  publish()

  return {
    send(input) {
      apply(input)
      publish()
    },
    ring,
    async answer(id, choice, opts = {}) {
      const item = items.get(id)
      if (!item || machine.shown !== id) return false
      if (isStale(item, deps.now())) {
        apply({ type: 'resolved', id, how: 'expired' })
        publish()
        return false
      }
      if (opts.reply) replies.set(id, opts.reply)
      sending = null
      apply({ type: 'answer', id, choice, confirmed: opts.confirmed })
      publish()
      const sent = sending as Promise<boolean> | null
      sending = null
      if (!sent) replies.delete(id)
      return sent ? sent : false
    },
    hold(on) {
      if (on === held) return
      held = on
      if (on) {
        for (const name of FOLDS) {
          if (!timers.has(name)) continue
          deps.clearTimer(timers.get(name))
          timers.delete(name)
          waitingFolds.add(name)
        }
      } else {
        const resume = [...waitingFolds]
        waitingFolds.clear()
        if (!isHidden(machine)) for (const name of resume) start(name)
      }
    },
    fullScreen(on) {
      if (on === fullScreen) return
      fullScreen = on
      syncQuiet()
      publish()
    },
    snapshot: () => ({ machine, steps, rows, items, beats, read: readOnce }),
    timers: () => timers.size + (goneOnTimer === null ? 0 : 1),
    stop() {
      stopped = true
      for (const name of [...timers.keys()]) clear(name)
      if (goneOnTimer !== null) deps.clearTimer(goneOnTimer)
      goneOnTimer = null
    },
  }
}
