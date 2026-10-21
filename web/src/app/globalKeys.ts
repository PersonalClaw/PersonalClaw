/** Where ⌘⌥A goes: the next chat that needs the owner. Pure, so the order it follows is tested
 *  without the shell. ⌘1 to ⌘9 open the sidebar's threads (`threadNumberRoute`, `threadRail.tsx`). */
import { approvalDestination } from './approvalDestination'
import { toLanes, type ApprovalInput, type AttentionInput, type LaneCard } from '../lib/attentionLanes'

/** The route of *card*'s work: where its approval is answered or its question waits. */
function routeOf(card: LaneCard): string | null {
  if (card.origin === 'approval') {
    const href = approvalDestination(card.approval).href
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
