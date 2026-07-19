import { forwardRef } from 'react'
import { motion } from 'framer-motion'
import { Composer } from './Composer'
import { bounce, expr } from '../design/motion'
import type { ComposerProps } from './composer/types'

/** The composer as a single persistent, shared-layout element. Forwards a ref to
 *  its outer box so the full-bleed <DotGlow> can measure it live each frame and
 *  cast a uniform, perfectly-synced glow from its edges. Passes all composer
 *  props straight through (the composer is configurable via `controls`).
 *
 *  Redesign-v2: the layoutId morph is the composer FLYING from the new-chat hero
 *  (screen-centered) down into the bottom dock when a chat starts — a signature
 *  motion moment. Its settle now scales through the expressiveness knob: bold
 *  lands with a little overshoot/life, refined glides in near-critically-damped.
 *  (bounce.settle already tracks the bounciness knob; expr nudges the stiffness so
 *  the whole flight reads calmer when refined.) */
export const ComposerStage = forwardRef<HTMLDivElement, ComposerProps>(function ComposerStage(props, ref) {
  const morph = { ...bounce.settle, stiffness: 200 + expr(80, 0.4) }
  return (
    <motion.div ref={ref} layoutId="composer-stage" transition={morph} className="relative z-10 w-full" style={{ maxWidth: 'var(--content-width)' }}>
      <Composer {...props} />
    </motion.div>
  )
})
