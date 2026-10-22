import { useEffect, useState } from 'react'
import type { PendingApproval, WaitingItem, WaitingKind } from './api'
import { waitingAnswerId } from './waitingAnswer'

// ── Everything waiting on you, as every surface reads it (`GET /api/waiting`, #3888) ─────────────
//
// The gateway's one list: each item with its source's own words, what answering it lets happen,
// how long it has waited, until when it can be answered and the route its own source answers it
// through, by consequence and then age. A surface keeps that order, shows each item's text, and
// never sends an answer for an item past answering: a card held past its `expires_at` disables its
// action at once (`useStaleAt`), so a click on it sends no request.

/** What each kind is called on a card. */
export const WAITING_KIND_LABEL: Record<WaitingKind, string> = {
  approval: 'Approval',
  question: 'Question',
  loop_question: 'Loop',
  gate: 'Workflow',
  park: 'Automation',
  browse_grant: 'Browser',
  plan_review: 'Plan',
  bridge_confirmation: 'Control bridge',
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

/** The item's text, in its source's own words: what an approval runs, a question's question, a
 *  loop's question, a gate's prompt, a park's question, a grant's task, a plan's step, a
 *  confirmation's ask. Never empty: a source that said nothing is named by its kind. */
export function waitingText(item: Pick<WaitingItem, 'kind' | 'brief'>): string {
  const b = record(item.brief)
  switch (item.kind) {
    case 'approval':
      return text(b.tool_purpose) || text(b.tool) || 'A tool call'
    case 'question': {
      const first = record(Array.isArray(b.questions) ? b.questions[0] : undefined)
      return text(first.question) || text(first.header) || 'A question'
    }
    case 'loop_question':
      return text(b.question) || 'The loop asks you'
    case 'gate': {
      const ask = record(b.ask)
      return text(ask.prompt) || text(ask.title) || text(b.node_id) || 'The run waits at a gate'
    }
    case 'park':
      return text(b.question) || text(record(b.card).blocker) || 'The action stopped for you'
    case 'browse_grant':
      return text(b.task) || 'A task asks to use your browser'
    case 'plan_review': {
      const step = record(b.step)
      return text(step.title) || (text(b.name) ? `Launch “${text(b.name)}”` : 'A plan waits for your review')
    }
    case 'bridge_confirmation':
      return text(b.body) || text(b.action) || 'A control-bridge action waits for you'
  }
}

/** Where it came from, in words: the loop, the workflow, the automation; "" when the item's own
 *  text already says it (a chat's ask is shown in its chat). */
export function waitingSource(item: Pick<WaitingItem, 'kind' | 'brief'>): string {
  const b = record(item.brief)
  if (item.kind === 'approval') return text(b.source_label)
  if (item.kind === 'question') return text(b.asked_by)
  if (item.kind === 'loop_question' || item.kind === 'plan_review') return text(b.name) ? `loop “${text(b.name)}”` : ''
  if (item.kind === 'gate') return text(b.workflow) ? `workflow “${text(b.workflow)}”` : ''
  if (item.kind === 'park') return text(b.trigger_name) ? `automation “${text(b.trigger_name)}”` : ''
  if (item.kind === 'bridge_confirmation') return text(b.asked_by)
  return ''
}

/** The epoch-ms instant *item* stops being answerable, or `null` when it waits until answered. */
export function staleAt(item: Pick<WaitingItem, 'expires_at'>): number | null {
  const at = item.expires_at ? Date.parse(item.expires_at) : NaN
  return Number.isFinite(at) ? at : null
}

/** Whether *item* can no longer be answered at *now* (epoch ms): the gateway said it is not
 *  current, or it is past its `expires_at`. */
export function isStale(item: Pick<WaitingItem, 'expires_at' | 'current'>, now: number): boolean {
  if (item.current === false) return true
  const at = staleAt(item)
  return at !== null && at <= now
}

/** The current time, read again the moment the next of *items* stops being answerable, so a
 *  card's action disables at once rather than at the next read of the list. */
export function useStaleAt(items: readonly Pick<WaitingItem, 'expires_at'>[]): number {
  const [now, setNow] = useState(() => Date.now())
  const next = items
    .map(staleAt)
    .filter((at): at is number => at !== null && at > now)
    .reduce<number | null>((min, at) => (min === null || at < min ? at : min), null)
  useEffect(() => {
    if (next === null) return
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, next - Date.now()) + 50)
    return () => window.clearTimeout(timer)
  }, [next])
  return now
}

/** An approval item as the approval card reads it: the registry row its brief carries, which is
 *  the same words `GET /api/approvals` serves (`waiting.APPROVAL_BRIEF`), under the registry id its
 *  own route names (`waitingAnswerId`). `null` for an item that is no approval, or whose route is
 *  not an approval's: nothing answers it as one. */
export function approvalOf(item: WaitingItem): PendingApproval | null {
  const id = item.kind === 'approval' ? waitingAnswerId(item) : ''
  if (!id) return null
  const b = record(item.brief)
  const asked = item.asked_at ? Date.parse(item.asked_at) : NaN
  return {
    ...(b as Partial<PendingApproval>),
    id,
    request_id: id,
    source: '',
    tool: text(b.tool) || 'a tool',
    session: item.session,
    work: item.work,
    ts: Number.isFinite(asked) ? Math.round(asked / 1000) : 0,
    session_title: text(b.session_title),
    agent: text(b.agent),
    risk: item.risk,
    grant_agent: '',
    interrupt: item.interrupt,
  } as PendingApproval
}

/** The in-app route that opens *item* with its card, as `navigate` takes it (no leading `#/`). */
export function waitingRoute(item: Pick<WaitingItem, 'route'>): string {
  return (item.route || '').replace(/^#\/?/, '')
}
