import { useEffect, useRef } from 'react'
import type { SessionMark } from './sessionMap'
import { Popover } from '../../ui/Popover'
import { SessionMapCard, sessionMapMarkName } from './SessionMapCard'

/** SESSION MAP RAIL — the marks + track (SEMANTIC-SESSION-MAP §A.1/§A.5, atom SSM-4).
 *
 *  A narrow segmented vertical rail that indexes the transcript: one discrete tick per
 *  `SessionMark` (SSM-1's contract), laid along a subtle chrome spine. It is the visual floor
 *  the later atoms build on — current-region accent (SSM-5), the hover/focus preview card
 *  (SSM-6), keyboard/click jump (SSM-7), the hit-target + reduced-motion pass (SSM-8). This
 *  atom owns ONLY the marks + track: derivation lives in `sessionMapMarks`, so the rail is a
 *  pure function of the marks it is handed and holds no transcript logic of its own.
 *
 *  DESIGN LANGUAGE (web/DESIGN.md):
 *   · The track is `--color-rail` — the same chrome tone `NavRail` uses — so the spine reads as
 *     rail, not content (§A.1). It is a 1px hairline, never a colored side-stripe: the
 *     Tone-Not-Line rule keeps tone in the discrete marks (the nav targets), never a full-height
 *     colored bar (enforced by `sideStripeDoctrine.test.ts`).
 *   · Marks carry only the two-tone accent/history vocabulary this atom is scoped to. The
 *     CURRENT region paints `--color-primary` (One-Voice: coral means "the agent / live /
 *     current"); history paints `--color-on-surface-low` (neutral ink). Colour encodes
 *     position-in-session and nothing else — no decoration (web/DESIGN.md §Semantic). Every
 *     value routes through a token, so `tokenLint.test.ts` passes (no raw hex/px).
 *
 *  CURRENT REGION. Viewport tracking arrives with SSM-5's `IntersectionObserver`. Until then the
 *  newest turn is the current region — which is exactly the on-load state before any scroll, so
 *  this is the correct special case, not a placeholder. SSM-5 generalises it to "whatever is on
 *  screen" by driving the accented set; here it is derived from the marks alone.
 *
 *  ── SSM-6: THE HOVER/FOCUS PREVIEW CARD ─────────────────────────────────────────────────────
 *
 *  Each mark is now the TRIGGER for its own `ui/Popover` card (`SessionMapCard`, §A.3), which
 *  made two things concrete that SSM-4 had deferred:
 *
 *  🔑 THE MARK BECAME A REAL CONTROL, because SSM-6's contract is "hover **or focus**" and SSM-4
 *  rendered marks `aria-hidden` with no focusable element anywhere in the rail — there was
 *  nothing for "focus does not auto-open" to be true OF. So a mark is a `<button>` carrying the
 *  short `aria-label` §A.6 specifies. It is ONE tab stop, not one per tick: the roving slot sits
 *  on the current region (`tabIndex` 0 there, -1 elsewhere), which is both where a reader's
 *  attention is and the seed SSM-7's arrow-key cursor moves. SSM-7 owns the cursor itself,
 *  Enter/Space → `onJumpTo`, and the `aria-live` announcement.
 *
 *  🪤 FOCUS MUST NOT OPEN THE CARD, and the reason is Tab-through: the rail sits between the
 *  transcript and the composer, so a keyboard user reaching for the message box would flash a
 *  card per tick on the way past (§A.3). Only the pointer opens it — there is deliberately no
 *  `onFocus` wiring here, and `SessionMapCard.test.tsx` pins it.
 */

/** Card width. A preview line wants roughly 45-55 characters to be worth reading — narrower and
 *  the two-line clamp cuts mid-phrase, wider and a card hovering over the transcript occludes
 *  the very turn it is describing. */
const CARD_WIDTH = 300

/** Pointer-intent delays (§A.3). The OPEN delay is what stops a pointer merely CROSSING the rail
 *  on its way to the transcript from strobing a card per tick; the CLOSE delay is the bridge that
 *  lets the pointer leave the 4px tick, cross the gap and land on the card — without it the card
 *  dismisses itself the instant you try to read it. */
