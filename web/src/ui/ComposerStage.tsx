import { forwardRef } from 'react'
import { motion } from 'framer-motion'
import { Composer } from './Composer'
import { physics, expr } from '../design/motion'
import type { ComposerProps } from './composer/types'
import { INSTANT } from './composer/rise'

type ComposerStageProps = ComposerProps & {
  /** The stage moves without a spring while this holds: a chat's new-chat hero, until the chat
   *  starts. A page settling around the hero (the column re-centring as the shell mounts, a strip
   *  landing above it) used to spring the stage on every load, and on a slow runner that spring's
   *  tail was still asking for frames when an idle Chat was measured (#3905, R6). The flight into
   *  the dock still springs: the dock's instance animates it, and this no longer holds there.
   *
   *  Not `layoutDependency={started}`: that broke the flight (92 style writes fell to 9). The
   *  hero's instance unmounts without rendering again, so with a dependency it never takes the
   *  snapshot the dock's instance flies from. A transition per state leaves every snapshot in
   *  place. */
  settled?: boolean
}

/** The composer as a single persistent, shared-layout element. Forwards a ref to
 *  its outer box so the full-bleed <DotGlow> can measure it live each frame and
 *  cast a uniform, perfectly-synced glow from its edges. Passes all composer
 *  props straight through (the composer is configurable via `controls`).
 *
 *  Redesign-v2: the layoutId morph is the composer FLYING from the new-chat hero
 *  (screen-centered) down into the bottom dock when a chat starts — a signature
 *  motion moment. Its settle now scales through the expressiveness knob: bold
 *  lands with a little overshoot/life, refined glides in near-critically-damped.
 *  (physics.fluid already tracks the bounciness knob; expr nudges the stiffness so
 *  the whole flight reads calmer when refined.) */
export const ComposerStage = forwardRef<HTMLDivElement, ComposerStageProps>(function ComposerStage({ settled = false, ...props }, ref) {
  const morph = settled ? INSTANT : { ...physics.fluid, stiffness: 200 + expr(80, 0.4) }
  return (
    <motion.div ref={ref} layoutId="composer-stage" data-composer-stage transition={morph} className="relative z-10 w-full" style={{ maxWidth: 'var(--content-width)' }}>
      <Composer {...props} settled={settled} />
    </motion.div>
  )
})
