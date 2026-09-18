import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import type { SessionMark } from './sessionMap'
import { SESSION_MAP_MIN_MARKS } from './sessionMap'
import { Popover } from '../../ui/Popover'
import { SessionMapCard, sessionMapCardContent, sessionMapMarkName } from './SessionMapCard'
import { currentMarkRange, useVisibleTurns } from './sessionMapRegion'
import { physics } from '../../design/motion'

/** SESSION MAP RAIL — the marks + track (SEMANTIC-SESSION-MAP §A.1/§A.5, atom SSM-4).
 *
 *  A narrow segmented vertical rail that indexes the transcript: one discrete tick per
 *  `SessionMark` (SSM-1's contract), laid along a subtle chrome spine.
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
 *  ── SSM-6: THE HOVER PREVIEW CARD ───────────────────────────────────────────────────────────
 *
 *  Each mark is a `<button>` and the TRIGGER for a `ui/Popover` card (`SessionMapCard`, §A.3).
 *  SSM-4 rendered marks `aria-hidden` with no focusable element anywhere in the rail — there was
 *  nothing for "focus does not auto-open" to be true OF — so a mark became a real control carrying
 *  the short `aria-label` §A.6 specifies.
 *
 *  🪤 PASSIVE FOCUS MUST NOT OPEN THE CARD, and the reason is Tab-through: the rail sits between
 *  the transcript and the composer, so a keyboard user reaching for the message box would flash a
 *  card on the way past (§A.3). Tab opens nothing. An EXPLICIT cursor key does — see REVEAL below.
 *
 *  ── SSM-5: THE CURRENT REGION IS WHAT IS ON SCREEN ──────────────────────────────────────────
 *
 *  The accented set is driven by `useVisibleTurns`' `IntersectionObserver` over `turnNodes`, not by
 *  the marks: `currentMarkRange` turns "these turns are on screen" into the contiguous mark slice
 *  that lights coral (`sessionMapRegion.ts` owns both, and the reasoning for the binary search and
 *  for the empty-set case). The rail therefore no longer claims the newest turn is current while
 *  the reader is halfway up the transcript.
 *
 *  ── SSM-7: THE CURSOR, THE JUMP, AND EXACTLY ONE CARD ───────────────────────────────────────
 *
 *  The rail is ONE tab stop with a roving `tabIndex` (§A.6). The slot starts on the current region
 *  and the arrow keys move it; `Home`/`End` go to the ends and `PageUp`/`PageDown` step by
 *  `PAGE_STEP`. `Enter`/`Space` call `onJumpTo(mark.visibleIndex)` — `ChatPage`'s existing
 *  `jumpToTurn`, verbatim reuse, no new scroll machinery.
 *
 *  🔑 REVEAL: A CURSOR KEY OPENS THE CARD, PASSIVE FOCUS DOES NOT. A sighted keyboard user
 *  arrowing down a rail of 4px ticks with no card is navigating blind — the card is the answer to
 *  "what is this one?", so §A.6 has the first Arrow reveal a mark rather than step past it. But
 *  SSM-6's Tab-through rule still has to hold. The discriminator is INTENT, not focus: a cursor key
 *  sets `reveal` immediately before it moves focus, and the receiving mark's `onFocus` CONSUMES
 *  that one-shot flag. Tab never sets it, so Tab still opens nothing.
 *
 *  🪤 AND THIS IS WHERE `ui/Popover` COULD NOT BE DRIVEN, so the mechanism is deliberately NOT the
 *  one that looks obvious. `Popover.openSignal` is one-way — `if (openSignal > 0) setOpen(true)`,
 *  with no prop, signal or ref that reaches `setOpen(false)`. Revealing card j through it while i
 *  was open would leave i open too: a rail of STACKED cards, one per arrow press. The plan sized
 *  three ways out (give `Popover` a close signal; remount it with a `key`; collapse to one
 *  rail-wide `Popover`). None was taken, because none is needed: the card the cursor opened belongs
 *  to the mark that has FOCUS, and focus is a singleton the platform already maintains. So a mark
 *  closes its own card on `blur`, through the `toggle` the primitive already hands its trigger.
 *  Exactly-one-card is then a property of focus rather than a rule the rail has to coordinate, no
 *  shared primitive changes, and no second mechanism exists to drift from the first.
 *
 *  🪤 WHOEVER OPENED THE CARD CLOSES IT, which is why `kbOpen` exists. Blur-closes cannot be
 *  unconditional: a POINTER-opened card is dismissed by the pointer leaving it (§A.3's bridge lets
 *  the pointer cross the gap and select the excerpt), and a click into that card blurs the tick —
 *  so an unconditional blur-close would dismiss the card the user is reading. One flag, one owner.
 *
 *  ── SSM-8: HIT TARGET, FOCUS RING, REDUCED MOTION ───────────────────────────────────────────
 *
 *  · `.hit-24-x` gives each 4px tick the 24px pressable band `NavRail`'s splitter uses
 *    (`hitTargetThinHandle.test.ts`, which now measures this rail as its third adopter). The band
 *    is HORIZONTAL only, and that is the utility's whole point rather than a shortfall: it pins to
 *    the element's own vertical extent so it cannot overhang its neighbours. On a rail whose ticks
 *    sit a few px apart a 24px VERTICAL band would swallow the marks above and below — the
 *    stolen-target defect that file records twice. Recorded, not rounded up, exactly as
 *    `SidePanel`'s 15px is.
 *  · NO `outline-none`: the mark takes the global `:focus-visible` ring (`tokens.css` — 2px opaque
 *    `--color-primary`, no alpha, `focusRingContrast.test.ts`) rather than minting a local one.
 *  · The HALO is the rail's one animation (§A.5's hover-enlarge) and it runs on `physics.snappy`, a
 *    gated getter, so `prefers-reduced-motion` collapses it to `instant` through the module's one
 *    off-switch (`reducedMotionAppWide.test.ts`) instead of a hand-rolled spring here. It grows the
 *    4px tick's footprint on hover/focus, which is what makes a keyboard cursor on a 4px target
 *    visible at all.
 *
 *  ── SSM-9: THE RAIL HAS NO RETURN-TO-NEWEST OF ITS OWN, ON PURPOSE ──────────────────────────
 *
 *  "Back to the newest turn" is a map affordance, and the map already has exactly one:
 *  `SessionMapReturnLatest` — the transcript pill §A.7 forbids replacing. Do not add a "newest"
 *  control at the foot of this rail, and do not give the coarse-pointer drawer (SSM-10) one:
 *  `SessionMapReturnLatest.test.tsx` derives the single-implementation property over the whole
 *  `src` tree, so a second one turns that red rather than shipping two names for one intent.
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

/** `PageUp`/`PageDown` step (§A.6). Marks, not turns: the cursor moves over ticks, and a turn can
 *  emit up to seven of them, so a turn-sized page would be a different distance on every press. */
