import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import type { SessionMark } from './sessionMap'
import { SESSION_MAP_MIN_MARKS } from './sessionMap'
import { Popover } from '../../ui/Popover'
import { SessionMapCard, sessionMapCardContent, sessionMapMarkName } from './SessionMapCard'
import { currentMarkRange, useVisibleTurns } from './sessionMapRegion'
import { physics } from '../../design/motion'

/** SESSION MAP RAIL — the marks (SEMANTIC-SESSION-MAP §A.1/§A.5, atoms SSM-4/5/6/7/8).
 *
 *  A vertical rail that indexes the transcript: one mark per `SessionMark` (SSM-1's contract),
 *  stacked on a constant pitch in the transcript's left gutter.
 *
 *  ── THE REFERENCE, AND WHAT WAS TAKEN FROM IT ───────────────────────────────────────────────
 *
 *  The owner asked for Codex's rail. Codex ships one — the OpenAI client's *thread user-message
 *  navigation rail* — and its design was read off the shipped implementation rather than guessed:
 *  `openai.chatgpt` VS Code extension, `webview/assets/thread-user-message-navigation-rail-app-*.
 *  {css,js}`. Seven properties were adopted, each because it answers something the previous
 *  dot-on-a-spine form got wrong:
 *
 *   1. A MARK IS A LINE, NOT A DOT, and its LENGTH is the primary channel (Codex: a 26×2px line
 *      whose `scaleX` runs 6px → 26px). A dot can only say "something is here"; a line of varying
 *      length says how much and what kind, at a glance, before any colour is read.
 *   2. LENGTH ENCODES STRUCTURE. Codex keys idle length on `data-hierarchy-level` (12 / 8 / 4px for
 *      levels 1-3), so its rail reads as a document outline. PC's mark vocabulary has a natural
 *      two-level hierarchy the old rail flattened: a TURN mark (`user`/`assistant`) is the heading,
 *      a sub-event mark (`tool`/`approval`/`error`/`subagent`/`activity`) is the work inside it. So
 *      `TURN_STEP` lengthens the turns and the rail becomes legible as an outline of the session.
 *   3. THE LENS. Hovering one mark takes it to full length and drags its ±3 neighbours after it —
 *      1 → .7 → .4 → .2, verbatim the falloff Codex's CSS applies through sibling selectors. This
 *      is the single thing that makes the rail feel alive under a pointer, and it is why a mark
 *      does not need to be large to be findable.
 *   4. CONSTANT PITCH + THE RAIL SCROLLS ITSELF. The old rail spaced marks by ordinal FRACTION over
 *      the full transcript height, so a long session compressed 200 marks into an unreadable smear
 *      and every mark moved whenever one was added. Codex gives every mark the same pitch and lets
 *      the rail overflow (`max-h`, `overflow-y-auto`), fading at both edges. A mark's size is then
 *      a constant, not a function of how long you have been talking.
 *   5. THE CARD SITS BESIDE THE MARK, not under it (Codex: `side="right"`, centred on the mark).
 *      On a 10px pitch a card placed below covers the twenty marks under it — the sweep-down-the-
 *      rail gesture the lens exists for stops working. `ui/Popover` gained `placement="right"` for
 *      this; see its prop doc for why that side deliberately does not flip.
 *   6. MOTION IS LENGTH ONLY, NEVER COLOUR. Codex transitions `transform` and nothing else, at
 *      160ms, and zeroes the duration under `prefers-reduced-motion`. PC gets both properties for
 *      free by handing `physics.snappy` — a gated getter — to framer.
 *   7. `aria-current`. Codex marks every on-screen item `aria-current="true"`. PC carried the same
 *      fact as a `data-` attribute only, so the current region was announced to nobody.
 *
 *  🔴 WHAT WAS DELIBERATELY NOT TAKEN, and each is a real divergence rather than an oversight:
 *
 *   · CODEX'S OPACITY RAMP (.4 idle / .6 current / 1 active). `design/schemeContrast.test.ts`
 *     measures BOTH of this rail's mark tones against `--color-canvas` in all 12 schemes at full
 *     opacity, and its own comment records `primary → canvas` already at 4.37-4.41. An alpha would
 *     make 24 live assertions optimistic about a ratio that is itself the subject of an open owner
 *     ruling. Length carries the emphasis instead; the tones stay opaque and measured.
 *   · CODEX'S INK-ONLY PALETTE. Codex paints its current mark `--color-text`, i.e. no accent at
 *     all. PC keeps coral, because `web/DESIGN.md`'s One-Voice rule assigns "the agent / live /
 *     current" to `--color-primary` and the map is the clearest case of it. The redesign does not
 *     make coral the ONLY channel any more, which was the point: a current mark is now longer AND
 *     coral AND `aria-current`, so `(length, tone)` is jointly distinguishing for all four classes
 *     of mark and hue is no longer carrying the state alone.
 *   · PRESS-AND-DRAG SCRUBBING. Codex captures the pointer and scrolls the transcript `instant`
 *     while you drag down the rail. Not taken here: it needs `onJumpTo` to carry a scroll behaviour,
 *     and `ChatPage.jumpToTurn` documents a measured layout-timing defect on the drawer path that a
 *     second caller would have to re-reason about. The lens already tracks the pointer
 *     continuously, which is most of the same feel.
 *   · CODEX'S BOOKMARKS (a dot riding the end of the line). PC has no per-turn bookmark concept;
 *     inventing one here would be a product decision smuggled in as a visual detail.
 *   · OPENING THE CARD ON PASSIVE FOCUS. Codex's `onFocus` opens its tooltip. PC must not — see
 *     the Tab-through trap below — so the REVEAL discriminator stays.
 *
 *  ── THERE IS NO SPINE, AND THAT IS A DELETION, NOT AN OMISSION ──────────────────────────────
 *
 *  The previous form drew a 1px `--color-rail` hairline behind the ticks. Codex draws no track, and
 *  two independent measurements say PC should not either: `--color-rail` is `#f0f4f8` in light mode
 *  against a `--color-canvas` of the same value — 1.000:1, an element that paints nothing at all —
 *  and with marks on a constant pitch the column of lines already reads as a rail without one. So
 *  the track is gone rather than retinted, which needed no cross-surface token change and no visual
 *  baseline churn. `SessionMapRail.test.tsx` asserts its ABSENCE so it cannot drift back.
 *
 *  Nothing here is a coloured side-stripe: the Tone-Not-Line rule
 *  (`design/sideStripeDoctrine.test.ts`) bans a full-height coloured bar, and the tone lives in
 *  discrete marks that ARE the nav targets. Dropping the spine moves further from that line, not
 *  closer to it. Every value routes through a token, so `tokenLint.test.ts` passes.
 *
 *  ── SSM-5: THE CURRENT REGION IS WHAT IS ON SCREEN ──────────────────────────────────────────
 *
 *  The accented set is driven by `useVisibleTurns`' `IntersectionObserver` over `turnNodes`, not by
 *  the marks: `currentMarkRange` turns "these turns are on screen" into the contiguous mark slice
 *  that lights coral (`sessionMapRegion.ts` owns both, and the reasoning for the binary search and
 *  for the empty-set case). Codex derives its own `aria-current` set the same way, from an
 *  `IntersectionObserver` over the messages — so this is a shared mechanism, not a coincidence.
 *
 *  ── SSM-6/7: THE CARD, THE CURSOR, THE JUMP, AND EXACTLY ONE CARD ───────────────────────────
 *
 *  Each mark is a `<button>` and the TRIGGER for a `ui/Popover` card (`SessionMapCard`, §A.3).
 *
 *  🪤 PASSIVE FOCUS MUST NOT OPEN THE CARD, and the reason is Tab-through: the rail sits between
 *  the transcript and the composer, so a keyboard user reaching for the message box would flash a
 *  card on the way past (§A.3). Tab opens nothing. An EXPLICIT cursor key does — see REVEAL below.
 *
 *  The rail is ONE tab stop with a roving `tabIndex` (§A.6). The slot starts on the current region
 *  and the arrow keys move it; `Home`/`End` go to the ends and `PageUp`/`PageDown` step by
 *  `PAGE_STEP`. `Enter`/`Space` call `onJumpTo(mark.visibleIndex)` — `ChatPage`'s existing
 *  `jumpToTurn`, verbatim reuse, no new scroll machinery.
 *
 *  🔑 REVEAL: A CURSOR KEY OPENS THE CARD, PASSIVE FOCUS DOES NOT. A sighted keyboard user
 *  arrowing down a rail with no card is navigating blind — the card is the answer to "what is this
 *  one?", so §A.6 has the first Arrow reveal a mark rather than step past it. But SSM-6's
 *  Tab-through rule still has to hold. The discriminator is INTENT, not focus: a cursor key sets
 *  `reveal` immediately before it moves focus, and the receiving mark's `onFocus` CONSUMES that
 *  one-shot flag. Tab never sets it, so Tab still opens nothing.
 *
 *  🪤 AND THIS IS WHERE `ui/Popover` COULD NOT BE DRIVEN, so the mechanism is deliberately NOT the
 *  one that looks obvious. `Popover.openSignal` is one-way — `if (openSignal > 0) setOpen(true)`,
 *  with no prop, signal or ref that reaches `setOpen(false)`. Revealing card j through it while i
 *  was open would leave i open too: a rail of STACKED cards, one per arrow press. The card the
 *  cursor opened belongs to the mark that has FOCUS, and focus is a singleton the platform already
 *  maintains. So a mark closes its own card on `blur`, through the `toggle` the primitive already
 *  hands its trigger. Exactly-one-card is then a property of focus rather than a rule the rail has
 *  to coordinate, no shared primitive changes, and no second mechanism exists to drift from the first.
 *
 *  🪤 WHOEVER OPENED THE CARD CLOSES IT, which is why `kbOpen` exists. Blur-closes cannot be
 *  unconditional: a POINTER-opened card is dismissed by the pointer leaving it (§A.3's bridge lets
 *  the pointer cross the gap and select the excerpt), and a click into that card blurs the mark —
 *  so an unconditional blur-close would dismiss the card the user is reading. One flag, one owner.
 *
 *  🔴 AND THE CURSOR SCROLLS THE RAIL BEFORE IT FOCUSES, not after, because the rail now has a
 *  scroller of its own. `focus()` scroll-into-view fires a `scroll` event, and a portaled `Popover`
 *  closes on ANY scroll (capture) so the fixed flyout cannot drift off its anchor — so focusing
 *  first would have the reveal open a card and the scroll it caused close it again, on whichever
 *  side of the race the frame landed. `revealInRail` runs first and `focus({ preventScroll: true })`
 *  raises no scroll of its own, so the only scroll event happens while the NEW card does not yet
 *  exist (and closing the PREVIOUS one is what a moved cursor wants anyway).
 *
 *  ── SSM-8: HIT TARGET, FOCUS RING, REDUCED MOTION ───────────────────────────────────────────
 *
 *  · THE PRESSABLE AREA IS THE WHOLE ROW, and the rows are flush — Codex's geometry (a 10px-tall,
 *    36px-wide button per mark, stacked with no gap) means every pixel of the rail belongs to some
 *    mark and there is no dead space to miss. Measured against what it replaces: a 4px tick plus a
 *    24px-wide band was ~96px² of target with gaps between; this row is ~320px² with none.
 *  · 🔴 AND THAT IS WHY THIS RAIL NO LONGER ADOPTS `.hit-24-x`. Two reasons, and the second is the
 *    one that matters. (1) The band centres a FIXED 24px on the element's own width, so it is a fix
 *    only for an element NARROWER than that — this row is 32px and the pseudo-element now sits
 *    entirely inside the target it was added to enlarge. Keeping it would be inert decoration that
 *    `design/hitTargetThinHandle.test.ts` would go on crediting. (2) The band is HORIZONTAL only,
 *    by design — `top: 0; bottom: 0` pins it to the element's own height, because on a rail whose
 *    marks sit a few px apart a 24px VERTICAL band would swallow the marks above and below (the
 *    stolen-target defect that file records twice). But on a VERTICAL list the horizontal axis was
 *    never the constraint: the old mark was 4px TALL, and no amount of width fixed that. This
 *    redesign fixes the axis that was actually failing, 4px → 10px, by giving the whole row to the
 *    mark — which is a thing the utility could not do and was never asked to.
 *    🪤 STATED PLAINLY RATHER THAN CLAIMED AS A PASS: a 32×10 row still does not meet WCAG 2.2
 *    SC 2.5.8's 24×24, and flush 10px rows do not qualify for its spacing exception either. Neither
 *    did the form this replaces (4px tall, WITH gaps), and Codex's own 36×10 row does not. The
 *    coarse-pointer surface is where that floor is owed and met: SSM-10's drawer rows are asserted
 *    ≥44px in `e2e/sessionMap.spec.ts`. This rail is the fine-pointer form, and the browser gate
 *    now measures its real row box so the improvement is a number rather than a claim.
 *  · 🔴 AND THE ROW HAS TO CLEAR `NavRail`'s BAND, which is why this rail carries a fixed `ml-4`.
 *    The rail mounts flush against the shell's content-column edge, and `ui/NavRail`'s resize
 *    splitter sits on the other side of exactly that seam wearing the SAME `.hit-24-x` band plus
 *    `z-10`. Measured at 1280x420 on a 196px nav: splitter box 192-196, so its band spans 182-206.
 *    Before this clearance existed `document.elementFromPoint` at every tick's own centre returned
 *    the SPLITTER — 6 of 6 — so EVERY mark was un-clickable by pointer at the default nav width, on
 *    every desktop viewport (the seam does not move with width). `ml-4` puts the rail's left edge at
 *    212, clear of 206, and the row spans rightward from there.
 *    🪤 FIXED `ml-4`, NOT the t-shirt `ml-l`, and this is the part that would rot silently. The
 *    clearance is owed to `--hit-min`, a FIXED 24px WCAG 2.2 SC 2.5.8 floor — but `--spacing-l`
 *    is `16px * var(--space-scale)`, and `--space-scale` is a user density preference that
 *    `tokens.css` drops to 0.8 and 0.68. On the compact densities `ml-l` would be 12.8px and
 *    10.9px against a 14px requirement, so the stolen target would come back for exactly the
 *    users who chose tighter spacing, invisibly. Tailwind's numeric scale is not re-mapped here
 *    (no `--spacing:` override), so `ml-4` is a real 16px at every density.
 *  · NO `outline-none`: the mark takes the global `:focus-visible` ring (`tokens.css` — 2px opaque
 *    `--color-primary`, no alpha, `focusRingContrast.test.ts`) rather than minting a local one.
 *    Codex sets `outline-none` on its own buttons and shows focus only by taking the marker to full
 *    length; PC keeps the ring AND takes the mark to full length.
 *  · THE LENGTH IS THE RAIL'S ONE ANIMATION. It runs on `physics.snappy`, a gated getter, so
 *    `prefers-reduced-motion` collapses it to `instant` through the module's one off-switch
 *    (`reducedMotionAppWide.test.ts`) instead of a hand-rolled spring here. Codex reaches the same
 *    place from the other direction, zeroing its `transition-duration` under the same query.
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
 *  the very turn it is describing. (Codex's own cap is `min(20rem, 100vw - 16px)`.) */
