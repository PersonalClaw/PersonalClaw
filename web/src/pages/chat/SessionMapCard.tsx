import type { SessionMapEntry } from './sessionMap'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import { rowSubject } from '../../lib/rowSubject'

/** SESSION MAP PREVIEW CARD — what a marker says when you point at it
 *  (SEMANTIC-SESSION-MAP §A.3, atom SSM-6).
 *
 *  A marker is a short line: it says WHERE a message is in the session and nothing about WHAT it
 *  was. The card answers that, in the form of the owner's reference (2026-09-25): the user's
 *  request, truncated, then a MUTED excerpt of the beginning of the reply — plus a small timestamp,
 *  which the reference lacked and the owner asked for.
 *
 *   · the REQUEST in `--color-on-surface` — deliberately NOT `--color-on-surface-low`, which is the
 *     "very low contrast metadata" the reference was faulted for. The dimmed ramp is for chrome (the
 *     clock time), never for the sentence you came to read.
 *   · the REPLY EXCERPT under a hairline divider, muted in `--color-on-surface-var` — muted, and
 *     still text-legible on the card, which is what `e2e/a11y.spec.ts` sweeps with the card open.
 *   · NO ROLE LABEL. Every entry IS a user message and its excerpt IS the reply to it
 *     (`sessionMapEntries`), so the roles are implicit in the map's shape — a "You" on every card
 *     would label the one thing the map never varies.
 */

/** How much of a message may become part of a marker's accessible NAME. The 40-char budget is
 *  `lib/rowSubject`'s row-scoped one (`design/computedNames.test.tsx` measured the app-wide cap): a
 *  name must be DISTINGUISHING and BOUNDED, and a rail of markers is a dense row list. */
const NAME_SUBJECT_CAP = 40

/** The accessible NAME of a marker (§A.6, and the owner's reference-friction list): its position
 *  among the user's messages plus a bounded piece of the message — "Message 12 of 40: {first
 *  words}", never the full text. The rail's marker, the drawer's row and the rail's jump
 *  announcement all read this position, so the two forms cannot number a message differently. */
export function sessionMapMarkName(entries: SessionMapEntry[], index: number): string {
  return `Message ${index + 1} of ${entries.length}: ${rowSubject([entries[index].preview], NAME_SUBJECT_CAP)}`
}

export interface SessionMapCardProps {
  /** The entry being previewed. */
  entry: SessionMapEntry
  /** The pointer arrived on the card itself: cancel the close the trigger's mouse-leave armed,
   *  so the pointer can cross the gap and read (or select) the excerpt. */
  onMouseEnter: () => void
  /** The pointer left the card: dismiss it. */
  onMouseLeave: () => void
}

/** The card body. Rendered INSIDE `ui/Popover`'s flyout, so the frosted `.glass` material, the
 *  `--radius-lgi` corner, the `overlayEnter` spring, the viewport clamp, `z-[var(--z-menu)]`,
 *  single-layer Escape and focus-restore-on-dismiss all come from the shared primitive (§A.3) —
 *  this owns only what is inside it.
 *
 *  NOT `aria-hidden`. The card carries strictly MORE than its trigger's accessible name (a
 *  timestamp and the reply excerpt), and hiding real content from assistive tech to dodge a
 *  double-read would be trading a whole surface for a duplicated phrase. §A.6 puts the
 *  announcement policy on an `aria-live` region that speaks only while focused, which is where
 *  the keyboard layer (SSM-7) resolves double-speak — the card does not pre-empt it here.
 */
export function SessionMapCard({ entry, onMouseEnter, onMouseLeave }: SessionMapCardProps) {
  const iso = isoStamp(entry.ts)
  return (
    <div data-session-map-card onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
      {/* An unreadable stamp renders NOTHING rather than a placeholder — `lib/epoch`'s contract,
          so a message with no `ts` shows a card with no clock instead of "Invalid Date". */}
      {iso ? (
        <time
          data-type="caption"
          data-session-map-time
          dateTime={iso}
          title={fullStamp(entry.ts)}
          className="block tabular-nums text-on-surface-low"
        >
          {clockTime(entry.ts)}
        </time>
      ) : null}
      <p data-type="body-s" data-session-map-request className={`${iso ? 'mt-xs ' : ''}line-clamp-2 text-on-surface`}>
        {entry.preview}
      </p>
      {entry.response ? (
        <p
          data-type="caption"
          data-session-map-response
          className="mt-s line-clamp-3 border-t border-outline-variant pt-s text-on-surface-var"
        >
          {entry.response}
        </p>
      ) : null}
    </div>
  )
}
