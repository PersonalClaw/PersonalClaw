// ── The buddy's line, in copy.md's words ───────────────────────────────────────────────────────
//
// The state machine (`machine.ts`) says what the line says as data, a `Truth`, so it stays free of
// the copy module's imports; this puts a truth in the words `copy.ts` holds for it, cut to a
// surface. Presence never changes the words: one truth gives one line, which a surface only cuts
// (invariant 9).

import { BUDDY_LINES, lineFor, type BuddySurface, type LineParts } from './copy'
import type { Truth } from './machine'
import { secondsBetween } from './timing'

/** What the host knows that the line needs and the machine does not: the push-to-talk chord. */
export interface LineContext { chord?: string }

/** A truth's parts, in its template's words. */
export function partsOf(t: Truth, { chord = '' }: LineContext = {}): LineParts {
  switch (t.kind) {
    case 'phase': {
      const { work } = t
      switch (t.phase) {
        case 'queued': return BUDDY_LINES.queued(work.name)
        case 'thinking': return BUDDY_LINES.thinking(work.name)
        case 'searching': return BUDDY_LINES.searching(work.name, work.line)
        case 'tool': return BUDDY_LINES.tool(work.name, work.line)
        case 'planning': return BUDDY_LINES.planning(work.name)
        case 'delegating': return BUDDY_LINES.delegating(work.name, work.helpers ?? 1)
        case 'streaming': return BUDDY_LINES.streaming(work.name)
        case 'listening': return BUDDY_LINES.listening(chord)
        case 'approval': return BUDDY_LINES.approval(work.name, { tool: work.line })
        case 'input': return BUDDY_LINES.question(work.name, work.line)
        case 'done': return BUDDY_LINES.done(work.name, undefined)
        case 'error': return BUDDY_LINES.error(work.name, work.line)
      }
      break
    }
    case 'alert': {
      const { alert } = t
      if (alert.kind === 'approval') return BUDDY_LINES.approval(alert.work, { tool: alert.brief })
      if (alert.kind === 'input') return BUDDY_LINES.question(alert.work, alert.brief)
      return BUDDY_LINES.error(alert.work, alert.brief)
    }
    case 'listening': return BUDDY_LINES.listening(chord)
    case 'done':
      return t.count > 1
        ? BUDDY_LINES.severalDone(t.count)
        : BUDDY_LINES.done(t.work.name, t.at > t.work.startedAt ? secondsBetween(t.work.startedAt, t.at) : undefined)
    case 'idle': return BUDDY_LINES.idle()
    case 'restarting': return BUDDY_LINES.restarting()
    case 'offline': return BUDDY_LINES.offline(t.since)
    case 'recovery': return BUDDY_LINES.recovery()
    case 'elsewhere': return BUDDY_LINES.answeredElsewhere()
    case 'expired': return BUDDY_LINES.expired(t.work)
    case 'returnSummary': return BUDDY_LINES.returnSummary(t.done, t.needYou)
  }
  return BUDDY_LINES.idle()
}

/** A truth's line on `surface`, cut to its limit. */
export function lineOf(t: Truth, surface: BuddySurface = 'expandedCard', context: LineContext = {}): string {
  return lineFor(surface, partsOf(t, context))
}
