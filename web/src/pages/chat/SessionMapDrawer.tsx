import { motion } from 'framer-motion'
import { MessageSquare } from 'lucide-react'
import type { SessionMark, SessionMarkKind } from './sessionMap'
import { SESSION_MAP_MIN_MARKS } from './sessionMap'
import { sessionMapMarkName } from './SessionMapCard'
import { EmptyState } from '../../ui/ListScaffold'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import { spring } from '../../design/motion'

/** SESSION MAP — THE COARSE-POINTER FORM (SEMANTIC-SESSION-MAP §A.8, atom SSM-10).
 *
 *  The rail (SSM-4) is a column of 4px ticks that answer "what is this one?" through a HOVER
 *  card. A touch device has no hover and no 4px precision, so on the mobile form the rail
 *  collapses to ONE named control (`ChatPage`'s "Session map" header control) that opens this
 *  drawer — a tappable row per mark, with the card's content inlined into the row because
 *  there is no pointer to reveal it with.
 *
 *  🔑 IT IS NOT A SECOND SESSION MAP. It renders the SAME `SessionMark[]` the rail renders,
 *  jumps through the SAME `onJumpTo`, and names each row with the SAME `sessionMapMarkName`
 *  (SSM-6's naming rule) — so the two forms cannot disagree about what the map contains, what
 *  a mark is called, or where a tap lands. What differs is only what a finger can use: a
 *  44px row instead of a 4px tick, words instead of tone, and no hover layer at all.
 *
 *  🪤 AND IT HAS NO RETURN-TO-NEWEST CONTROL, for the reason SSM-9's test enforces: the map
 *  owns exactly one "back to the newest turn" affordance app-wide, and it is the transcript
 *  pill (`SessionMapReturnLatest`). A drawer-local "newest" row would be the second.
 *
 *  🪤 NO `<nav>` LANDMARK HERE, unlike the rail. This body is rendered INSIDE `ui/SidePanel`,
 *  which is already a `role="region"` named by its "Session map" title — adding a nav landmark
 *  with the same name inside it would give assistive tech two nested regions with one name for
 *  one surface. The rail needs its landmark because nothing wraps it; this does not.
 */

/** The seven marks in words. The rail encodes kind as TONE, which a drawer row cannot borrow:
 *  it has no current-region observer (the rail is not mounted in this form), so the two-tone
 *  accent/history vocabulary has no input here — and inventing per-kind colour would mint a
 *  third mark vocabulary for the same closed set. Words carry the distinction to every user,
 *  including the one using a screen reader, and need no contrast budget. */
const KIND_LABEL: Record<SessionMarkKind, string> = {
  user: 'You',
  assistant: 'Assistant',
  tool: 'Tool',
  approval: 'Approval',
  error: 'Error',
  subagent: 'Subagent',
  activity: 'Activity',
}

export interface SessionMapDrawerProps {
  /** The ordered marks from `sessionMapMarks` (SSM-1) — the same array the rail receives. */
  marks: SessionMark[]
  /** `ChatPage`'s `jumpToTurn`, handed a mark's coordinate. Identical to the rail's prop, so a
   *  tap and a tick-click are the same navigation. */
  onJumpTo: (visibleIndex: number) => void
}

export function SessionMapDrawer({ marks, onJumpTo }: SessionMapDrawerProps) {
  // The same self-suppression threshold the rail applies (§A.1), read from the one place it is
  // defined: a map of fewer than two marks indexes nothing worth navigating. The rail answers
  // this by rendering nothing; a drawer the user deliberately OPENED must say why it is empty
  // instead of showing a blank panel.
  if (marks.length < SESSION_MAP_MIN_MARKS) {
    return <EmptyState icon={MessageSquare} title="Nothing to map yet" hint="Send a message — the session map indexes each turn and the tool calls, approvals and errors inside it." />
  }
  return (
    <ul data-session-map-drawer className="flex flex-col gap-px">
      {marks.map((mark, i) => {
        const iso = isoStamp(mark.ts)
        return (
          <li key={mark.markIndex}>
            <motion.button
              type="button"
              data-session-map-row
              data-kind={mark.kind}
              // The row's own name is the rail's (§A.6): short, turn-scoped, bounded. Set
              // explicitly because the row's subtree also carries the kind word and a clock,
              // and a name computed from that reads "Tool 14:02 npm run build" backwards.
              aria-label={sessionMapMarkName(marks, i)}
              onClick={() => onJumpTo(mark.visibleIndex)}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...spring.spatialDefault, delay: Math.min(i * 0.03, 0.3) }}
              // `min-h-11` is 44px — the touch floor this form exists for, and the reason the
              // rail's `.hit-24-x` band is not reused: that utility grows a HAIRLINE to the
              // 24px pointer minimum, which is a different (and smaller) target than a thumb.
              className="flex min-h-11 w-full items-baseline gap-s rounded-md px-2.5 py-2 text-left transition-colors hover:bg-surface-high"
            >
              <span data-type="label-s" data-session-map-row-kind className="w-16 shrink-0 text-on-surface-var">
                {KIND_LABEL[mark.kind]}
              </span>
              <span data-type="body-s" className="min-w-0 flex-1 line-clamp-2 text-on-surface">{mark.preview}</span>
              {/* An unreadable stamp renders nothing rather than "Invalid Date" — `lib/epoch`'s
                  contract, the same way `SessionMapCard` inherits it. */}
              {iso ? (
                <time data-type="caption" dateTime={iso} title={fullStamp(mark.ts)} className="shrink-0 tabular-nums text-on-surface-low">
                  {clockTime(mark.ts)}
                </time>
              ) : null}
            </motion.button>
          </li>
        )
      })}
    </ul>
  )
}
