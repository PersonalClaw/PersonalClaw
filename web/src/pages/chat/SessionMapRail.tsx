import { useEffect, useId, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import type { SessionMapEntry } from './sessionMap'
import { SESSION_MAP_MIN_MARKS } from './sessionMap'
import { Popover } from '../../ui/Popover'
import { SessionMapCard, sessionMapMarkName } from './SessionMapCard'
import { currentMarkRange, useVisibleTurns } from './sessionMapRegion'
import { physics } from '../../design/motion'

/** SESSION MAP RAIL — the markers (SEMANTIC-SESSION-MAP §A.1/§A.5, atoms SSM-4/5/6/7/8).
 *
 *  A vertical rail that indexes the transcript: ONE MARKER PER USER MESSAGE (`sessionMapEntries`),
 *  stacked on a constant pitch in the transcript's left gutter, fixed while the transcript scrolls.
 *
 *  ── THE DESIGN, AS THE OWNER RULED IT (2026-09-25) ──────────────────────────────────────────
 *
 *  The owner described a reference app's rail and ruled on each property; these are the rules, and
 *  each one replaced something the previous form did:
 *
 *   1. A MARKER IS A USER MESSAGE. Assistant replies and their tool calls are not markers: a reply
 *      is too long to show, and its opening is already on the question's card.
 *   2. EVERY MARKER RESTS AT ONE LENGTH. The previous form spent length on structure (a turn read
 *      longer than a sub-event) and on the current region (on-screen marks idled longer). Both are
 *      gone — at rest the rail is a column of identical lines.
 *   3. WHAT IS ON SCREEN IS SAID BY COLOUR ALONE — coral for the messages whose exchange is in
 *      view, a muted neutral for the rest — and never by size, so scrolling the transcript never
 *      makes the rail's geometry move.
 *   4. ONLY THE MARKER UNDER THE POINTER OR THE KEYBOARD CURSOR EXPANDS, beyond the resting length
 *      and in a brighter tone, with its preview card beside it. Its neighbours stay put: the ±3
 *      falloff the previous form took from Codex made a sweep down the rail ripple, which is size
 *      changing for something other than the one marker the user is on.
 *   5. SKIMMING MOVES NOTHING BUT THE CARD. Pointing and arrowing preview; only a click, Enter or
 *      Space jumps the transcript (`onJumpTo`, `ChatPage`'s `jumpToTurn`).
 *
 *  Kept from the form this replaces, each for its original reason: the constant pitch with a rail
 *  that scrolls itself once it outgrows the gutter (a marker's size never depends on how long the
 *  session is), the card placed BESIDE the marker rather than over its neighbours (`ui/Popover`'s
 *  `placement="right"`), `aria-current` on the on-screen region, and motion that is LENGTH ONLY on
 *  the one gated spring — colour switches, it never animates.
 *
 *  ── THE TWO TONES, AND WHAT THEIR CONTRAST CAN AND CANNOT DO ────────────────────────────────
 *
 *  `design/schemeContrast.test.ts` parses the `const tone = isCurrent ? … : …` declaration below
 *  and measures it in all 12 schemes × both modes. Both tones clear SC 1.4.11's 3:1 on the rail's
 *  ground, the canvas. The rest tone is `--color-map-rest`, a neutral minted for this rail and
 *  tuned to sit as far in lightness from coral as 3:1-on-the-canvas allows: the previous rest tone,
 *  `--color-on-surface-low`, measured 1.004:1 against coral in dark mode — identical in lightness,
 *  i.e. hue alone — which a rail that now says "on screen" by colour ONLY could not keep. The
 *  lightness step between the two is pinned at its measured floor there. It is not 3:1, and that is
 *  arithmetic rather than tuning: both tones must clear 3:1 on the canvas, so a 3:1 step between
 *  them needs an in-view tone about 9:1 from the canvas, and the accent is 5.6-12.9:1. The rest of
 *  the distinction is hue, and `aria-current` carries it to assistive tech.
 *
 *  The pointed-at marker brightens: to `--color-primary-emphasis` when it is on screen (the accent
 *  tone whose contract is to out-contrast primary on its ground), to `--color-on-surface` when it is
 *  not. Either way it is the highest-contrast line on the rail, which is what "brightens" means in
 *  both modes.
 *
 *  ── THERE IS NO SPINE, AND THAT IS A DELETION, NOT AN OMISSION ──────────────────────────────
 *
 *  An earlier form drew a 1px `--color-rail` hairline behind the marks. `--color-rail` is `#f0f4f8`
 *  in light mode against a `--color-canvas` of the same value — 1.000:1, an element that paints
 *  nothing — and with markers on a constant pitch the column already reads as a rail. So the track
 *  is gone rather than retinted. `SessionMapRail.test.tsx` asserts its ABSENCE so it cannot drift
 *  back. Nothing here is a coloured side-stripe either (`design/sideStripeDoctrine.test.ts`): the
 *  tone lives in discrete markers that ARE the nav targets.
 *
 *  ── SSM-5: THE CURRENT REGION IS WHAT IS ON SCREEN ──────────────────────────────────────────
 *
 *  The coral set is driven by `useVisibleTurns`' `IntersectionObserver` over EVERY turn an entry
 *  owns — the question and each turn of its answer — and `currentMarkRange` resolves an on-screen
 *  turn to the question that opened its exchange (`sessionMapRegion.ts` owns both). So while a long
 *  answer is being read, its question stays lit after the question itself has scrolled away.
 *
 *  ── SSM-6/7: THE CARD, THE CURSOR, THE JUMP, AND EXACTLY ONE CARD ───────────────────────────
 *
 *  Each marker is a `<button>` and the TRIGGER for a `ui/Popover` card (`SessionMapCard`, §A.3).
 *
 *  🪤 PASSIVE FOCUS MUST NOT OPEN THE CARD, and the reason is Tab-through: the rail sits between
 *  the transcript and the composer, so a keyboard user reaching for the message box would flash a
 *  card on the way past (§A.3). Tab opens nothing. An EXPLICIT cursor key does — see REVEAL below.
 *
 *  The rail is ONE tab stop with a roving `tabIndex` (§A.6). The slot starts on the current region
 *  and the arrow keys move it; `Home`/`End` go to the ends and `PageUp`/`PageDown` step by
 *  `PAGE_STEP`. `Enter`/`Space` call `onJumpTo(entry.visibleIndex)` — `ChatPage`'s existing
 *  `jumpToTurn`, verbatim reuse, no new scroll machinery.
 *
 *  🔑 REVEAL: A CURSOR KEY OPENS THE CARD, PASSIVE FOCUS DOES NOT. A sighted keyboard user
 *  arrowing down a rail with no card is navigating blind — the card is the answer to "what is this
 *  one?", so §A.6 has the first Arrow reveal a marker rather than step past it. But SSM-6's
 *  Tab-through rule still has to hold. The discriminator is INTENT, not focus: a cursor key sets
 *  `reveal` immediately before it moves focus, and the receiving marker's `onFocus` CONSUMES that
 *  one-shot flag. Tab never sets it, so Tab still opens nothing.
 *
 *  🪤 AND THIS IS WHERE `ui/Popover` COULD NOT BE DRIVEN, so the mechanism is deliberately NOT the
 *  one that looks obvious. `Popover.openSignal` is one-way — `if (openSignal > 0) setOpen(true)`,
 *  with no prop, signal or ref that reaches `setOpen(false)`. Revealing card j through it while i
 *  was open would leave i open too: a rail of STACKED cards, one per arrow press. The card the
 *  cursor opened belongs to the marker that has FOCUS, and focus is a singleton the platform
 *  already maintains. So a marker closes its own card on `blur`, through the `toggle` the primitive
 *  already hands its trigger. Exactly-one-card is then a property of focus rather than a rule the
 *  rail has to coordinate, no shared primitive changes, and no second mechanism exists to drift.
 *
 *  🪤 WHOEVER OPENED THE CARD CLOSES IT, which is why `kbOpen` exists. Blur-closes cannot be
 *  unconditional: a POINTER-opened card is dismissed by the pointer leaving it (§A.3's bridge lets
 *  the pointer cross the gap and select the excerpt), and a click into that card blurs the marker —
 *  so an unconditional blur-close would dismiss the card the user is reading. One flag, one owner.
 *
 *  🔴 AND THE CURSOR SCROLLS THE RAIL BEFORE IT FOCUSES, not after, because the rail has a scroller
 *  of its own. `focus()` scroll-into-view fires a `scroll` event, and a portaled `Popover` closes on
 *  ANY scroll (capture) so the fixed flyout cannot drift off its anchor — so focusing first would
 *  have the reveal open a card and the scroll it caused close it again, on whichever side of the
 *  race the frame landed. `revealInRail` runs first and `focus({ preventScroll: true })` raises no
 *  scroll of its own, so the only scroll event happens while the NEW card does not yet exist.
 *
 *  ── SSM-8: HIT TARGET, FOCUS RING, REDUCED MOTION ───────────────────────────────────────────
 *
 *  · THE PRESSABLE AREA IS THE WHOLE ROW, the rows are flush, and a row is 32×24. Every pixel of
 *    the rail belongs to some marker, and each marker is now a 24×24-or-larger target, which is
 *    WCAG 2.2 SC 2.5.8's minimum — the owner listed "precise pointer movement required" among the
 *    reference's frictions. The previous 10px pitch did not meet it (neither did Codex's); with one
 *    marker per user message instead of one per event there are few enough rows to afford the
 *    height, and the rail still scrolls itself once a long session outgrows the gutter.
 *    🪤 NOT `absolute inset-0`: `ui/Popover` wraps its trigger in an unsized `<div className=
 *    "relative">`, which becomes the containing block for anything absolute inside it, so an inset
 *    marker measured 32×0 in a real browser while every jsdom assertion passed (Playwright read
 *    "element is not visible" 321 times). Explicit dimensions cannot be captured by that wrapper.
 *  · 🔴 AND THE ROW HAS TO CLEAR `NavRail`'s BAND, which is why this rail carries a fixed `ml-4`.
 *    The rail mounts flush against the shell's content-column edge, and `ui/NavRail`'s resize
 *    splitter sits on the other side of exactly that seam wearing a `.hit-24-x` band plus `z-10`.
 *    Measured at 1280x420 on a 196px nav: splitter box 192-196, so its band spans 182-206; before
 *    this clearance `document.elementFromPoint` at every marker's centre returned the SPLITTER.
 *    `ml-4` puts the rail's left edge at 212, clear of 206. FIXED `ml-4`, NOT the t-shirt `ml-l`:
 *    `--spacing-l` is `16px * var(--space-scale)`, which the compact densities drop to 12.8px and
 *    10.9px against a 14px requirement, so the stolen target would come back for exactly the users
 *    who chose tighter spacing. Tailwind's numeric scale is not re-mapped (no `--spacing:`
 *    override), so `ml-4` is a real 16px at every density.
 *  · NO `outline-none`: the marker takes the global `:focus-visible` ring (`tokens.css` — 2px
 *    opaque `--color-primary`, `focusRingContrast.test.ts`) in ADDITION to expanding, so a keyboard
 *    user sees where the cursor is through two channels, the platform's and the design's.
 *  · THE LENGTH IS THE RAIL'S ONE ANIMATION. It runs on `physics.snappy`, a gated getter, so
 *    `prefers-reduced-motion` collapses it to `instant` through the module's one off-switch
 *    (`reducedMotionAppWide.test.ts`): with motion off the marker still expands, it just does not
 *    travel there.
 *
 *  ── SSM-9: THE RAIL HAS NO RETURN-TO-NEWEST OF ITS OWN, ON PURPOSE ──────────────────────────
 *
 *  "Back to the newest message" is a map affordance, and the map already has exactly one:
 *  `SessionMapReturnLatest`, the transcript's circular control. Do not add a "newest" control at
 *  the foot of this rail, and do not give the coarse-pointer drawer (SSM-10) one:
 *  `SessionMapReturnLatest.test.tsx` derives the single-implementation property over the whole
 *  `src` tree, so a second one turns that red rather than shipping two names for one intent.
 */

/** Card width. A preview line wants roughly 45-55 characters to be worth reading — narrower and
 *  the two-line clamp cuts mid-phrase, wider and a card hovering over the transcript occludes
 *  the very turn it is describing. (Codex's own cap is `min(20rem, 100vw - 16px)`.) */
const CARD_WIDTH = 300

/** Pointer-intent delays (§A.3). The OPEN delay is what stops a pointer merely CROSSING the rail
 *  on its way to the transcript from strobing a card per marker; the CLOSE delay is the bridge that
 *  lets the pointer leave the marker, cross the gap and land on the card — without it the card
 *  dismisses itself the instant you try to read it. */
const OPEN_DELAY_MS = 150
const CLOSE_DELAY_MS = 120

/** `PageUp`/`PageDown` step (§A.6), in markers — one per user message. */
const PAGE_STEP = 5

/** The ONE resting length, as a fraction of the 24px line: every marker idles at 12px, whatever
 *  it indexes and whether or not it is on screen (owner rule 2). Half is what leaves the pointed-at
 *  marker room to read as expanded — it doubles — without the resting column reading as dots. */
export const MARK_REST_SCALE = 0.5

/** The pointed-at marker's length: the full line (owner rule 4). */
export const MARK_EXPANDED_SCALE = 1

/** The rail fades its own top and bottom edge so an overflowing marker list ends in a gradient
 *  rather than a hard cut (Codex: `--edge-fade-distance: 2.5rem`). `black` rather than a hex
 *  because a mask reads only alpha — and a hex literal is the one thing `tokenLint` never allows. */
const EDGE_FADE =
  'linear-gradient(to bottom, transparent, black var(--spacing-xl),'
  + ' black calc(100% - var(--spacing-xl)), transparent)'

/** Keep the cursor's marker inside the rail's OWN scroller (a roving cursor that walks off the
 *  visible slice of the rail is invisible).
 *
 *  Rect-based rather than `offsetTop`-based: the marker sits inside `Popover`'s wrapper, so its
 *  `offsetTop` is relative to that and would place every marker at the top. jsdom reports zeroes
 *  for every rect, which makes this a no-op there rather than a wrong scroll. */
function revealInRail(list: HTMLElement | null, mark: HTMLElement | null) {
  if (!list || !mark) return
  const l = list.getBoundingClientRect()
  const m = mark.getBoundingClientRect()
  // Clear of the edge FADE, not merely inside the box: the list's padding is the fade distance (see
  // the list's note), so a marker revealed only as far as the box edge would sit in the faded band.
  const fade = parseFloat(getComputedStyle(list).paddingTop) || 0
  if (m.top < l.top + fade) list.scrollTop -= l.top + fade - m.top
  else if (m.bottom > l.bottom - fade) list.scrollTop += m.bottom - (l.bottom - fade)
}

export interface SessionMapRailProps {
  /** The ordered entries from `sessionMapEntries` — one marker per user message. */
  entries: SessionMapEntry[]
  /** `ChatPage`'s live `turnNodes` registry, keyed by the same coordinate an entry carries —
   *  the page keys it through `markCoordOf`, the one rule the entries are built with too. Read
   *  only to observe which turns are on screen (SSM-5). */
  turnNodes: ReadonlyMap<number, Element>
  /** The transcript scroll container (`ChatPage`'s `scrollRef`) — the observer root (SSM-5). */
  scrollRef: { current: Element | null }
  /** `ChatPage`'s `jumpToTurn`. The rail hands it an entry's `visibleIndex` and owns no scroll
   *  machinery of its own (SSM-7). */
  onJumpTo: (visibleIndex: number) => void
}

export function SessionMapRail(props: SessionMapRailProps) {
  // Self-suppression (§A.1): a map of fewer than two messages indexes nothing worth a rail.
  // Guarded out here rather than inside the body so the early return sits before any hook. The
  // threshold lives on the contract (`sessionMap.ts`) because SSM-10's drawer applies the same one.
  if (props.entries.length < SESSION_MAP_MIN_MARKS) return null
  return <SessionMapRailBody {...props} />
}

function SessionMapRailBody({ entries, turnNodes, scrollRef, onJumpTo }: SessionMapRailProps) {
  const visibleTurns = useVisibleTurns(turnNodes, scrollRef, entries.flatMap((e) => e.coords))
  const [regionStart, regionEnd] = currentMarkRange(entries, visibleTurns)
  /** The roving cursor, or `null` while it has never been moved — see the tab-stop note below. */
  const [cursor, setCursor] = useState<number | null>(null)
  /** The ONE marker the pointer or the keyboard is on, or `null` — the only marker that expands.
   *  Rail state rather than per-marker state because it is a singleton: a pointer leaving one
   *  marker for the next must hand the expansion over, never leave two expanded. */
  const [pointedAt, setPointedAt] = useState<number | null>(null)
  /** What the rail says out loud. Empty whenever the rail does not hold focus (§A.6). */
  const [announcement, setAnnouncement] = useState('')
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const listRef = useRef<HTMLDivElement>(null)
  const hintId = useId()
  /** One-shot "this focus came from a cursor key" — set by `move`, consumed by the marker's
   *  `onFocus`. The REVEAL discriminator (see the header). */
  const reveal = useRef(false)

  const lastIndex = entries.length - 1
  // The single tab stop. Before the first cursor key it is the current region's first marker:
  // both where the reader's attention already is and the seed the arrow keys move from.
  // `regionStart` can be the empty range's 0, which is a valid index.
  const tabStop = cursor === null ? Math.max(0, regionStart) : Math.min(cursor, lastIndex)

  const move = (to: number) => {
    const next = Math.max(0, Math.min(lastIndex, to))
    setCursor(next)
    const el = buttons.current[next]
    if (!el) return
    // Scroll the rail FIRST — see the header's note on the portaled Popover's scroll-close.
    revealInRail(listRef.current, el)
    // Armed only immediately before a focus that WILL fire, so the one-shot cannot be left set for
    // a later Tab to consume. Nothing else ever sets it, and the receiving `onFocus` clears it
    // synchronously — which is why no handler has to remember to disarm it.
    reveal.current = true
    el.focus({ preventScroll: true })
    // `focus()` is synchronous, so by here the receiving `onFocus` has either consumed the flag or
    // never ran — which happens when the cursor was ALREADY on `next` (an arrow held at either end).
    // Clearing it unconditionally is therefore a no-op in the normal case and, in that one, stops an
    // armed reveal surviving until an unrelated Tab arrives and opens a card out of nowhere.
    reveal.current = false
  }

  const jump = (index: number) => {
    onJumpTo(entries[index].visibleIndex)
    // The one thing focus does NOT announce. The marker's own `aria-label` already names the cursor
    // position on every move, so repeating it here would be the double-speak §A.6 forbids; what a
    // screen-reader user cannot otherwise tell is whether the TRANSCRIPT moved, because focus stays
    // on the rail.
    setAnnouncement(`Jumped to message ${index + 1} of ${entries.length}`)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    // 🪤 THERE IS DELIBERATELY NO "FIRST ARROW" SPECIAL CASE, and writing one is how §A.6's "the
    // first Arrow reveals the last on-screen marker rather than stepping past it" gets implemented
    // backwards. The roving slot ALREADY starts inside the current region (see `tabStop`), so a
    // branch that sent the first arrow to the region's edge sent it to where the cursor already
    // was: measured, the first keypress became a dead key that moved nothing AND revealed nothing.
    // Seeding the slot is the fix; the branch was the bug.
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
      aria-describedby={hintId}
      onKeyDown={onKeyDown}
      // Focus leaving the rail entirely — not moving between two of its own markers — silences the
      // live region, so a later hover cannot re-announce a jump nobody just made.
      onBlur={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
        setAnnouncement('')
      }}
      // `ml-4` is the clearance that keeps this rail's pressable band off `ui/NavRail`'s splitter
      // band — load-bearing, measured, and deliberately NOT a density-scaled token. See SSM-8.
      // `items-center`: the marker list is as tall as it needs to be, capped at the gutter's
      // height, and sits in the middle of it rather than stretching.
      className="relative ml-4 flex h-full w-8 shrink-0 items-center"
    >
      {/* The reference's first friction was that its marks are small and UNEXPLAINED. The landmark
          says what it is and how to use it once, to assistive tech, instead of every marker saying
          it again; sighted users learn it from the card the first hover opens. */}
      <span id={hintId} className="sr-only">
        One mark per message you sent. Point at or arrow to a mark to preview it; click it or press
        Enter to jump to it.
      </span>
      {/* The marker list — a constant-pitch column that SCROLLS ITSELF once it outgrows the gutter,
          so a marker's size never depends on how long the session is. `overscroll-contain` keeps a
          wheel gesture that reaches the end of the rail from chaining into the transcript behind
          it. Scrollbars are hidden app-wide (`tokens.css`), so the fade mask is the only edge
          affordance and it needs no utility to suppress a bar.
          🔴 `py-xl` IS THE FADE DISTANCE, AND WITHOUT IT THE FIRST AND LAST MARKERS WERE DIMMED. The
          mask fades the list's own top and bottom `--spacing-xl`, overflowing or not, and an end
          marker's line sat 12px in — at 60% alpha, which composites the rest tone to ~1.9:1 on the
          canvas, under SC 1.4.11, on a rail where colour is the only thing saying what is on
          screen. Padded by the same token, a list that fits shows every marker at full strength,
          and one that overflows still fades the markers it is cutting off. */}
      <div
        ref={listRef}
        data-session-map-list
        className="flex max-h-full w-full flex-col overflow-y-auto overscroll-contain py-xl"
        style={{ maskImage: EDGE_FADE, WebkitMaskImage: EDGE_FADE }}
      >
        {entries.map((entry, i) => (
          // 🔴 THE ROW DECLARES NO HEIGHT, AND `shrink-0` IS THE ONLY REASON IT EXISTS. The pitch is
          // declared ONCE, on the marker itself, because the marker IS the pitch. What the row is
          // for is the classic flex trap: once the list's content outgrows `max-h-full`, a
          // shrinkable column child shrinks instead of letting the container scroll. `Popover`
          // renders its own wrapper around the trigger with a fixed class list, so `shrink-0` has
          // to live on an element this file owns.
          <span key={entry.markIndex} className="w-full shrink-0">
            <SessionMapMark
              entries={entries}
              index={i}
              isCurrent={i >= regionStart && i <= regionEnd}
              isTabStop={i === tabStop}
              expanded={pointedAt === i}
              reveal={reveal}
              buttonRef={(el) => { buttons.current[i] = el }}
              onPoint={(on) => setPointedAt((at) => (on ? i : at === i ? null : at))}
              onJump={() => jump(i)}
            />
          </span>
        ))}
      </div>
      {/* §A.6: announces the keyboard's own action, and ONLY while the rail holds focus — a hover
          must stay silent, so this is deliberately not driven by the card. */}
      <span data-session-map-live role="status" aria-live="polite" className="sr-only">{announcement}</span>
    </nav>
  )
}

