/** Where ⌘⌥A goes: the next thing that needs the owner. Pure, so the order it follows is tested
 *  without the shell. ⌘1 to ⌘9 open the sidebar's threads (`threadNumberRoute`, `threadRail.tsx`). */
import { needsYou } from '../lib/needsYou'
import type { NeedsYouRead } from '../lib/useNeedsYou'
import { waitingRoute } from '../lib/waitingList'
import { SOURCE_WORDS } from '../pages/dashboard/NeedCards'

/** Where ⌘⌥A goes: the first row of Needs you, in its order (`GET /api/waiting`'s, then what there
 *  is to review), to the route where it is answered: a chat's ask to its chat, a run's step to its
 *  run. A row answered on Needs you itself (a browser grant, a proposal, a note) opens there, with
 *  its panel open. `null` when nothing is listed. */
export function nextNeedsYouRoute(read: Pick<NeedsYouRead, 'waiting' | 'items' | 'proposals'>): string | null {
  const [first] = needsYou(read.waiting, read.items, read.proposals)
  if (!first) return null
  const own = first.origin === 'waiting' ? waitingRoute(first.item) : ''
  if (own && own.split('?')[0] !== 'mission-control') return own
  return `mission-control?open=${encodeURIComponent(first.key)}`
}

/** What ⌘⌥A says with nothing listed while a source could not be read: something may be waiting
 *  there. '' when every source was read. */
export function unreadNeedsYou(read: Pick<NeedsYouRead, 'waiting'>): string {
  const raw: unknown = read.waiting?.unreadable
  const kinds = Array.isArray(raw) ? raw.filter((k): k is string => typeof k === 'string') : []
  if (kinds.length === 0) return ''
  return `Couldn't read ${kinds.map((k) => SOURCE_WORDS[k] ?? k).join(', ')}, so something may be waiting.`
}
