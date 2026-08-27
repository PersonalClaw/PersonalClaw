import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Compass, ArrowUpRight, ArrowRight, Settings, X } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState, SlotAction } from './kit'
import { instant, physics, spring } from '../../../design/motion'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import type { DiscoverTip, DiscoverTryIt } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'

// How many cards are drawn behind the front one — so at most five edges are ever
// visible, however many tips exist. Past that the sliver-per-card is too thin to
// read as anything but noise, and each layer is a real DOM node behind an
// interactive card.
const BEHIND = 4

// Per-layer offsets, in px/scale/opacity. Cards recede UP and BACK: each step is
// smaller, dimmer, and nudged up so the deck reads as depth rather than a list.
// Kept tight (a 4px lip, 2% shrink) so five layers occupy a small band above the
// front card instead of a tall ramp.
const STEP_Y = 4
const STEP_SCALE = 0.02
const STEP_OPACITY = 0.3

// Room above the front card for the receding lips. Derived, so changing BEHIND or
// STEP_Y can't silently clip the topmost one.
const DECK_PAD = BEHIND * STEP_Y + 2

/** Discover (§6) — the dashboard's curated spotlight, rendered as a physical DECK:
 *  one tip in front, the rest stacked behind it, receding and dimming with depth.
 *  Dismissing the front card removes it and the next one rises into its place —
 *  same shared-layout spring the rest of the shell uses, so the promotion reads as
 *  the card actually moving forward rather than a list re-flowing.
 *
 *  Why a deck and not a list: the tips are a queue the user works through one at a
 *  time, and a deck says "one thing to look at now, more behind it" in a fraction of
 *  the vertical space three stacked cards took. The full grouped list stays on the
 *  Discover hub for anyone who wants to browse rather than be led.
 *
 *  Propose-don't-write: a tip only points (deep-links into an existing page) and
 *  hides (dismiss persists; an area auto-hides once used). It never enables or
 *  configures anything — the user acts. */
export function Discover({ navigate }: RouteProps) {
  const { discover, discoverErr, dismissDiscoverTip } = useDashboardLive()
  const reduce = useReducedMotion()

  // 🔴 ONE SENTENCE ANSWERED THREE DIFFERENT QUESTIONS. A single gate ORed a falsy feed together
  // with a disabled one onto "Discover tips are off.", and its own comment admitted the conflation
  // ("Kill switch off, OR nothing loaded yet"). So on the app's first screen a
  // FAILED read and a not-yet-arrived read both announced a setting the user never touched — and
  // the genuinely-off case, the only one that sentence is true for, offered no way to change it.
  // Measured on an empty home with `/api/legibility/discover` aborted: this slot said "Discover
  // tips are off." while `#/discover`, on the identical rejection, said "Couldn't load your tips"
  // with a Retry; and 1.6s into a delayed load it said the same thing again.
  //
  // Three conditions, three answers, all three borrowed from something already shipped:
  //  · failed  → the honest read-error slot `OnThisMachine`/`PinnedArtifacts` already use, with
  //              `#/discover`'s own noun for the thing ("your tips").
  //  · unread  → nothing, exactly as `OnThisMachine` and `PinnedArtifacts` do for `!data`. A slot
  //              that says nothing for a moment is not a claim; a wrong sentence is.
  //  · off     → the fact PLUS the on-ramp `#/discover`'s off-branch already carries, to the same
  //              route (`settings/legibility`) under the same label.
  if (discoverErr && !discover) {
    return <SlotEmptyState icon={Compass}>Couldn&rsquo;t load your tips.</SlotEmptyState>
  }
  if (!discover) return null
  if (!discover.enabled) {
    return (
      <SlotEmptyState
        icon={Compass}
        // Through `SlotAction`, not a `Button`: this control lives in a SLOT's action position, the
        // same position Tasks' "+ New task" and Schedule's "+ New trigger" occupy, and all three can
        // paint on the first screen at once. The ghost `Button` this replaced also carried an ink
        // override in `className`, landing two colour utilities on one element — `ghost` already
        // sets `text-on-surface` — so which ink won was decided by stylesheet ORDER, the hazard
        // `ui/Button`'s own `ghost-accent` note documents. The deck's "See all" footer below keeps
        // its `Button`: it belongs to the deck, not to a slot's sentence.
        action={<SlotAction icon={Settings} onClick={() => navigate('settings/legibility')}>Open Settings</SlotAction>}
      >
        Discover tips are off.
      </SlotEmptyState>
    )
  }
  const tips = discover.areas.flatMap((a) => a.tips)
  if (tips.length === 0) {
    return (
      <SlotEmptyState icon={Compass}>
        You&rsquo;ve explored every part of PersonalClaw. Nice.
      </SlotEmptyState>
    )
  }

  // Only the front card plus its backing layers are mounted. `dismissDiscoverTip`
  // refetches the slice, so the deck refills from the server rather than from a
  // local copy that could drift out of sync with what's actually dismissed.
  const deck = tips.slice(0, BEHIND + 1)

  return (
    <div className="flex flex-col gap-s">
      {/* The deck's height is the FRONT card's height (the backing layers are
          absolutely positioned), so the section doesn't jump as cards promote.
          The top padding is DERIVED from the deck's depth rather than guessed, so
          changing BEHIND or STEP_Y can't silently clip the topmost lip. */}
      <div className="relative" style={{ paddingTop: DECK_PAD }}>
        <AnimatePresence initial={false} mode="popLayout">
          {deck
            // Painted back-to-front so the front card wins the stacking order
            // without any of them needing an explicit z-index.
            .map((tip, depth) => ({ tip, depth }))
            .reverse()
            .map(({ tip, depth }) => (
              <TipCard
                key={tip.id}
                tip={tip}
                depth={depth}
                reduce={!!reduce}
                onGo={() => navigate(tryItPath(tip.try_it))}
                onDismiss={() => dismissDiscoverTip(tip.id)}
              />
            ))}
        </AnimatePresence>
      </div>
      <Button variant="ghost" size="xs" onClick={() => navigate('discover')} className="group self-start text-on-surface-var">
        {tips.length > 1 ? `See all ${tips.length} in Discover` : 'Open Discover'}
        <ArrowRight size={14} className="transition-transform group-hover:translate-x-px" />
      </Button>
    </div>
  )
}