const OPEN_DELAY_MS = 150
const CLOSE_DELAY_MS = 120

export interface SessionMapRailProps {
  /** The ordered marks from `sessionMapMarks` (SSM-1). The rail renders one tick per mark. */
  marks: SessionMark[]
}

export function SessionMapRail({ marks }: SessionMapRailProps) {
  // Self-suppression (§A.1): a map of fewer than two marks indexes nothing worth a rail.
  if (marks.length < 2) return null

  // The newest turn's coordinate is the current region (see CURRENT REGION above); every mark
  // that belongs to it — the turn mark and its sub-events — lights as current.
  const currentVisibleIndex = Math.max(...marks.map((m) => m.visibleIndex))
  const lastIndex = marks.length - 1
  // The rail's single tab stop: the first mark of the current region (see the SSM-6 note above).
  const tabStop = marks.findIndex((m) => m.visibleIndex === currentVisibleIndex)

  return (
    <nav aria-label="Session map" className="relative flex h-full w-4 shrink-0 justify-center">
      {/* The chrome spine — a hairline in the rail tone. Decorative: the marks are the targets. */}
      <div
        aria-hidden
        data-session-map-track
        className="absolute inset-y-0 w-px rounded-pill"
        style={{ background: 'var(--color-rail)' }}
      />
      {/* One discrete tick per mark, spaced by ordinal (§A.2's pre-measure fallback; SSM-5 adds
          height-weighted spacing). */}
      {marks.map((mark, i) => (
        <span
          key={mark.markIndex}
          className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2"
          style={{ top: `${(i / lastIndex) * 100}%` }}
        >
          <SessionMapMark
            marks={marks}
            index={i}
            isCurrent={mark.visibleIndex === currentVisibleIndex}
            isTabStop={i === tabStop}
          />
        </span>
      ))}
    </nav>
  )
}

/** One tick and the card it opens.
 *
 *  A component per mark rather than one card for the rail, because `Popover` anchors its flyout to
 *  ITS OWN trigger's measured rect — the whole point of a per-mark card is that it appears beside
 *  the mark you are pointing at, and a single rail-wide popover would anchor to the rail.
 */
function SessionMapMark({ marks, index, isCurrent, isTabStop }: {
  marks: SessionMark[]
  index: number
  isCurrent: boolean
  isTabStop: boolean
}) {
  const mark = marks[index]
  const timer = useRef<number | null>(null)
  const cancel = () => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
  }
  /** Arm ONE pending intent, replacing any other. Always cancelling first is what makes a pointer
   *  sweeping down the rail leave no armed opens behind it. */
  const after = (ms: number, fn: () => void) => {
    cancel()
    timer.current = window.setTimeout(() => { timer.current = null; fn() }, ms)
  }
  // A tick can unmount while an intent is armed (the transcript re-derives its marks on every
  // turn), and a timer firing into an unmounted component would toggle a dead popover.
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current) }, [])

  return (
    <Popover
      portal
      placement="bottom"
      width={CARD_WIDTH}
      trigger={(open, toggle) => (
        <button
          type="button"
          data-session-mark
          data-kind={mark.kind}
          data-current={isCurrent || undefined}
          // ONE tab stop for the rail; SSM-7 moves it with the arrow keys.
          tabIndex={isTabStop ? 0 : -1}
          aria-label={sessionMapMarkName(marks, index)}
          onMouseEnter={() => { cancel(); if (!open) after(OPEN_DELAY_MS, toggle) }}
          onMouseLeave={() => { cancel(); if (open) after(CLOSE_DELAY_MS, toggle) }}
          className="block size-1 rounded-pill"
          style={{ background: isCurrent ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}
        />
      )}
    >
      {/* `close` restores focus to the trigger — `Popover`'s contract for a dismissal, and correct
          here unmodified: a programmatic focus following a pointer gesture does not paint
          `:focus-visible`, so the rail gains no stray ring, and the tick the pointer just left is
          exactly where a keyboard user would want to continue from. */}
      {(close) => (
        <SessionMapCard
          marks={marks}
          index={index}
          onMouseEnter={cancel}
          onMouseLeave={() => after(CLOSE_DELAY_MS, close)}
        />
      )}
    </Popover>
  )
}
