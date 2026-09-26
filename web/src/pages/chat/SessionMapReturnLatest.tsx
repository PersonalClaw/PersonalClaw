import { AnimatePresence, motion } from 'framer-motion'
import { ArrowDown } from 'lucide-react'
import { spring } from '../../design/motion'

/** SESSION MAP — RETURN-TO-NEWEST (SEMANTIC-SESSION-MAP §A.7, atom SSM-9).
 *
 *  The map's canonical "back to newest" affordance. It is the control the chat transcript has always
 *  shown when you scroll up — same accessible name, same gate, same gesture — MOVED here, because
 *  §A.7's instruction is the whole atom: **do not invent a new control.**
 *
 *  🔑 WHY MOVING A COMPONENT IS THE DELIVERABLE, and not a filing preference. The Session Map is the
 *  transcript's in-session navigation (§B.1: it *replaces* the Activity → Index tab and *subsumes*
 *  the ad-hoc jump affordances into one model). A navigation surface that owns "go to turn N" but
 *  not "go to the newest" would leave the second question answered by a control nobody owns — and
 *  the predictable next step is `SessionMapRail` (or SSM-10's coarse-pointer drawer) growing a
 *  second back-to-newest of its own, at which point the app has two controls for one intent, with
 *  two names, two hit targets and two ideas of where "newest" is. So the map owns exactly one, and
 *  `ChatPage` renders the map's rather than defining its own.
 *
 *  `SessionMapReturnLatest.test.tsx` holds that shape in place by DERIVATION over the `src` tree
 *  rather than by a list: exactly one non-test source file may define this control, and it must be
 *  this one. A duplicate anywhere in `web/src` — in the rail, in a drawer, back inline in
 *  `ChatPage` — turns that red.
 *
 *  🪤 THE FOLLOW-THE-STREAM EFFECT IS A DIFFERENT CONCERN AND MUST NOT BE UNIFIED WITH THIS.
 *  `ChatPage` also scrolls to the transcript end as tokens arrive (`ChatPage.tsx:1461`), and it
 *  looks like the same call. It is not: that one is CONDITIONAL (only while the reader is already
 *  near the bottom — the whole point is not to yank someone reading history) and INSTANT (no
 *  `behavior: 'smooth'`, because a per-token smooth scroll never settles). This one is an explicit
 *  user gesture: unconditional, and smooth precisely so the jump reads as travel rather than a
 *  teleport. Collapsing the two would make streaming either yank or crawl.
 *
 *  DESIGN LANGUAGE: a CIRCULAR down-arrow — the owner's reference (2026-09-25) shows exactly this
 *  once the reader leaves the newest message, and the words it used to carry are the accessible
 *  name (and the tooltip) rather than a label competing with the transcript. It is chrome, not
 *  content — `bg-surface/95` + `backdrop-blur-md` over the transcript, `border-outline-variant/50`,
 *  the arrow at `text-on-surface-var` brightening to `text-on-surface` on hover — and 32px, above
 *  SC 2.5.8's 24px floor. Entrance/exit ride `spring.spatialFast`, a gated getter, so
 *  `prefers-reduced-motion` collapses it through `design/motion`'s single off-switch
 *  (`reducedMotionAppWide.test.ts`) instead of a hand-rolled transition here.
 */

/** The one accessible name. §A.7: the control "keeps its text accessible name", so this is also
 *  what `toggleLabelInName`-style name rails read. Exported because the single-implementation
 *  derivation asserts on the literal, and a second copy of the string is exactly what it hunts. */
export const RETURN_TO_LATEST_LABEL = 'Jump to latest message'

/** The one gesture: bring the transcript's end anchor into view, smoothly.
 *
 *  Takes the ELEMENT rather than the ref so the map does not reach into `ChatPage`'s ref cell, and
 *  so a caller with no anchor yet (an unmounted or empty transcript) is a no-op rather than a
 *  crash — the control can only be visible once the transcript has scrolled, but the `?.` costs
 *  nothing and removes the ordering question entirely. */
export function scrollToLatest(end: Element | null | undefined) {
  end?.scrollIntoView({ behavior: 'smooth', block: 'end' })
}

export interface SessionMapReturnLatestProps {
  /** True while the transcript is scrolled away from its bottom (`ChatPage.tsx:580`, a distance
   *  threshold on the scroll container). §A.7 pins the gate to exactly this: the control is absent,
   *  not disabled, when there is nothing to return to. */
  scrolledUp: boolean
  /** What "newest" means to the host — in `ChatPage`, `scrollToLatest(endRef.current)`. Injected
   *  rather than resolved here because the transcript's end anchor belongs to the transcript. */
  onReturnToLatest: () => void
}

export function SessionMapReturnLatest({ scrolledUp, onReturnToLatest }: SessionMapReturnLatestProps) {
  return (
    <AnimatePresence>
      {scrolledUp && (
        <motion.button type="button" onClick={onReturnToLatest}
          aria-label={RETURN_TO_LATEST_LABEL}
          title={RETURN_TO_LATEST_LABEL}
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={spring.spatialFast}
          className="absolute left-1/2 -top-2 z-20 -translate-x-1/2 inline-flex size-8 items-center justify-center rounded-full border border-outline-variant/50 bg-surface/95 text-on-surface-var shadow-md backdrop-blur-md transition-colors hover:bg-surface-high hover:text-on-surface">
          <ArrowDown size={16} aria-hidden />
        </motion.button>
      )}
    </AnimatePresence>
  )
}
