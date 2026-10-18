import { useState, useEffect, useRef } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Spark } from '../Spark'
import { spring, physics } from '../../design/motion'
import { phaseLabel, type WorkPhase } from '../../lib/workPhase'

const CURATED_PHRASES = [
  'Connecting the dots…',
  'Sketching an answer…',
  'Turning it over…',
  'Weighing the angles…',
  'Pulling it together…',
  'Reading between the lines…',
  'Almost there…',
]

const ROTATION_MS = 2500

interface Props {
  /** The turn's phase, as the gateway measured it (`work_phase`); `null` until it has said one, for
   *  a turn this page has just sent: accepted, not started yet. */
  phase: WorkPhase | null
  /** The gateway's line for the phase ("Searching your knowledge", the tool's own label). */
  line: string
}

/** The chat's indicator for a turn that has not shown anything yet: the phase's own word, and the
 *  gateway's line when it says more than that word does. Nothing here is read out of the words: a
 *  step whose label mentions thinking is still the step it is. */
export function StreamingIndicator({ phase, line }: Props) {
  const reduce = useReducedMotion()
  const label = phaseLabel(phase ?? 'queued')
  const says = line.trim() && line.trim().toLowerCase() !== label.toLowerCase() ? line.trim() : null
  const [sentence, setSentence] = useState<string>(() => says || CURATED_PHRASES[0])
  const curatedIdx = useRef(0)

  useEffect(() => {
    if (says) {
      setSentence(says)
      return
    }
    const id = setInterval(() => {
      curatedIdx.current = (curatedIdx.current + 1) % CURATED_PHRASES.length
      setSentence(CURATED_PHRASES[curatedIdx.current])
    }, ROTATION_MS)
    return () => clearInterval(id)
  }, [says])

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4, transition: spring.effects }}
      transition={physics.playful}
      className="flex items-start gap-m py-s"
    >
      {/* Claw mark with breathing coral glow */}
      <div className="relative mt-0.5 shrink-0">
        <Spark size={20} animated />
        <motion.span
          aria-hidden
          className="absolute inset-0 rounded-full"
          style={{ background: 'radial-gradient(circle, color-mix(in srgb, var(--color-primary) 35%, transparent), transparent 70%)' }}
          animate={reduce ? { opacity: 0.4 } : { opacity: [0.3, 0.7, 0.3], scale: [1, 1.3, 1] }}
          transition={{ duration: 2.4, ease: 'easeInOut', repeat: Infinity }}
        />
      </div>

      <div className="flex min-w-0 flex-col gap-xs">
        {/* The phase's own word, with coral text shimmer */}
        <span data-type="label-m" data-phase-label className="text-shimmer-primary" aria-live="polite">
          {label}
        </span>

        {/* Flipping sentence line */}
        <div className="relative h-5 overflow-hidden">
          <AnimatePresence mode="popLayout">
            <motion.p
              key={sentence}
              data-type="body-m"
              className="absolute inset-x-0 truncate text-on-surface-low"
              initial={reduce ? { opacity: 0 } : { rotateX: 90, opacity: 0, y: 4 }}
              animate={reduce ? { opacity: 1 } : { rotateX: 0, opacity: 1, y: 0 }}
              exit={reduce ? { opacity: 0 } : { rotateX: -90, opacity: 0, y: -4 }}
              transition={spring.spatialDefault}
              style={{ transformOrigin: 'center center', perspective: 600 }}
            >
              {sentence}
            </motion.p>
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  )
}
