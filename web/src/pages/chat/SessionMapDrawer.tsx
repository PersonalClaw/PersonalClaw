import { useState } from 'react'
import { Bookmark, BookmarkCheck, MessageSquare } from 'lucide-react'
import type { SessionMapEntry } from './sessionMap'
import { SESSION_MAP_MIN_MARKS } from './sessionMap'
import { bookmarkLabel, sessionMapMarkName } from './SessionMapCard'
import { currentMarkRange } from './sessionMapRegion'
import { MARK_MAX_PX, markLength } from './sessionMapGeometry'
import { EmptyState } from '../../ui/ListScaffold'
import { IconButton } from '../../ui/IconButton'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'

/** SESSION MAP — THE TOUCH FORM (#4776).
 *
 *  A touch device has no hover and no marker-sized precision, so on the mobile form the rail
 *  collapses to ONE named control (`ChatPage`'s "Session map" header control) that opens this
 *  drawer: the rail laid out for a finger. Each row is a message, with the rail's own marker beside
 *  it (its on-screen length and tone, its bookmark dot), the preview's content inlined because there
 *  is no pointer to reveal it with, and the bookmark control at the row's end.
 *
 *  🔑 IT IS NOT A SECOND SESSION MAP. It renders the SAME `SessionMapEntry[]` the rail renders,
 *  jumps through the SAME `onJumpTo`, bookmarks through the SAME `onToggleBookmark`, and names each
 *  row with the SAME `sessionMapMarkName`, so the two forms cannot disagree about what the map
 *  holds, what an entry is called, which are bookmarked or where a tap lands. What differs is only
 *  what a finger can use: 44px rows and controls, and nothing that needs a hover.
 *
 *  The rows are still: no staggered entrance, since a list the owner opens to navigate should be
 *  there at once.
 *
 *  🪤 AND IT HAS NO RETURN-TO-NEWEST CONTROL: the map owns exactly one "back to the newest message"
 *  affordance app-wide, and it is the transcript's (`SessionMapReturnLatest`).
 *
 *  🪤 NO `<nav>` LANDMARK HERE, unlike the rail. This body is rendered INSIDE `ui/SidePanel`, which
 *  is already a `role="region"` named by its "Session map" title.
 */

export interface SessionMapDrawerProps {
  /** The ordered entries from `sessionMapEntries` — the same array the rail receives. */
  entries: SessionMapEntry[]
  /** `ChatPage`'s `jumpToTurn`, handed an entry's coordinate. Identical to the rail's prop. */
  onJumpTo: (visibleIndex: number) => void
  /** Bookmark a message, or take its bookmark off — the rail's own handler. */
  onToggleBookmark?: (entry: SessionMapEntry) => void
  /** `ChatPage`'s live `turnNodes` registry and transcript scroller, read ONCE as the drawer opens
   *  to say which exchanges were on screen behind it. */
  turnNodes?: ReadonlyMap<number, Element>
  scrollRef?: { current: Element | null }
}

/** The coordinates on screen in the transcript right now, measured from the turn nodes' boxes.
 *  The drawer reads it as it opens, before the panel narrows the transcript. */
function onScreenNow(turnNodes: ReadonlyMap<number, Element> | undefined, scroller: Element | null | undefined): number[] {
  if (!turnNodes || !scroller) return []
  const view = scroller.getBoundingClientRect()
  if (!view.height) return []
  const seen: number[] = []
  for (const [coord, node] of turnNodes) {
    const r = node.getBoundingClientRect()
    if (r.bottom > view.top && r.top < view.bottom) seen.push(coord)
  }
  return seen
}

export function SessionMapDrawer({ entries, onJumpTo, onToggleBookmark, turnNodes, scrollRef }: SessionMapDrawerProps) {
  const [visible] = useState(() => onScreenNow(turnNodes, scrollRef?.current))
  // The same self-suppression threshold the rail applies. A drawer the owner deliberately OPENED
  // must say why it is empty instead of showing a blank panel.
  if (entries.length < SESSION_MAP_MIN_MARKS) {
    return <EmptyState icon={MessageSquare} title="Nothing to map yet" hint="Once you have sent four messages, the session map lists each one, so you can jump back to it." />
  }
  const [start, end] = visible.length ? currentMarkRange(entries, visible) : [0, -1]
  return (
    <ul data-session-map-drawer className="flex flex-col gap-px">
      {entries.map((entry, i) => {
        const iso = isoStamp(entry.ts)
        const isCurrent = i >= start && i <= end
        const len = markLength(i, null, isCurrent)
        const bookmark = bookmarkLabel(entry)
        return (
          // The row is the jump, and it carries a control of its own (the bookmark), so the jump is
          // `ui/RowHitTarget`'s empty button stretched under the row, never a button around a button.
          // `min-h-11` is 44px — the touch floor this form exists for.
          <li
            key={entry.markIndex}
            data-session-map-row
            data-current={isCurrent || undefined}
            data-bookmarked={entry.bookmarked || undefined}
            onClick={() => onJumpTo(entry.visibleIndex)}
            // Out of the tab order: the keyboard reaches the row through its `RowHitTarget`.
            tabIndex={-1}
            className="group relative isolate flex min-h-11 cursor-pointer items-stretch rounded-md transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary"
          >
            {/* The row's name is the rail's: short, positioned, bounded, saying a bookmark. */}
            <RowHitTarget label={sessionMapMarkName(entries, i)} selected={isCurrent} />
            <span className="flex min-w-0 flex-1 items-start gap-s px-2.5 py-2 text-left">
              {/* The rail's marker for this message, at rest: its length and tone say whether its
                  exchange was on screen, and its dot says it is bookmarked. */}
              <span aria-hidden data-session-map-row-mark className="relative mt-2 flex h-0.5 shrink-0" style={{ width: MARK_MAX_PX + 8 }}>
                <span
                  className="block h-0.5 origin-left rounded-pill"
                  style={{
                    width: MARK_MAX_PX,
                    transform: `scaleX(${len / MARK_MAX_PX})`,
                    background: isCurrent ? 'var(--color-primary)' : 'var(--color-map-rest)',
                  }}
                />
                {entry.bookmarked ? (
                  <span className="absolute left-0 top-1/2 size-1.5 rounded-full"
                    style={{ background: isCurrent ? 'var(--color-primary)' : 'var(--color-map-rest)', transform: `translate(${len + 2}px, -50%)` }} />
                ) : null}
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <span data-type="body-s" data-session-map-request className="line-clamp-2 text-on-surface">{entry.preview}</span>
                {entry.response ? (
                  <span data-type="caption" data-session-map-response className="mt-xs line-clamp-1 text-on-surface-var">{entry.response}</span>
                ) : null}
              </span>
              {iso ? (
                <time data-type="caption" dateTime={iso} title={fullStamp(entry.ts)} className="shrink-0 tabular-nums text-on-surface-low">
                  {clockTime(entry.ts)}
                </time>
              ) : null}
            </span>
            {onToggleBookmark ? (
              <IconButton
                icon={entry.bookmarked ? BookmarkCheck : Bookmark}
                // Each row's control names its message, so a list of them is not a column of one name.
                label={`${bookmark.label}: message ${i + 1}`}
                title={bookmark.label}
                active={entry.bookmarked}
                size={44}
                iconSize={18}
                disabled={!!bookmark.unavailable}
                disabledReason={bookmark.unavailable}
                // The row's own click is the jump; the bookmark keeps its click to itself.
                onClick={(e) => { e.stopPropagation(); onToggleBookmark(entry) }}
              />
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}
