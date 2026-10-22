import type { WaitingItem, WaitingList } from './api'
import { sentenceStart } from './attentionLanes'
import { BUDDY_LINES, lineFor } from './buddy/copy'
import type { WorkPhase } from './workPhase'
import { waitingAnswerId } from './waitingAnswer'
import { waitingRoute, waitingSource, waitingText } from './waitingList'

// ── A buddy alert's native banner (#3897) ───────────────────────────────────────────────────────
//
// The gateway names a note an alert (`note.alert`: an approval, a question or an error) on the
// path that already decided it may interrupt now and may reach the shell. Its banner says what the
// buddy says: the alert kind's still orb, and one line of what changed from the buddy's own
// templates, at most 120 characters with the work's name cut first. The line alone says what
// changed, so a banner shown without its icon still does. A tap opens the item the alert is about.

/** The alerts a banner shows as the buddy does; `desktop/nativeNotifications.js` reads the same. */
export const ALERT_KINDS = ['approval', 'question', 'error'] as const
export type AlertKind = (typeof ALERT_KINDS)[number]

/** The orb's state each alert shows as its still: waiting with "!", waiting with "?", blocked. */
export const ALERT_STATE: Readonly<Record<AlertKind, WorkPhase>> = {
  approval: 'approval',
  question: 'input',
  error: 'error',
}

/** The banner's title: the design gives a banner its orb and its line, and the line carries the
 *  whole message (#3897, open question 4). */
export const ALERT_TITLE = 'PersonalClaw'

/** A route the shell takes for a tap (`desktop/nativeNotifications.js`'s `ROUTE_RE`). */
const SHELL_ROUTE = /^[a-z0-9][a-z0-9/-]{0,63}$/

type Note = Record<string, unknown>

const text = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')
const firstLine = (v: unknown): string => text(v).split('\n')[0].trim()

/** The alert a note is, or "" for one the gateway did not name. */
export function alertOf(note: Note): AlertKind | '' {
  const a = note.alert
  return typeof a === 'string' && (ALERT_KINDS as readonly string[]).includes(a) ? (a as AlertKind) : ''
}

/** The waiting item *note* is a view of, or null: an approval by the registry id its row names, a
 *  question by the work it names (its loop, its run, its automation) or the chat it is shown in. */
export function itemOfNote(note: Note, list: WaitingList | null | undefined): WaitingItem | null {
  const items = Array.isArray(list?.waiting) ? list.waiting : []
  const approval = text(note.approval)
  if (approval) return items.find((i) => i.kind === 'approval' && waitingAnswerId(i) === approval) ?? null
  const asks = items.filter((i) => i.kind !== 'approval')
  const works = [
    text(note.loop) || text(note.loop_id) ? `loop:${text(note.loop) || text(note.loop_id)}` : '',
    text(note.run_id) ? `workflow:${text(note.run_id)}` : '',
    text(note.trigger_id) ? `trigger:${text(note.trigger_id)}` : '',
  ].filter(Boolean)
  for (const work of works) {
    const found = asks.find((i) => i.work === work)
    if (found) return found
  }
  const session = text(note.session)
  return (session && asks.find((i) => i.session === session)) || null
}

/** The work an alert's line names, by its source's own name, or what the note itself says. */
function workOf(note: Note, alert: AlertKind, item: WaitingItem | null): string {
  const named = item ? waitingSource(item) : ''
  if (named) return sentenceStart(named)
  const label = text(note.source_label)
  if (label) return sentenceStart(label)
  // An error names its work in its title ("Loop failed", "Scheduled job failed"): the line says
  // the stop itself, so the title's own verb goes.
  if (alert === 'error') return firstLine(note.title).replace(/\s+(failed|errored|stopped)$/i, '') || 'Your work'
  return 'Your work'
}

/** The tool an approval's line names: its item's, else the row's own title after its prefix. */
function toolOf(note: Note, item: WaitingItem | null): string {
  const tool = item ? text(item.brief.tool) : ''
  return tool || firstLine(note.title).replace(/^approval needed:\s*/i, '')
}

/** The banner an alert note raises: its title, its line and the route a tap opens. */
export function alertBanner(note: Note, alert: AlertKind, item: WaitingItem | null): { title: string; body: string; route: string } {
  const work = workOf(note, alert, item)
  const parts = alert === 'approval' ? BUDDY_LINES.approval(work, { tool: toolOf(note, item) })
    : alert === 'question' ? BUDDY_LINES.question(work, item ? waitingText(item) : firstLine(note.body) || firstLine(note.title))
      : BUDDY_LINES.error(work, firstLine(note.body))
  const own = item ? waitingRoute(item) : ''
  return {
    title: ALERT_TITLE,
    body: lineFor('notificationBody', parts),
    route: SHELL_ROUTE.test(own) ? own : '',
  }
}