const CARD_WIDTH = 300

/** Pointer-intent delays (§A.3). The OPEN delay is what stops a pointer merely CROSSING the rail
 *  on its way to the transcript from strobing a card per mark; the CLOSE delay is the bridge that
 *  lets the pointer leave the mark, cross the gap and land on the card — without it the card
 *  dismisses itself the instant you try to read it. */
const OPEN_DELAY_MS = 150
const CLOSE_DELAY_MS = 120

/** `PageUp`/`PageDown` step (§A.6). Marks, not turns: the cursor moves over marks, and a turn can
 *  emit up to seven of them, so a turn-sized page would be a different distance on every press. */
const PAGE_STEP = 5

/** How short a mark gets at progress 0, as a fraction of its full length. Codex's own floor is
 *  6/26 ≈ .23; a quarter of the 24px line is 6px, which is still unambiguously a mark and not a
 *  dot. Below about this the line stops reading as a line. */
const MARK_MIN_SCALE = 0.25

/** The structural step: how much length a TURN mark carries over a sub-event mark at rest. This is
 *  what makes the rail an outline rather than a list — Codex's `data-hierarchy-level` in PC's own
 *  vocabulary. .34 of the 24px line is 6px, so a turn idles at 12px against a sub-event's 6px. */
const TURN_STEP = 0.34