const PAGE_STEP = 5

/** How far the halo grows around the tick. 4px × 3 = a 12px disc — big enough to find a cursor on
 *  a hairline, small enough not to reach the neighbouring tick. */
const HALO_SCALE = 3

export interface SessionMapRailProps {
  /** The ordered marks from `sessionMapMarks` (SSM-1). The rail renders one tick per mark. */
  marks: SessionMark[]
  /** `ChatPage`'s live `turnNodes` registry, keyed by the same coordinate a mark carries —
   *  the page keys it through `markCoordOf`, which is `sessionMap.ts`'s exported rule precisely
   *  so the two cannot drift. Read only to observe which turns are on screen (SSM-5). */
  turnNodes: ReadonlyMap<number, Element>
  /** The transcript scroll container (`ChatPage`'s `scrollRef`) — the observer root (SSM-5). */
  scrollRef: { current: Element | null }
  /** `ChatPage`'s `jumpToTurn`. The rail hands it a mark's `visibleIndex` and owns no scroll
   *  machinery of its own (SSM-7). */
  onJumpTo: (visibleIndex: number) => void
}

export function SessionMapRail(props: SessionMapRailProps) {
  // Self-suppression (§A.1): a map of fewer than two marks indexes nothing worth a rail. Guarded
  // out here rather than inside the body so the early return sits before any hook. The threshold
  // lives on the contract (`sessionMap.ts`) because SSM-10's drawer applies the same one.
  if (props.marks.length < SESSION_MAP_MIN_MARKS) return null
  return <SessionMapRailBody {...props} />
}

