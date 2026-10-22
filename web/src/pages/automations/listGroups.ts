import type { AutomationGroup, ReportsTo } from '../../lib/api'

/** The Automations list's groups, in the order it shows them, each with its heading (#3816). The
 *  route places every row (`triggers/listing.py`); the page lays the groups out and never moves a
 *  row between them. */
export const LIST_GROUPS: ReadonlyArray<{ readonly id: AutomationGroup; readonly heading: string }> = [
  { id: 'needs_you', heading: 'Needs you' },
  { id: 'next_to_run', heading: 'Next to run' },
  { id: 'not_on_a_clock', heading: 'Not on a clock' },
]

/** One group's rows, as the page lays it out. */
export interface PlacedGroup<T> {
  readonly id: AutomationGroup
  readonly heading: string
  readonly rows: readonly T[]
}

/** *rows* in the route's order, under their groups' headings, an empty group left out. The order is
 *  each row's served place in the whole list (`rank`), so the same rows read in another order lay
 *  out the same. Rows from a gateway that places none come back as one group with no heading, in
 *  the order it sent them. */
export function placeRows<T extends { group?: AutomationGroup; rank?: number }>(rows: readonly T[]): PlacedGroup<T>[] {
  if (!rows.some((r) => r.group)) return rows.length ? [{ id: 'not_on_a_clock', heading: '', rows }] : []
  const served = [...rows].sort((a, b) => (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER))
  return LIST_GROUPS
    .map((g) => ({ ...g, rows: served.filter((r) => (r.group ?? 'not_on_a_clock') === g.id) }))
    .filter((g) => g.rows.length > 0)
}

/** Where a row's results go, in the words its row says it: the thread it reports to, the chat
 *  channel, the Inbox, or "Silent" for one that sends nothing on its own, the word its panel's chip
 *  uses. `''` for a row that says nothing about it. */
export function reportsToLabel(to: ReportsTo | undefined): string {
  if (!to || !('kind' in to)) return ''
  switch (to.kind) {
    case 'thread': return to.title ? `Reports to “${to.title}”` : 'Reports to its thread'
    case 'channel': return `Reports to ${to.channel.charAt(0).toUpperCase()}${to.channel.slice(1)}`
    case 'inbox': return 'Reports to the Inbox'
    case 'none': return 'Silent'
    default: return ''
  }
}
