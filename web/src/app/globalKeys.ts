/** Where the global keys go: the next chat that needs the owner (⌘⌥A) and the nth chat in the list
 *  (⌘1 to ⌘9). Pure, so the order each one follows is tested without the shell. */
import { approvalDestination } from './approvalDestination'
import { toLanes, type ApprovalInput, type AttentionInput, type LaneCard } from '../lib/attentionLanes'
import { sessionRecencyMs } from '../lib/epoch'
import type { ChatSessionSummary } from '../lib/api'

/** The route of *card*'s work: where its approval is answered or its question waits. */
function routeOf(card: LaneCard): string | null {
  if (card.origin === 'approval') {
    const href = approvalDestination(card.approval.session || '').href
    return card.approval.session ? href.replace(/^#\//, '') : 'mission-control'
  }
  const session = typeof card.refs?.session === 'string' ? card.refs.session : ''
  return session ? `chat/${encodeURIComponent(session)}` : 'mission-control'
}

/** Where ⌘⌥A goes: the first card of Home's Needs-you order (approvals first, then your turn,
 *  oldest first in each, as `toLanes` sorts them), or `null` when nothing waits on the owner. */
export function nextNeedsYouRoute(items: AttentionInput[], approvals: ApprovalInput[]): string | null {
  const lanes = toLanes(items, approvals)
  const card = lanes['needs-approval'][0] ?? lanes['your-turn'][0]
  return card ? routeOf(card) : null
}

/** The chats ⌘1 to ⌘9 count, in the chat list's own order: the owner's own chats, pinned first,
 *  then the most recent. */
export function chatsInListOrder(sessions: ChatSessionSummary[]): ChatSessionSummary[] {
  return sessions
    .filter((s) => (s.origin ?? 'manual') === 'manual')
    .slice()
    .sort((a, b) => (Number(!!b.pinned) - Number(!!a.pinned)) || (sessionRecencyMs(b) - sessionRecencyMs(a)))
}

/** The route of chat *n* (1 to 9) in the list, or `null` when the list is shorter. */
export function chatRoute(sessions: ChatSessionSummary[], n: number): string | null {
  const s = chatsInListOrder(sessions)[n - 1]
  return s ? `chat/${encodeURIComponent(s.key)}` : null
}
