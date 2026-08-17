import { createContext, useContext, type CSSProperties, type ReactNode } from 'react'
import { motion } from 'framer-motion'

import { listItemEnter, regionStagger } from '../../design/motion'

// ── Orchestrated surface entrance (plan FLUID-MOTION §S3 T3.2) ──────────────────
// A page's top-level bands cascade in on arrival instead of all landing together.
// One group per surface, one region per band; the choreography itself is
// `design/motion.regionStagger()`, so retuning the whole app's cascade is one
// number in one file and no surface can opt into a different feel.
//
// THREE properties this pair exists to make un-forgettable, because each of them is
// a defect a per-page `motion.div` would reintroduce:
//
//  1. **Reduced motion is an ABSENCE, not a speed.** `regionStagger()` returns null
//     and both components then render plain `<div>`s — no variants, no hidden
//     initial state, no transition. There is no "fast cascade" tier.
//  2. **An entrance NEVER gates content.** Regions are always rendered, always in
//     the DOM, and always interactive on the first commit; the animation only
//     decorates their arrival. Nothing here waits on an animation to mount a child,
//     which is the FM-5 precedent applied one level down.
//  3. **It plays on MOUNT and nothing re-renders it.** See the replay rule below.
//
// **The replay rule.** An entrance plays exactly once, when its `EntranceGroup`
// mounts — which for a route surface is once per navigation to it (`app/App.tsx`
// keys the route wrapper on the route, so arriving mounts the page). Everything
// that happens afterwards on a live surface — a WebSocket push, a `refresh()`, a
// filter change, opening a detail panel — is a RE-RENDER of a mounted group, and a
// re-render never replays a Framer entrance. Two placement rules keep it that way,
// and they are the part a call site can get wrong:
//   • put the group ABOVE every data-dependent branch when its regions are static
//     (the dashboard's bands, the inbox's body), or ON the loaded column when the
//     regions ARE the data (Discover's areas);
//   • never give a group or a region a key derived from data.
// `lib/useCachedData` is what makes the first rule sufficient: a same-key
// revalidation HOLDS the last value rather than dropping to `undefined`, so a
// refresh cannot flip a surface back through its skeleton branch and remount the
// group underneath. A surface that re-parents its own blocks after layout
// (settings' masonry re-packs on every commit) is NOT a candidate — it would
// remount blocks and replay their entrance on a resize.

/** Is there a live entrance above us?
 *
 *  Regions read the decision from the group instead of re-asking `regionStagger()`,
 *  and that is load-bearing rather than tidy: a region that answered for itself could
 *  end up variant-driven under a plain parent (if the media query flipped between the
 *  two renders), and a variant-driven region with no parent to propagate the `animate`
 *  label stays at `opacity: 0` FOREVER. Defaulting to `false` also makes the failure
 *  mode of forgetting the group a missing entrance, never missing content. */
const Entering = createContext(false)

type RegionProps = {
  children: ReactNode
  className?: string
  style?: CSSProperties
}

/** The orchestrator for one surface — put it on the element that already owns the
 *  page's column so the entrance costs no extra DOM. Its direct `EntranceRegion`
 *  children cascade in; any other child simply appears (a child with an explicit
 *  `animate` object of its own, like a list row, keeps its own animation — Framer
 *  only propagates a variant label to children that declare `variants`).
 *
 *  `data-entrance` states the decision on the element: `"staggered"` or `"none"`. It
 *  is how both the tests and the UX harness read which path ran, since the two
 *  branches are otherwise distinguishable only by inline styles Framer owns. */
export function EntranceGroup({ children, className, style }: RegionProps) {
  const transition = regionStagger()
  if (!transition) {
    return <div data-entrance="none" className={className} style={style}>{children}</div>
  }
  return (
    <Entering.Provider value={true}>
      <motion.div
        data-entrance="staggered"
        className={className}
        style={style}
        variants={{ animate: { transition } }}
        initial="initial"
        animate="animate"
      >
        {children}
      </motion.div>
    </Entering.Provider>
  )
}

/** One band of a surface. Rises + fades on `listItemEnter` — the same variant a list
 *  row uses, because a region arriving and a row arriving are the same gesture at two
 *  scales, and a second variant here would be a second vocabulary. */
export function EntranceRegion({ children, className, style }: RegionProps) {
  const entering = useContext(Entering)
  if (!entering) {
    return <div data-entrance-region="none" className={className} style={style}>{children}</div>
  }
  return (
    <motion.div data-entrance-region="staggered" variants={listItemEnter} className={className} style={style}>
      {children}
    </motion.div>
  )
}