/** The current-region step, added on top. A current turn therefore idles at ~21px and a current
 *  sub-event at ~12px — the same length a HISTORY turn idles at, which is deliberate and not a
 *  collision: tone separates those two, so the pair `(length, tone)` is distinguishing for all
 *  four classes while neither channel has to carry the state by itself. */
const CURRENT_STEP = 0.34

/** THE LENS (Codex's falloff, read off its CSS). The mark under the pointer or cursor goes to full
 *  length and pulls its neighbours after it, by distance: ±1 → .7, ±2 → .4, ±3 → .2, nothing
 *  beyond. Expressed as an array because that is what it is — a measured curve, not a formula. */
const LENS = [1, 0.7, 0.4, 0.2] as const

/** The rail fades its own top and bottom edge so an overflowing mark list ends in a gradient
 *  rather than a hard cut (Codex: `--edge-fade-distance: 2.5rem`). `black` rather than a hex
 *  because a mask reads only alpha — and a hex literal is the one thing `tokenLint` never allows. */
const EDGE_FADE =
  'linear-gradient(to bottom, transparent, black var(--spacing-xl),'
  + ' black calc(100% - var(--spacing-xl)), transparent)'

/** A mark's length, as a 0..1 progress that `MARK_MIN_SCALE` maps onto the line's `scaleX`.
 *
 *  `max`, not a sum: the lens is an ATTENTION channel and the base is a STRUCTURE channel, so the
 *  pointed-at mark must reach full length whatever kind it is, and a lensed neighbour must never
 *  read SHORTER than it does at rest. Summing them did both wrong.
 */