function SessionMapRailBody({ marks, turnNodes, scrollRef, onJumpTo }: SessionMapRailProps) {
  const visibleTurns = useVisibleTurns(turnNodes, scrollRef, marks.map((m) => m.visibleIndex))
  const [regionStart, regionEnd] = currentMarkRange(marks, visibleTurns)
  /** The roving cursor, or `null` while it has never been moved — see the tab-stop note below. */
  const [cursor, setCursor] = useState<number | null>(null)
  /** What the rail says out loud. Empty whenever the rail does not hold focus (§A.6). */
  const [announcement, setAnnouncement] = useState('')
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  /** One-shot "this focus came from a cursor key" — set by `move`, consumed by the mark's
   *  `onFocus`. The REVEAL discriminator (see the header). */
  const reveal = useRef(false)

  const lastIndex = marks.length - 1
  // The single tab stop. Before the first cursor key it is the current region's first mark: both
  // where the reader's attention already is and the seed the arrow keys move from. `regionStart`
  // can be the empty range's 0, which is a valid index.
  const tabStop = cursor === null ? Math.max(0, regionStart) : Math.min(cursor, lastIndex)

  const move = (to: number) => {
    const next = Math.max(0, Math.min(lastIndex, to))
    setCursor(next)
    // Armed only immediately before a focus that WILL fire, so the one-shot cannot be left set for
    // a later Tab to consume. Nothing else ever sets it, and the receiving `onFocus` clears it
    // synchronously — which is why no handler has to remember to disarm it.
    const el = buttons.current[next]
    if (!el) return
    reveal.current = true
    el.focus()
    // `focus()` is synchronous, so by here the receiving `onFocus` has either consumed the flag or
    // never ran — which happens when the cursor was ALREADY on `next` (an arrow held at either end).
    // Clearing it unconditionally is therefore a no-op in the normal case and, in that one, stops an
    // armed reveal surviving until an unrelated Tab arrives and opens a card out of nowhere.
    reveal.current = false
  }

  const jump = (index: number) => {
    onJumpTo(marks[index].visibleIndex)
    const { turnPosition, turnTotal } = sessionMapCardContent(marks, index)
    // The one thing focus does NOT announce. The mark's own `aria-label` already names the cursor
    // position on every move, so repeating it here would be the double-speak §A.6 forbids; what a
    // screen-reader user cannot otherwise tell is whether the TRANSCRIPT moved, because focus stays
    // on the rail.
    setAnnouncement(`Jumped to turn ${turnPosition} of ${turnTotal}`)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    // 🪤 THERE IS DELIBERATELY NO "FIRST ARROW" SPECIAL CASE, and writing one is how §A.6's "the
    // first Arrow reveals the last on-screen mark rather than stepping past it" gets implemented
    // backwards. The concern is real — an arrow must not step to a mark the reader cannot see — but
    // the roving slot ALREADY starts inside the current region (see `tabStop`), so a branch that
    // sent the first arrow to the region's edge sent it to where the cursor already was: measured,
    // the first keypress became a dead key that moved nothing AND revealed nothing (the reveal
    // one-shot is consumed by a focus change, and focusing the already-focused element fires
    // none). Seeding the slot is the fix; the branch was the bug.
    switch (e.key) {
      case 'ArrowUp': move(tabStop - 1); break
      case 'ArrowDown': move(tabStop + 1); break
      case 'Home': move(0); break
      case 'End': move(lastIndex); break
      case 'PageUp': move(tabStop - PAGE_STEP); break
      case 'PageDown': move(tabStop + PAGE_STEP); break
      case 'Enter': case ' ': jump(tabStop); break
      default: return
    }
    // Only for the keys the rail HANDLED, and it is load-bearing on both branches: the arrows must
    // not scroll the transcript out from under the cursor, and cancelling `Enter`/`Space` here is
    // what stops the browser's own button activation firing a SECOND jump through `onClick`.
    e.preventDefault()
  }

  return (
    <nav
      aria-label="Session map"
      onKeyDown={onKeyDown}
      // Focus leaving the rail entirely — not moving between two of its own marks — silences the
      // live region, so a later hover cannot re-announce a jump nobody just made.
      onBlur={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
        setAnnouncement('')
      }}
      className="relative flex h-full w-4 shrink-0 justify-center"
    >
      {/* The chrome spine — a hairline in the rail tone. Decorative: the marks are the targets. */}
      <div
        aria-hidden
        data-session-map-track
        className="absolute inset-y-0 w-px rounded-pill"
        style={{ background: 'var(--color-rail)' }}
      />
      {/* One discrete tick per mark, spaced by ordinal (§A.2's pre-measure fallback; height-weighted
          spacing is a later atom). The wrapper is the LOCATOR — zero-size, so the tick inside it can
          be `absolute` and own the containing block `.hit-24-x` requires. */}
      {marks.map((mark, i) => (
        <span
          key={mark.markIndex}
          className="absolute left-1/2"
          style={{ top: `${(i / lastIndex) * 100}%` }}
        >
          <SessionMapMark
            marks={marks}
            index={i}
            isCurrent={i >= regionStart && i <= regionEnd}
            isTabStop={i === tabStop}
            reveal={reveal}
            buttonRef={(el) => { buttons.current[i] = el }}
            onJump={() => jump(i)}
          />
        </span>
      ))}
      {/* §A.6: announces the keyboard's own action, and ONLY while the rail holds focus — a hover
          must stay silent, so this is deliberately not driven by the card. */}
      <span data-session-map-live role="status" aria-live="polite" className="sr-only">{announcement}</span>
    </nav>
  )
}

