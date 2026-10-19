import type { ReactNode } from 'react'
import type { PhaseFamily } from '../lib/workPhase'
import { cx } from './cx'

/** StateMark — a state shown the one way every surface shows it: a mark in its family's colour and
 *  the words that name it.
 *
 *  The four families (quiet, active, waiting, blocked, `lib/workPhase.ts`) carry urgency, and the words
 *  carry the exact state, so the mark is decoration (`aria-hidden`) and the label is required. The
 *  colour is the family's shared state token (`--color-state-<family>`, `design/tokens.css` layer 2),
 *  the same set the agent orb and the buddy read, so a scheme recolours all of them together.
 *
 *  In forced colours the system replaces the mark's fill, so it is drawn with a border too: the ring
 *  stays in the system's text colour and the label still says what it is. */
export function StateMark({ family, children, id, className }: {
  /** The state's family; pick it from the phase contract (`phaseFamily(phase)`), never by colour. */
  family: PhaseFamily
  /** The words that name the state (`phaseLabel(phase)`, or the work's own line). Never empty: the
   *  mark alone means nothing. */
  children: ReactNode
  id?: string
  className?: string
}) {
  const tone = `var(--color-state-${family})`
  return (
    <span id={id} data-state-family={family} className={cx('inline-flex items-center gap-xs text-on-surface', className)}>
      <span aria-hidden="true" className="size-2 shrink-0 rounded-pill border" style={{ background: tone, borderColor: tone }} />
      <span>{children}</span>
    </span>
  )
}