export function markProgress(mark: SessionMark, isCurrent: boolean, lensDistance: number | null): number {
  const structural = mark.kind === 'user' || mark.kind === 'assistant' ? TURN_STEP : 0
  const base = Math.min(1, structural + (isCurrent ? CURRENT_STEP : 0))
  const lens = lensDistance !== null && lensDistance < LENS.length ? LENS[lensDistance] : 0
  return Math.max(base, lens)
}

/** Progress → the line's horizontal scale. */
export function markScaleX(progress: number): number {
  return MARK_MIN_SCALE + (1 - MARK_MIN_SCALE) * progress
}

/** Keep the cursor's mark inside the rail's OWN scroller (Codex does the same, for the same
 *  reason: a roving cursor that walks off the visible slice of the rail is invisible).
 *
 *  Rect-based rather than `offsetTop`-based: the mark is absolutely positioned inside its row, so
 *  its `offsetTop` is 0 relative to the row and would place every mark at the top. jsdom reports
 *  zeroes for every rect, which makes this a no-op there rather than a wrong scroll. */
function revealInRail(list: HTMLElement | null, mark: HTMLElement | null) {
  if (!list || !mark) return
  const l = list.getBoundingClientRect()
  const m = mark.getBoundingClientRect()
  if (m.top < l.top) list.scrollTop -= l.top - m.top
  else if (m.bottom > l.bottom) list.scrollTop += m.bottom - l.bottom
}