/** One marker and the card it opens.
 *
 *  A component per marker rather than one card for the rail, because `Popover` anchors its flyout
 *  to ITS OWN trigger's measured rect — the whole point of a per-marker card is that it appears
 *  beside the marker you are pointing at, and a single rail-wide popover would anchor to the rail.
 */
function SessionMapMark({ entries, index, isCurrent, isTabStop, expanded, reveal, buttonRef, onPoint, onJump }: {
  entries: SessionMapEntry[]
  index: number
  isCurrent: boolean
  isTabStop: boolean
  /** This is the marker the pointer or the keyboard is on — the only one that expands. */
  expanded: boolean
  /** The rail's one-shot reveal intent (see the file header). Consumed here, on focus. */
  reveal: { current: boolean }
  buttonRef: (el: HTMLButtonElement | null) => void
  /** Claim or release the rail's one expanded marker. */
  onPoint: (on: boolean) => void
  onJump: () => void
}) {
  const entry = entries[index]
  const timer = useRef<number | null>(null)
  /** True while THIS card was opened by a cursor key, so blur closes what blur owns. */
  const kbOpen = useRef(false)

  const cancel = () => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
  }
  /** Arm ONE pending intent, replacing any other. Always cancelling first is what makes a pointer
   *  sweeping down the rail leave no armed opens behind it. */
  const after = (ms: number, fn: () => void) => {
    cancel()
    timer.current = window.setTimeout(() => { timer.current = null; fn() }, ms)
  }
  // A marker can unmount while an intent is armed (the transcript re-derives its entries on every
  // turn), and a timer firing into an unmounted component would toggle a dead popover.
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current) }, [])

  // The on-screen tone and the rest tone — `design/schemeContrast.test.ts` parses THIS declaration
  // and measures both in 12 schemes × 2 modes, so keep it in this one shape.
  const tone = isCurrent ? 'var(--color-primary)' : 'var(--color-map-rest)'
  // The pointed-at marker brightens: the accent's higher-contrast shade on screen, the strongest
  // ink off it (see the header's tone note).
  const lit = isCurrent ? 'var(--color-primary-emphasis)' : 'var(--color-on-surface)'

  return (
    <Popover
      portal
      placement="right"
      width={CARD_WIDTH}
      trigger={(open, toggle) => (
        <button
          type="button"
          ref={buttonRef}
          data-session-mark
          data-current={isCurrent || undefined}
          data-expanded={expanded || undefined}
          // The on-screen region, in the vocabulary assistive tech actually reads.
          aria-current={isCurrent ? 'true' : undefined}
          // ONE tab stop for the rail; the arrow keys move it (§A.6).
          tabIndex={isTabStop ? 0 : -1}
          aria-label={sessionMapMarkName(entries, index)}
          onClick={onJump}
          onMouseEnter={() => {
            onPoint(true)
            cancel()
            if (!open) after(OPEN_DELAY_MS, () => { kbOpen.current = false; toggle() })
          }}
          onMouseLeave={() => {
            onPoint(false)
            cancel()
            if (open) after(CLOSE_DELAY_MS, toggle)
          }}
          onFocus={() => {
            onPoint(true)
            // Consume the one-shot: a cursor key reveals the card, a Tab does not (see REVEAL).
            const wanted = reveal.current
            reveal.current = false
            if (wanted && !open) { kbOpen.current = true; toggle() }
          }}
          onBlur={() => {
            onPoint(false)
            cancel()
            if (open && kbOpen.current) { kbOpen.current = false; toggle() }
          }}
          // THE TARGET, AND THE PITCH — one declaration for both, because they are the same box.
          // `h-6` × `w-full` is 24×32 (SSM-8); `items-center` puts the 2px line on the row's centre
          // line; `justify-start` grows it rightward from the rail's inner edge.
          className="flex h-6 w-full items-center justify-start"
          // `color`, NOT `background`: the painted thing is the LINE inside the row, and the row
          // itself must stay transparent or the rail would be a solid block. The line inherits it
          // through `currentColor`.
          style={{ color: expanded ? lit : tone }}
        >
          {/* THE MARKER. A 24px line at 2px tall, resting at `MARK_REST_SCALE` and scaled from its
              left edge to full length while pointed at — the rail's one animation, and the only
              thing `prefers-reduced-motion` has to collapse. `initial={false}` so a rail of many
              markers does not animate on mount. */}
          <motion.span
            aria-hidden
            data-session-map-mark-line
            className="h-0.5 w-6 origin-left rounded-pill"
            initial={false}
            animate={{ scaleX: expanded ? MARK_EXPANDED_SCALE : MARK_REST_SCALE }}
            transition={physics.snappy}
            style={{ background: 'currentColor' }}
          />
        </button>
      )}
    >
      {/* `close` restores focus to the trigger — `Popover`'s contract for a dismissal, and correct
          here unmodified: a programmatic focus following a pointer gesture does not paint
          `:focus-visible`, so the rail gains no stray ring. */}
      {(close) => (
        <SessionMapCard
          entry={entry}
          onMouseEnter={cancel}
          onMouseLeave={() => after(CLOSE_DELAY_MS, close)}
        />
      )}
    </Popover>
  )
}
