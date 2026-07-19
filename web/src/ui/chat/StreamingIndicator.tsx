import { useState, useEffect, useRef, useMemo } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Spark } from '../Spark'
import { spring, bounce } from '../../design/motion'

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

type Phase = 'thinking' | 'working' | 'responding'

interface Props {
  statusText: string
  activity: string | null
}

export function StreamingIndicator({ statusText, activity }: Props) {
  const reduce = useReducedMotion()
  const phase = derivePhase(statusText)
  const [sentence, setSentence] = useState<string>(() => activity || CURATED_PHRASES[0])
  const curatedIdx = useRef(0)
  const lastActivity = useRef<string | null>(null)

  useEffect(() => {
    if (activity && activity !== lastActivity.current) {
      lastActivity.current = activity
      setSentence(activity)
      return
    }
    const id = setInterval(() => {
      if (lastActivity.current && Date.now() - (activityTs.current ?? 0) < ROTATION_MS * 1.5) return
      curatedIdx.current = (curatedIdx.current + 1) % CURATED_PHRASES.length
      setSentence(CURATED_PHRASES[curatedIdx.current])
      lastActivity.current = null
    }, ROTATION_MS)
    return () => clearInterval(id)
  }, [activity])

  const activityTs = useRef<number>(Date.now())
  useEffect(() => { if (activity) activityTs.current = Date.now() }, [activity])

  const label = useMemo(() => {
    if (phase === 'responding') return 'Responding'
    if (phase === 'working') return 'Working'
    return 'Thinking'
  }, [phase])

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4, transition: spring.effects }}
      transition={bounce.lift}
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
        {/* State label with coral text shimmer */}
        <span
          data-type="label-m"
          className="text-shimmer-primary"
          aria-live="polite"
        >
          {label}
        </span>

        {/* Flipping sentence line */}
        <div className="relative h-5 overflow-hidden">
          <AnimatePresence mode="popLayout">
            <motion.p
              key={sentence}
              data-type="body-m"
              className="absolute inset-x-0 text-on-surface-low"
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

function derivePhase(statusText: string): Phase {
  const s = statusText.toLowerCase()
  if (!s || s.includes('think')) return 'thinking'
  if (s.includes('respond') || s.includes('writing') || s.includes('streaming')) return 'responding'
  return 'working'
}