/** One tick and the card it opens.
 *
 *  A component per mark rather than one card for the rail, because `Popover` anchors its flyout to
 *  ITS OWN trigger's measured rect — the whole point of a per-mark card is that it appears beside
 *  the mark you are pointing at, and a single rail-wide popover would anchor to the rail (and,
 *  since the rect is measured once on open, would strand the card at the first mark's position as
 *  the cursor moved on).
 */
function SessionMapMark({ marks, index, isCurrent, isTabStop, reveal, buttonRef, onJump }: {
  marks: SessionMark[]
  index: number
  isCurrent: boolean
  isTabStop: boolean
  /** The rail's one-shot reveal intent (see the file header). Consumed here, on focus. */
  reveal: { current: boolean }
  buttonRef: (el: HTMLButtonElement | null) => void
  onJump: () => void
}) {
  const mark = marks[index]
  const timer = useRef<number | null>(null)
  /** True while THIS card was opened by a cursor key, so blur closes what blur owns. */
  const kbOpen = useRef(false)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const active = hovered || focused

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

  const tone = isCurrent ? 'var(--color-primary)' : 'var(--color-on-surface-low)'

  return (
    <Popover
      portal
      placement="bottom"
      width={CARD_WIDTH}
      trigger={(open, toggle) => (
        <button
          type="button"
          ref={buttonRef}
          data-session-mark
          data-kind={mark.kind}
          data-current={isCurrent || undefined}
          // ONE tab stop for the rail; the arrow keys move it (§A.6).
          tabIndex={isTabStop ? 0 : -1}
          aria-label={sessionMapMarkName(marks, index)}
          onClick={onJump}
          onMouseEnter={() => {
            setHovered(true)
            cancel()
            if (!open) after(OPEN_DELAY_MS, () => { kbOpen.current = false; toggle() })
          }}
          onMouseLeave={() => {
            setHovered(false)
            cancel()
            if (open) after(CLOSE_DELAY_MS, toggle)
          }}
          onFocus={() => {
            setFocused(true)
            // Consume the one-shot: a cursor key reveals the card, a Tab does not (see REVEAL).
            const wanted = reveal.current
            reveal.current = false
            if (wanted && !open) { kbOpen.current = true; toggle() }
          }}
          onBlur={() => {
            setFocused(false)
            cancel()
            if (open && kbOpen.current) { kbOpen.current = false; toggle() }
          }}
          className="absolute left-1/2 top-0 block size-1 -translate-x-1/2 -translate-y-1/2 rounded-pill hit-24-x"
          style={{ background: tone }}
        >
          {/* The hover/focus halo (§A.5). `initial={false}` so a rail of 200 ticks does not animate
              on mount; `physics.snappy` so reduced motion collapses it through the module gate. */}
          <motion.span
            aria-hidden
            data-session-map-halo
            className="pointer-events-none absolute inset-0 rounded-pill"
            initial={false}
            animate={{ scale: active ? HALO_SCALE : 1, opacity: active ? 0.25 : 0 }}
            transition={physics.snappy}
            style={{ background: tone }}
          />
        </button>
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
