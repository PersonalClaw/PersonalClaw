// ── The playground's card: the machine's state as the expanded card's props (#3893) ────────────
//
// The stage draws the real card (`ui/BuddyCard.tsx`) from the real machine. What the card reads
// beside the machine, the running list's kind, last update and route for each piece of work and
// the waiting list's row for an alert, is made up here from the machine's own fields, as the rest
// of the playground's inputs are. No route is given, so nothing on the stage leaves the page; the
// Card tab's fixtures (`cardFixtures.ts`) carry routes.
//
// The card wires no Allow or Deny: the host passes an approval's choices. The playground's are the
// app's words and names, styled as the chat's approval card styles them (neither is the primary
// tier), and their answer is the machine's answer input, which the log shows and never sends.

import { Ban, Check } from 'lucide-react'
import type { PendingApproval, WorkKind } from '../../lib/api'
import type { Alert, Work } from '../../lib/buddy/inputs'
import { focusedWork, viewOf, type BuddyState } from '../../lib/buddy/machine'
import { inSight } from '../../lib/buddy/focus'
import { lineOf } from '../../lib/buddy/lines'
import { EMPTY_WINDOW, type StepWindows } from '../../lib/buddy/steps'
import type { QuestionSegment } from '../chat/chatTypes'
import type { ApprovalChoice } from '../../ui/ApprovalPrompt'
import type { BuddyCard, CardAlert, CardAnswer, CardWork } from '../../ui/BuddyCard'

/** The kinds the playground's work takes, in turn, so the card shows each kind's glyph. */
const KINDS: readonly WorkKind[] = ['chat', 'loop', 'workflow', 'subagent']

/** A moment the made-up work last changed at: the playground's clock from a fixed day. */
const DAY = Date.UTC(2026, 9, 21, 9, 0, 0)

export function cardWorkOf(w: Work): CardWork {
  const n = Number(w.id.replace(/\D/g, '')) || 0
  return { ...w, kind: KINDS[n % KINDS.length], since: new Date(DAY + w.startedAt).toISOString(), route: '' }
}

/** The waiting list's row for a made-up approval: its tool is the alert's brief. */
export function approvalRowOf(a: Alert): PendingApproval {
  return {
    id: a.id, request_id: a.id, source: 'playground', tool: a.brief, tool_input: '', tool_purpose: '',
    session: '', ts: Math.round((DAY + a.raisedAt) / 1000), session_title: '', agent: '', risk: '', grant_agent: '',
    source_label: a.work, confirms: a.broadReach ? ['deletes'] : [],
  }
}

/** The chat's question segment for a made-up question: the alert's brief, answered in words. */
export function questionOf(a: Alert): QuestionSegment {
  return {
    kind: 'question', id: a.id, toolCallId: '', askedBy: a.work, answerable: true,
    questions: [{ question: a.brief, header: '', multiSelect: false, free_text: true, options: [{ label: 'main', description: '' }, { label: 'next', description: '' }] }],
  }
}

/** Everything the stage's card raises: the playground's Allow and Deny, and the card's own answers. */
export type StageAnswer = CardAnswer | { kind: 'allow' } | { kind: 'deny' }

/** The playground's Allow and Deny for `row`: the app's words and names (`Allow <tool>`), neither
 *  the primary tier, answering through `onAnswer`. */
export function approvalChoicesOf(row: PendingApproval, onAnswer: (a: StageAnswer) => void): ApprovalChoice[] {
  return [
    { key: 'allow', icon: Check, label: 'Allow', approves: true, name: `Allow ${row.tool}`, onClick: () => onAnswer({ kind: 'allow' }) },
    { key: 'deny', icon: Ban, label: 'Deny', tone: 'danger', name: `Deny ${row.tool}${row.deny_effect ? `. ${row.deny_effect}` : ''}`, onClick: () => onAnswer({ kind: 'deny' }) },
  ]
}

export function cardAlertOf(a: Alert, onAnswer: (a: StageAnswer) => void): CardAlert {
  if (a.kind === 'approval') {
    const row = approvalRowOf(a)
    return { kind: 'approval', row, choices: approvalChoicesOf(row, onAnswer) }
  }
  if (a.kind === 'input') return { kind: 'input', question: questionOf(a) }
  return { kind: 'error', work: a.work, reason: a.brief, route: '' }
}

type CardProps = Parameters<typeof BuddyCard>[0]

/** The card's props for the machine as it stands: the focused work and its steps, the alert that
 *  shows, the work in sight, and the countdown while it is about to fold. */
export function cardPropsOf(machine: BuddyState, steps: StepWindows, onAnswer: (a: StageAnswer) => void): CardProps {
  const view = viewOf(machine)
  const focus = focusedWork(machine)
  const alert = machine.queue.find((a) => a.id === machine.shown) ?? null
  return {
    focus: focus ? cardWorkOf(focus) : null,
    line: view.truth ? lineOf(view.truth, 'expandedCard') : '',
    steps: (focus && steps[focus.id]) || EMPTY_WINDOW,
    alert: alert ? cardAlertOf(alert, onAnswer) : null,
    waiting: view.waiting,
    inSight: inSight(machine.live, focus?.id ?? machine.focus).map(cardWorkOf),
    list: machine.connection === 'up' ? 'live' : 'reconnecting',
    countdown: view.countdown ? 10 : undefined,
    openHref: '#/mission-control',
    orbId: 'buddy-playground-orb',
    onAnswer,
  }
}
