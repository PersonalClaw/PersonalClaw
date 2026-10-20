import { useEffect } from 'react'
import { animate, motion, useMotionValue } from 'framer-motion'
import { sweep } from '../../design/motion'

/** The Stop control's sweep (`docs/design/agent-orb.md`): a quarter arc in the button's own ink,
 *  just inside its rim and outside the square, turning once per lap of `design/motion`'s `sweep()`.
 *  Decoration over the button, so it takes no pointer and no name. The composer draws it only when
 *  `sweep()` gives a lap: under reduced motion there is no arc at all.
 *
 *  It turns while `turning`, and otherwise holds still where it is, as the agent orb holds still
 *  while the turn waits on the owner. It turns on from there when `turning` comes back, so a turn
 *  that waited never jumps the arc to the top. The lap starts only when `turning` does: a re-render
 *  for a streamed word changes nothing, and the Loading sweep slider is read when a lap starts. */
export function StopSweep({ turning }: { turning: boolean }) {
  const rotate = useMotionValue(0)
  useEffect(() => {
    const lap = turning ? sweep() : null
    if (!lap) return
    const from = rotate.get() % 360
    const run = animate(rotate, [from, from + 360], lap)
    return () => run.stop()
  }, [turning, rotate])
  return (
    <motion.span aria-hidden data-stop-sweep-arc
      className="pointer-events-none absolute inset-0 text-on-primary" style={{ rotate }}>
      <svg viewBox="0 0 40 40" fill="none" className="size-full">
        <circle cx={20} cy={20} r={17} stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" pathLength={100} strokeDasharray="25 75" />
      </svg>
    </motion.span>
  )
}
