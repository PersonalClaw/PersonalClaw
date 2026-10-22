// ── The live presence's card: the host's snapshot as the expanded card's props (#3832) ──────────
//
// The playground's card (`stageCard.ts`) makes its rows up; this one reads the gateway's. The focused
// work and the work in sight are the running list's rows, and the alert is the waiting item the
// machine shows:
//  - an approval as the phone shows it: its registry row (`approvalOf`), and the phone's Allow and
//    Deny, with the app's words and names (`approvalChoicesOf`), answered through the host on the
//    approval's own route;
//  - an agent's question as the chat's card shows it, its reply sent the same way;
//  - a failure, with Open and Dismiss.
// Each control answers only by the host's `answer`, which sends nothing but for the alert that
// shows and an item still current.

import type { AgentQuestion, RunningRow } from '../../lib/api'
import type { HostSnapshot, BuddyHost } from '../../lib/buddy/host'
import type { Work } from '../../lib/buddy/inputs'
import { focusedWork, viewOf } from '../../lib/buddy/machine'
import { inSight } from '../../lib/buddy/focus'
import { lineOf } from '../../lib/buddy/lines'
import { EMPTY_WINDOW } from '../../lib/buddy/steps'
import { waitingQuestionRef } from '../../lib/waitingAnswer'
import { approvalOf } from '../../lib/waitingList'
import type { BuddyCard, CardAlert, CardAnswer, CardWork } from '../../ui/BuddyCard'
import { approvalChoicesOf } from './stageCard'

type CardProps = Parameters<typeof BuddyCard>[0]

/** A piece of the machine's work with its running row's kind, last update and route. */
function cardWork(w: Work, rows: ReadonlyMap<string, RunningRow>): CardWork {
  const row = rows.get(w.id)
  return { ...w, kind: row?.kind ?? 'chat', since: row?.since ?? null, route: row?.route ?? '' }
}

/** The alert that shows, as the card draws it, or `null`. */
export function liveAlertOf(snap: HostSnapshot, host: Pick<BuddyHost, 'answer'>): CardAlert | null {
  const alert = snap.machine.queue.find((a) => a.id === snap.machine.shown)
  if (!alert) return null
  if (alert.kind === 'error') {
    const row = snap.rows.get(alert.workId)
    return { kind: 'error', work: alert.work, reason: alert.brief, route: row?.route ?? '' }
  }
  const item = snap.items.get(alert.id)
  if (!item) return null
  if (alert.kind === 'approval') {
    const row = approvalOf(item)
    if (!row) return null
    // The phone's two choices, words and names, each answering through the host, which says
    // whether its request landed, as the prompt waits to know. The prompt asks its own second
    // confirmation before its Allow fires, so an Allow that reaches here is confirmed.
    const choices = approvalChoicesOf(row, () => {}).map((c) => {
      const allow = c.key === 'allow'
      return { ...c, onClick: () => host.answer(alert.id, { kind: allow ? 'allow' : 'deny' }, { confirmed: allow }) }
    })
    return { kind: 'approval', row, choices }
  }
  const ref = waitingQuestionRef(item)
  if (!ref) return null
  const brief = item.brief as { questions?: AgentQuestion[]; asked_by?: unknown }
  return {
    kind: 'input',
    question: {
      kind: 'question', id: ref.question, toolCallId: '', askedBy: typeof brief.asked_by === 'string' ? brief.asked_by : '',
      answerable: true, questions: Array.isArray(brief.questions) ? brief.questions : [],
    },
  }
}

/** The card's props for the snapshot as it stands. */
export function liveCardPropsOf(snap: HostSnapshot, host: BuddyHost): CardProps {
  const { machine } = snap
  const view = viewOf(machine)
  const focus = focusedWork(machine)
  const shown = machine.shown
  const onAnswer = (a: CardAnswer) => {
    if (!shown) return
    if (a.kind === 'reply') void host.answer(shown, { kind: 'send', text: '' }, { reply: a.reply })
    else host.send({ type: 'resolved', id: shown, how: 'withdrawn' })
  }
  return {
    focus: focus ? cardWork(focus, snap.rows) : null,
    line: view.truth ? lineOf(view.truth, 'expandedCard') : '',
    steps: (focus && snap.steps[focus.id]) || EMPTY_WINDOW,
    alert: liveAlertOf(snap, host),
    waiting: view.waiting,
    inSight: inSight(machine.live, focus?.id ?? machine.focus).map((w) => cardWork(w, snap.rows)),
    list: machine.connection === 'up' ? 'live' : 'reconnecting',
    countdown: view.countdown ? 10 : undefined,
    openHref: '#/mission-control',
    onAnswer,
    onHold: (held) => host.hold(held),
  }
}
