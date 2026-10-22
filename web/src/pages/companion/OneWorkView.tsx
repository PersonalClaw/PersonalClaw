import { useEffect, useRef, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { api, type AgentQuestion, type PendingApproval, type RunningRow, type WaitingItem } from '../../lib/api'
import { BUDDY_LINES, lineFor } from '../../lib/buddy/copy'
import { alertOf, createBuddyHost, LIVE_DEPS, workOf, type BuddyHost, type HostSnapshot } from '../../lib/buddy/host'
import { lineOf } from '../../lib/buddy/lines'
import { EMPTY_WINDOW } from '../../lib/buddy/steps'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { waitingQuestionRef } from '../../lib/waitingAnswer'
import { approvalOf, waitingText } from '../../lib/waitingList'
import type { QuestionReply } from '../chat/QuestionCard'
import { approvalChoicesOf } from '../buddy/stageCard'
import { BuddyCard, type CardAlert, type CardAnswer, type CardWork } from '../../ui/BuddyCard'
import { Button } from '../../ui/Button'
import { orbIdOf, waitingOrbState, waitingWorkName, workKindOf } from './allWork'

// ── View two: one piece of work owning the screen (#3898) ────────────────────────────────────
//
// The buddy's expanded card for the one piece of work the owner opened from view one: its orb
// large, its exact phase, its run as the three-line step window, its alert with the app's own
// prompt and answer controls, and Open in PersonalClaw for its own route. The orb is the shared
// element between the two views (`orbIdOf`), so it moves and scales into its place while the rest
// enters around it; under reduced motion the switch is instant (`Morph`).
//
// An approval is answered here as on view one, through `api.resolveApproval` on its own route; an
// agent's question through the chat's own answer route. Every other kind opens its own page, where
// it is answered with its brief, as the buddy's card does.

/** The frames that say a list moved: the doorbell the buddy's host reads again on. */
const RINGS = new Set(['running_changed', 'waiting_changed'])

/** What view two is about: the ask the owner opened, or the work. */
export type Opened = { item: WaitingItem; row: RunningRow | null } | { item: null; row: RunningRow }

/** The step window of *work*, folded by the buddy's own host from the running list's moves while
 *  this view is open: the same window the desktop's card draws. */
function useSteps(work: string) {
  const [snap, setSnap] = useState<HostSnapshot | null>(null)
  const hostRef = useRef<BuddyHost | null>(null)
  useEffect(() => {
    const host = createBuddyHost({
      ...LIVE_DEPS,
      now: () => Date.now(),
      setTimer: (fire, ms) => window.setTimeout(fire, ms),
      clearTimer: (handle) => window.clearTimeout(handle as number),
    }, setSnap)
    hostRef.current = host
    host.ring()
    return () => {
      host.stop()
      hostRef.current = null
    }
  }, [])
  useChatSocket((m: WsMessage) => { if (RINGS.has(m.type)) hostRef.current?.ring() }, () => hostRef.current?.ring())
  return snap?.steps[work] ?? EMPTY_WINDOW
}

/** The work view two focuses: its running row, or the ask's own words when no row lists it. */
function focusOf(opened: Opened, rows: ReadonlyMap<string, RunningRow>, now: number): CardWork {
  const { item, row } = opened
  if (row) return { ...workOf(row, now), kind: row.kind, since: row.since, route: row.route }
  // The type rules this out; a render path never throws for it.
  if (!item) return { id: '', name: 'PersonalClaw', phase: 'idle', line: '', startedAt: now, kind: 'chat', since: null, route: '' }
  const asked = item.asked_at ? Date.parse(item.asked_at) : NaN
  return {
    id: item.work || item.id,
    name: waitingWorkName(item, rows),
    phase: waitingOrbState(item),
    line: waitingText(item),
    startedAt: Number.isFinite(asked) ? asked : now,
    kind: workKindOf(item.work) ?? 'chat',
    since: item.asked_at,
    route: item.route,
  }
}

/** The ask as the card shows it, answered on its own route; null for a kind the card does not
 *  answer, which opens its own page. */
function alertCardOf(item: WaitingItem, decide: (ap: PendingApproval, action: 'approve' | 'reject') => void): CardAlert | null {
  const row = item.kind === 'approval' ? approvalOf(item) : null
  if (row) {
    const choices = approvalChoicesOf(row, (a) => decide(row, a.kind === 'allow' ? 'approve' : 'reject'))
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

/** The card's one line: the alert's, or the work's phase, in the expanded card's room. */
function lineOfOpened(opened: Opened, focus: CardWork, now: number): string {
  const { item } = opened
  if (!item) return lineOf({ kind: 'phase', phase: focus.phase, work: focus }, 'expandedCard')
  const alert = alertOf(item, focus.name, now)
  if (alert) return lineOf({ kind: 'alert', alert }, 'expandedCard')
  return lineFor('expandedCard', BUDDY_LINES.question(focus.name, waitingText(item)))
}

export function OneWorkView({ opened, rows, onBack, onDecide, onAnswered }: {
  opened: Opened
  rows: ReadonlyMap<string, RunningRow>
  onBack: () => void
  /** An approval's Allow or Deny, sent as view one sends it. */
  onDecide: (ap: PendingApproval, action: 'approve' | 'reject') => void
  /** An answer landed: the lists are read again. */
  onAnswered: () => void
}) {
  const now = Date.now()
  const focus = focusOf(opened, rows, now)
  const steps = useSteps(focus.id)
  const key = opened.item ? opened.item.id : opened.row.work
  const alert = opened.item ? alertCardOf(opened.item, onDecide) : null
  const ref = opened.item ? waitingQuestionRef(opened.item) : null
  const onAnswer = (a: CardAnswer) => {
    if (a.kind !== 'reply' || !ref) return
    void api.answerChatQuestion(ref.session, ref.question, a.reply as QuestionReply).then(onAnswered, (e: unknown) => {
      window.dispatchEvent(new CustomEvent('ne:toast', {
        detail: { level: 'error', message: `Couldn't send that answer: ${(e as Error)?.message || 'the gateway did not respond'}` },
      }))
    })
  }
  // Focus lands on the view's wrapper as it opens, a deep link's among it: the prompt is announced
  // and Allow is a Tab away, never one stray Enter away on a phone that just woke in a pocket.
  const wrapper = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = wrapper.current
    // Opened from a row lower down the list, or by a push into a scrolled page: the view starts
    // in sight.
    el?.scrollIntoView?.({ block: 'start' })
    el?.focus?.()
  }, [key])
  return (
    <div ref={wrapper} tabIndex={-1} role="region" aria-label={focus.name} className="flex min-w-0 flex-col gap-m rounded-lg" data-companion-view="one">
      <div>
        <Button variant="ghost" size="lg" onClick={onBack} ariaLabel="Back to everything">
          <ArrowLeft size={16} aria-hidden /> Everything
        </Button>
      </div>
      <BuddyCard
        focus={focus}
        line={lineOfOpened(opened, focus, now)}
        state={opened.item ? waitingOrbState(opened.item) : focus.phase}
        steps={steps}
        alert={alert}
        inSight={null}
        openHref={focus.route || '#/dashboard'}
        orbId={orbIdOf(key)}
        orbSize={96}
        onAnswer={onAnswer}
      />
    </div>
  )
}