export interface SessionMapRailProps {
  /** The ordered marks from `sessionMapMarks` (SSM-1). The rail renders one mark per entry. */
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
  /** The mark the LENS is centred on — whichever one the pointer or the keyboard is on, or `null`.
   *  Rail state rather than per-mark state, because the falloff is a property of the whole rail:
   *  a mark cannot know how far it is from a neighbour's hover. */
  const [lensAt, setLensAt] = useState<number | null>(null)
  /** What the rail says out loud. Empty whenever the rail does not hold focus (§A.6). */
  const [announcement, setAnnouncement] = useState('')
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const listRef = useRef<HTMLDivElement>(null)
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
      // `ml-4` is the clearance that keeps this rail's pressable band off `ui/NavRail`'s splitter
      // band — load-bearing, measured, and deliberately NOT a density-scaled token. See §A.8.
      // `items-center` is Codex's vertical centring: the mark list is as tall as it needs to be,
      // capped at the gutter's height, and sits in the middle of it rather than stretching.
      className="relative ml-4 flex h-full w-8 shrink-0 items-center"
    >
      {/* The mark list — a constant-pitch column that SCROLLS ITSELF once it outgrows the gutter,
          so a mark's size never depends on how long the session is. `overscroll-contain` keeps a
          wheel gesture that reaches the end of the rail from chaining into the transcript behind
          it. Scrollbars are hidden app-wide (`tokens.css`), so the fade mask is the only edge
          affordance and it needs no utility to suppress a bar. */}
      <div
        ref={listRef}
        data-session-map-list
        className="flex max-h-full w-full flex-col overflow-y-auto overscroll-contain"
        style={{ maskImage: EDGE_FADE, WebkitMaskImage: EDGE_FADE }}
      >
        {marks.map((mark, i) => (
          // 🔴 THE ROW DECLARES NO HEIGHT, AND `shrink-0` IS THE ONLY REASON IT EXISTS. The pitch is
          // declared ONCE, on the mark itself (see its className), because the mark IS the pitch —
          // two declarations would be a drift risk for the one number this layout turns on. What the
          // row is for is the classic flex trap: once the list's content outgrows `max-h-full`, a
          // shrinkable column child shrinks instead of letting the container scroll, so the marks
          // would compress rather than the rail overflowing. `Popover` renders its own wrapper
          // around the trigger and that wrapper's class list is fixed, so `shrink-0` has to live on
          // an element this file owns.
          <span key={mark.markIndex} className="w-full shrink-0">
            <SessionMapMark
              marks={marks}
              index={i}
              isCurrent={i >= regionStart && i <= regionEnd}
              isTabStop={i === tabStop}
              lensDistance={lensAt === null ? null : Math.abs(i - lensAt)}
              reveal={reveal}
              buttonRef={(el) => { buttons.current[i] = el }}
              onLens={(on) => setLensAt((at) => (on ? i : at === i ? null : at))}
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

/** One mark and the card it opens.
 *
 *  A component per mark rather than one card for the rail, because `Popover` anchors its flyout to
 *  ITS OWN trigger's measured rect — the whole point of a per-mark card is that it appears beside
 *  the mark you are pointing at, and a single rail-wide popover would anchor to the rail (and,
 *  since the rect is measured once on open, would strand the card at the first mark's position as
 *  the cursor moved on).
 */
function SessionMapMark({ marks, index, isCurrent, isTabStop, lensDistance, reveal, buttonRef, onLens, onJump }: {
  marks: SessionMark[]
  index: number
  isCurrent: boolean
  isTabStop: boolean
  /** How many marks away the lens centre is, or `null` when nothing is lensed. */
  lensDistance: number | null
  /** The rail's one-shot reveal intent (see the file header). Consumed here, on focus. */
  reveal: { current: boolean }
  buttonRef: (el: HTMLButtonElement | null) => void
  /** Claim or release the rail's lens centre. */
  onLens: (on: boolean) => void
  onJump: () => void
}) {
  const mark = marks[index]
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
  // A mark can unmount while an intent is armed (the transcript re-derives its marks on every
  // turn), and a timer firing into an unmounted component would toggle a dead popover.
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current) }, [])

  const tone = isCurrent ? 'var(--color-primary)' : 'var(--color-on-surface-low)'
  const scaleX = markScaleX(markProgress(mark, isCurrent, lensDistance))

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
          data-kind={mark.kind}
          data-current={isCurrent || undefined}
          // The current region, in the vocabulary assistive tech actually reads — the same
          // attribute Codex puts on every on-screen item. `data-current` alone announced nothing.
          aria-current={isCurrent ? 'true' : undefined}
          // ONE tab stop for the rail; the arrow keys move it (§A.6).
          tabIndex={isTabStop ? 0 : -1}
          aria-label={sessionMapMarkName(marks, index)}
          onClick={onJump}
          onMouseEnter={() => {
            onLens(true)
            cancel()
            if (!open) after(OPEN_DELAY_MS, () => { kbOpen.current = false; toggle() })
          }}
          onMouseLeave={() => {
            onLens(false)
            cancel()
            if (open) after(CLOSE_DELAY_MS, toggle)
          }}
          onFocus={() => {
            onLens(true)
            // Consume the one-shot: a cursor key reveals the card, a Tab does not (see REVEAL).
            const wanted = reveal.current
            reveal.current = false
            if (wanted && !open) { kbOpen.current = true; toggle() }
          }}
          onBlur={() => {
            onLens(false)
            cancel()
            if (open && kbOpen.current) { kbOpen.current = false; toggle() }
          }}
          // THE TARGET, AND THE PITCH — one declaration for both, because they are the same box.
          // `h-2.5` × `w-full` is 10×32; `items-center` puts the 2px line on the row's centre line;
          // `justify-start` grows it rightward from the rail's inner edge, which is what makes the
          // lens read as a bulge rather than a shimmer.
          //
          // 🪤 NOT `absolute inset-0`, AND THE FIRST DRAFT SHIPPED EXACTLY THAT AND RENDERED A RAIL
          // OF ZERO-SIZE BUTTONS. `ui/Popover` wraps whatever its `trigger` returns in its own
          // `<div className="relative">` — the anchor it measures — and that wrapper carries no size
          // and, being `relative`, becomes the containing block for anything absolute inside it. So
          // `inset-0` resolved against an auto-sized empty div rather than against the row, and every
          // mark measured 32×0 in a real browser. Every jsdom assertion passed: `getByRole` found the
          // buttons, their names were right, hover and Enter worked, and the marks were nonetheless
          // invisible and unclickable — Playwright's own log read "element is not visible" 321 times.
          // Explicit dimensions need no containing block, so they cannot be captured by a wrapper this
          // file does not control. (The tick form this replaces was immune by accident: it carried its
          // own `size-1`, so the zero-size wrapper never mattered and its header called the wrapper a
          // deliberate "zero-size LOCATOR".)
          className="flex h-2.5 w-full items-center justify-start"
          // 🔴 `color`, NOT `background`: the painted thing is now the LINE inside the row, and the
          // row itself must stay transparent or the rail would be a solid block. The line inherits
          // it through `currentColor` — Codex's own trick, and it keeps this declaration in the one
          // shape `design/schemeContrast.test.ts` parses out of this file to measure both tones in
          // 12 schemes. Do not inline it into the line's style: that rail throws rather than
          // silently measuring nothing.
          style={{ color: tone }}
        >
          {/* THE MARK. A 24px line at 2px tall, scaled horizontally from its left edge — the
              rail's one animation, and the only thing `prefers-reduced-motion` has to collapse.
              `initial={false}` so a rail of 200 marks does not animate on mount; `physics.snappy`
              so reduced motion collapses it through the module gate rather than a local spring. */}
          <motion.span
            aria-hidden
            data-session-map-mark-line
            className="h-0.5 w-6 origin-left rounded-pill"
            initial={false}
            animate={{ scaleX }}
            transition={physics.snappy}
            style={{ background: 'currentColor' }}
          />
        </button>
      )}
    >
      {/* `close` restores focus to the trigger — `Popover`'s contract for a dismissal, and correct
          here unmodified: a programmatic focus following a pointer gesture does not paint
          `:focus-visible`, so the rail gains no stray ring, and the mark the pointer just left is
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