/** One card in the deck. `depth` 0 is the front, interactive card; deeper cards are
 *  inert scenery — `pointer-events-none` and `aria-hidden` so neither the pointer
 *  nor a screen reader can reach a control the user cannot see properly. Only the
 *  front card is in the a11y tree, which is what "one tip at a time" should mean. */
function TipCard({ tip, depth, reduce, onGo, onDismiss }: {
  tip: DiscoverTip
  depth: number
  reduce: boolean
  onGo: () => void
  onDismiss: () => void
}) {
  const isFront = depth === 0
  // Reduced motion keeps the depth STAGING (a deck is information, not decoration)
  // but drops the springy promotion — layout animation is what's disorienting.
  // (Was a spread of the bounce tier UNDER spring.spatialDefault, which overwrote
  // every field of it — the tier contributed nothing. `fluid` is the tier this deck
  // wants: a generous settle for a card promoted to the front.)
  const transition = reduce ? instant : physics.fluid

  return (
    <motion.div
      layout
      // A stable layout id per card is what makes promotion a MOVE rather than an
      // unmount+mount: the card that was behind animates forward into the front
      // card's box instead of fading in at its final size.
      layoutId={`discover-card-${tip.id}`}
      initial={{ opacity: 0, scale: 1 - STEP_SCALE * (depth + 1), y: -STEP_Y * (depth + 1) }}
      animate={{
        opacity: isFront ? 1 : STEP_OPACITY,
        scale: 1 - STEP_SCALE * depth,
        y: -STEP_Y * depth,
        transition,
      }}
      // Dismissal slides the card out to the side and collapses it — a discard,
      // visually distinct from the promotion happening underneath it.
      exit={{ opacity: 0, scale: 0.96, x: 24, transition: reduce ? instant : spring.spatialFast }}
      aria-hidden={!isFront}
      // Backing cards are pinned to the FRONT card's baseline and given its full
      // height, so each one contributes exactly a STEP_Y lip above it — nothing
      // more. They render no children at all (see below), so there is no text to
      // bleed into the peek band.
      style={isFront ? undefined : { top: DECK_PAD, height: '100%' }}
      className={[
        'rounded-lg bg-surface-low',
        isFront ? 'relative flex flex-col gap-s px-m py-m' : 'pointer-events-none absolute inset-x-0',
        // A hairline is ALL the front card gets. A lift shadow here spilled a glow
        // out from behind it and over the cards underneath, which read as haze
        // rather than depth — the staging (scale + offset + dim) is what carries
        // the stack, so the chrome stays flat.
        isFront ? 'ring-1 ring-outline-variant/40' : 'ring-1 ring-outline-variant/25',
      ].join(' ')}
    >
      {/* A backing layer is a bare surface: an edge that says "another card is
          here". Rendering its real content would show text through the gap above
          the front card and read as a broken list rather than a deck. */}
      {!isFront ? null : (
      <>
      <div className="flex items-start gap-s">
        <Compass size={15} className="mt-0.5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <p data-type="label-l" className="truncate text-on-surface">{tip.title}</p>
          <p data-type="body-m" className="mt-xs text-on-surface-var">{tip.lesson}</p>
        </div>
        <IconButton
          icon={X}
          label="Dismiss — don't suggest this again"
          onClick={onDismiss}
          size={28}
          iconSize={14}
          className="-mr-xs shrink-0 text-on-surface-low"
        />
      </div>
      <Button variant="tonal" size="xs" onClick={onGo} className="group self-start">
        {tip.try_it.label}
        <ArrowUpRight size={13} className="transition-transform group-hover:translate-x-px group-hover:-translate-y-px" />
      </Button>
      </>
      )}
    </motion.div>
  )
}

/** Turn a `try_it` descriptor into a navigate() path. The route + query come from
 *  the backend, so the deep link stays server-authored — the widget serializes it. */
function tryItPath(t: DiscoverTryIt): string {
  const q = new URLSearchParams(t.query ?? {}).toString()
  return q ? `${t.route}?${q}` : t.route
}
