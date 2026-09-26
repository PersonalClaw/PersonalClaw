import { motion } from 'framer-motion'
import { MessageSquare } from 'lucide-react'
import type { SessionMapEntry } from './sessionMap'
import { SESSION_MAP_MIN_MARKS } from './sessionMap'
import { sessionMapMarkName } from './SessionMapCard'
import { EmptyState } from '../../ui/ListScaffold'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import { spring } from '../../design/motion'

/** SESSION MAP — THE COARSE-POINTER FORM (SEMANTIC-SESSION-MAP §A.8, atom SSM-10).
 *
 *  The rail (SSM-4) is a column of short markers that answer "what is this one?" through a HOVER
 *  card. A touch device has no hover and no marker-sized precision, so on the mobile form the rail
 *  collapses to ONE named control (`ChatPage`'s "Session map" header control) that opens this
 *  drawer — a tappable row per user message, with the card's content inlined into the row because
 *  there is no pointer to reveal it with.
 *
 *  🔑 IT IS NOT A SECOND SESSION MAP. It renders the SAME `SessionMapEntry[]` the rail renders —
 *  one per user message, the owner's rule for both forms — jumps through the SAME `onJumpTo`, and
 *  names each row with the SAME `sessionMapMarkName`, so the two forms cannot disagree about what
 *  the map contains, what an entry is called, or where a tap lands. What differs is only what a
 *  finger can use: a 44px row instead of a short line, and the card's two lines always shown.
 *
 *  🪤 AND IT HAS NO RETURN-TO-NEWEST CONTROL, for the reason SSM-9's test enforces: the map owns
 *  exactly one "back to the newest message" affordance app-wide, and it is the transcript's
 *  (`SessionMapReturnLatest`). A drawer-local "newest" row would be the second.
 *
 *  🪤 NO `<nav>` LANDMARK HERE, unlike the rail. This body is rendered INSIDE `ui/SidePanel`,
 *  which is already a `role="region"` named by its "Session map" title — adding a nav landmark
 *  with the same name inside it would give assistive tech two nested regions with one name for
 *  one surface. The rail needs its landmark because nothing wraps it; this does not.
 */

export interface SessionMapDrawerProps {
  /** The ordered entries from `sessionMapEntries` — the same array the rail receives. */
  entries: SessionMapEntry[]
  /** `ChatPage`'s `jumpToTurn`, handed an entry's coordinate. Identical to the rail's prop, so a
   *  tap and a marker click are the same navigation. */
  onJumpTo: (visibleIndex: number) => void
}

export function SessionMapDrawer({ entries, onJumpTo }: SessionMapDrawerProps) {
  // The same self-suppression threshold the rail applies (§A.1), read from the one place it is
  // defined: a map of fewer than two messages indexes nothing worth navigating. The rail answers
  // this by rendering nothing; a drawer the user deliberately OPENED must say why it is empty
  // instead of showing a blank panel.
  if (entries.length < SESSION_MAP_MIN_MARKS) {
    return <EmptyState icon={MessageSquare} title="Nothing to map yet" hint="Send a few messages — the session map lists each one, so you can jump back to it." />
  }
  return (
    <ul data-session-map-drawer className="flex flex-col gap-px">
      {entries.map((entry, i) => {
        const iso = isoStamp(entry.ts)
        return (
          <li key={entry.markIndex}>
            <motion.button
              type="button"
              data-session-map-row
              // The row's own name is the rail's (§A.6): short, positioned, bounded. Set explicitly
              // because the row's subtree also carries the reply excerpt and a clock, and a name
              // computed from all of that would read the answer as part of the question.
              aria-label={sessionMapMarkName(entries, i)}
              onClick={() => onJumpTo(entry.visibleIndex)}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...spring.spatialDefault, delay: Math.min(i * 0.03, 0.3) }}
              // `min-h-11` is 44px — the touch floor this form exists for, and the reason the
              // rail's pointer-sized row is not reused: a thumb is a bigger target than a cursor.
              className="flex min-h-11 w-full items-start gap-s rounded-md px-2.5 py-2 text-left transition-colors hover:bg-surface-high"
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span data-type="body-s" data-session-map-request className="line-clamp-2 text-on-surface">{entry.preview}</span>
                {entry.response ? (
                  <span data-type="caption" data-session-map-response className="mt-xs line-clamp-1 text-on-surface-var">{entry.response}</span>
                ) : null}
              </span>
              {/* An unreadable stamp renders nothing rather than "Invalid Date" — `lib/epoch`'s
                  contract, the same way `SessionMapCard` inherits it. */}
              {iso ? (
                <time data-type="caption" dateTime={iso} title={fullStamp(entry.ts)} className="shrink-0 tabular-nums text-on-surface-low">
                  {clockTime(entry.ts)}
                </time>
              ) : null}
            </motion.button>
          </li>
        )
      })}
    </ul>
  )
}
