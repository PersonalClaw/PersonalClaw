import { useState } from 'react'
import { PowerOff, CalendarX2, Play, X } from 'lucide-react'
import { api, type TriggerReviewCard } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { failureSentence } from '../../app/reportingWrite'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { TextLink } from '../../ui/TextLink'
import { InlineLoadError } from '../../ui/ListScaffold'
import { relPast } from './triggerMeta'

/** What a restart left for you to decide, above the list (`GET /api/triggers/review`).
 *
 *  Two kinds of card, one decision each. MISSED: a schedule's slots that did not run while
 *  PersonalClaw was stopped. INTERRUPTED: a run a restart cut off. Neither runs on its own —
 *  running a 3am job at 9am is sometimes right and sometimes exactly wrong, and an interrupted run
 *  may already have done part of its work — so each card offers Run now (once, however many slots
 *  it covers) and Dismiss, and either choice is a row in that automation's history.
 *
 *  This is what the "Missed scheduled runs" notice has always pointed at. Before it, the notice said
 *  "Review them and choose what to run now" over a page with nothing to review. */
export function TriggerReview({ cards, error, onRetry, onDecided, onOpen }: {
  cards: TriggerReviewCard[]
  /** The review read failed. Said, not hidden: an empty slot would read as "nothing is waiting"
   *  on the page the "Missed scheduled runs" notice sent you to. */
  error?: unknown
  onRetry: () => void
  /** Re-read the review and the lists after a decision lands. */
  onDecided: () => void
  onOpen: (openId: string) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  if (error) {
    return (
      <div className="mb-m" data-testid="trigger-review-error">
        <InlineLoadError what="the runs waiting for you after a restart" error={error} onRetry={onRetry} />
      </div>
    )
  }
  if (cards.length === 0) return null

  const decide = async (card: TriggerReviewCard, action: 'run_now' | 'dismiss') => {
    const key = `${card.trigger_id}|${card.kind}|${action}`
    setBusy(key)
    try {
      const res = await api.decideTriggerReview({ trigger_id: card.trigger_id, kind: card.kind, action })
      if (res.refused) notify(`${card.name} was not run: ${res.refused}`, 'error')
      else if (action === 'dismiss') notify(`Dismissed. ${card.name}'s history records that you chose not to run it.`, 'success')
      // "Notes", not "records as": the row's status is what the action reported (a workflow it
      // only started reads `launched`), and its summary is what says the run stood in for another.
      else if (res.ok) {
        notify(card.kind === 'interrupted'
          ? `${card.name} ran again. Its history notes that a restart interrupted the first run.`
          : `${card.name} ran now. Its history notes that it ran late.`, 'success')
      }
      // The Run button's own words for the failure (`result` is "failed: <why>"), as its panel shows them.
      else notify(res.result ? `${card.name}: ${res.result}` : `${card.name} did not run.`, 'error')
    } catch (e) {
      notify(failureSentence(action === 'dismiss' ? `dismiss ${card.name}` : `run ${card.name}`, e), 'error')
    } finally {
      setBusy(null)
      onDecided()
    }
  }

  return (
    <section aria-labelledby="trigger-review-heading" data-testid="trigger-review" className="mb-l rounded-lg border border-outline-variant/50 bg-surface-container px-l py-m">
      <Eyebrow as="h2" id="trigger-review-heading">Waiting for you after a restart</Eyebrow>
      <p data-type="body-s" className="mt-xs text-on-surface-var">
        These did not run while PersonalClaw was stopped or restarting, and none of them runs on its own. Run each now, or dismiss it.
      </p>
      <ul className="mt-m flex flex-col gap-s">
        {cards.map((card) => {
          const Icon = card.kind === 'interrupted' ? PowerOff : CalendarX2
          const running = busy === `${card.trigger_id}|${card.kind}|run_now`
          const dismissing = busy === `${card.trigger_id}|${card.kind}|dismiss`
          return (
            <li key={`${card.trigger_id}|${card.kind}`} data-testid="trigger-review-card"
              className="flex flex-wrap items-start gap-m rounded-md bg-surface px-m py-s">
              <Icon size={16} aria-hidden className="mt-0.5 shrink-0 text-warning" />
              <div className="min-w-0 flex-1">
                <TextLink onClick={() => onOpen(card.open_id)} size="sm" ink="emphasis">{card.name}</TextLink>
                <p data-type="body-s" className="text-on-surface-var">{reviewSentence(card)}</p>
              </div>
              <div className="flex shrink-0 items-center gap-s">
                <Button size="sm" onClick={() => void decide(card, 'run_now')} loading={running} loadingLabel="Running…"
                  disabled={busy !== null} disabledReason={busy !== null && !running ? 'Another decision is being recorded' : undefined}>
                  <Play size={14} aria-hidden /> Run now
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void decide(card, 'dismiss')} loading={dismissing} loadingLabel="Dismissing…"
                  disabled={busy !== null} disabledReason={busy !== null && !dismissing ? 'Another decision is being recorded' : undefined}>
                  <X size={14} aria-hidden /> Dismiss
                </Button>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/** The card's one sentence: what did not happen, when, and why it is waiting for you. */
export function reviewSentence(card: TriggerReviewCard): string {
  if (card.kind === 'interrupted') {
    return `A run was interrupted by a restart ${relPast(card.latest)}. It is not run again on its own, because it may already have done part of its work.`
  }
  const n = card.count
  const many = `${card.count_is_floor ? 'at least ' : ''}${n} scheduled run${n === 1 ? '' : 's'}`
  return n === 1
    ? `Missed ${many} ${relPast(card.latest)}, while PersonalClaw was not running.`
    : `Missed ${many} while PersonalClaw was not running; the latest was ${relPast(card.latest)}. Run now runs it once.`
}
