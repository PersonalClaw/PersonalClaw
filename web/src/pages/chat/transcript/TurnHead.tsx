import { useEffect, useRef, useState } from 'react'
import { AgentOrb } from '../../../ui/AgentOrb'
import { cx } from '../../../ui/cx'
import { prefersReducedMotion } from '../../../design/motion'
import { phaseLabel, type WorkPhase } from '../../../lib/workPhase'

// ── The head of the running turn's reply: the agent orb and one static line ─────────────────────
//
// One per turn, for the whole turn: before the reply's first word, while it streams, while a step
// runs and while it waits on her. The orb takes the turn's phase and the line is the gateway's own
// (`work_phase`'s `line`), or the phase's word when it says none, so the words always name the exact
// phase. It shows after `SHOW_DELAY_MS` and holds at least `MIN_HOLD_MS`, so a fast reply never
// flashes it, and when the turn ends it settles to the ending's still mark and fades within
// `FADE_MS` (`docs/design/agent-orb.md`).

export const SHOW_DELAY_MS = 150
export const MIN_HOLD_MS = 300
export const FADE_MS = 300

/** How the turn ended, from its last `work_phase` frame: `done` or `error`, and its ending line. */
export interface TurnEnding { phase: WorkPhase; line: string }

type Stage = 'hidden' | 'shown' | 'leaving'
const WAITING_TO_START: TurnEnding = { phase: 'queued', line: '' }

export function TurnHead({ running, phase, line, ending, onShown, className }: {
  /** Whether this page holds a running turn (the stream's claim). */
  running: boolean
  /** The running turn's phase, `null` until the gateway has said one or once its last frame came. */
  phase: WorkPhase | null
  /** The gateway's line for the phase. */
  line: string
  /** How the turn ended, once its last frame said so. */
  ending: TurnEnding | null
  /** Called when the head appears, so a transcript that follows its end can bring it into view. */
  onShown?: () => void
  className?: string
}) {
  const [stage, setStage] = useState<Stage>('hidden')
  const shownAt = useRef(0)
  // What it shows: the running phase, else the ending, else (sent, not described yet) waiting to
  // start. A turn that ended without its last frame keeps what it showed last while it goes.
  const now = running ? (phase ? { phase, line } : ending ?? WAITING_TO_START) : ending
  const last = useRef<TurnEnding>(WAITING_TO_START)
  useEffect(() => { if (now) last.current = now })
  const shows = now ?? last.current

  useEffect(() => {
    if (running) {
      if (stage === 'leaving') { setStage('shown'); return }
      if (stage === 'shown') return
      const t = window.setTimeout(() => { shownAt.current = Date.now(); setStage('shown') }, SHOW_DELAY_MS)
      return () => window.clearTimeout(t)
    }
    if (stage === 'shown') {
      const t = window.setTimeout(() => setStage('leaving'), Math.max(0, shownAt.current + MIN_HOLD_MS - Date.now()))
      return () => window.clearTimeout(t)
    }
    if (stage === 'leaving') {
      const t = window.setTimeout(() => setStage('hidden'), prefersReducedMotion() ? 0 : FADE_MS)
      return () => window.clearTimeout(t)
    }
  }, [running, stage])

  const shown = stage !== 'hidden'
  useEffect(() => { if (shown) onShown?.() }, [shown]) // eslint-disable-line react-hooks/exhaustive-deps -- once per appearance, whichever callback is current

  if (!shown) return null
  // The line is the turn's polite live region while the turn runs. Its ending is said once, by the
  // transcript's turn narration, so the ending line is shown and not said again.
  const saying = running && (phase !== null || ending === null)
  return (
    <div data-turn-head data-turn-head-stage={stage}
      className={cx('flex min-w-0 items-center gap-s transition-opacity duration-300 ease-out motion-reduce:transition-none', className)}
      style={{ opacity: stage === 'leaving' ? 0 : 1 }}>
      <AgentOrb phase={shows.phase} />
      <p data-turn-line data-type="body-m" aria-live={saying ? 'polite' : 'off'} className="min-w-0 truncate text-on-surface-var">
        {shows.line.trim() || phaseLabel(shows.phase)}
      </p>
    </div>
  )
}
