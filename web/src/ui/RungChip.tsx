import { type AutonomyLadder, type AutonomyType } from '../lib/api'
import { rungMeta, rungReason } from '../lib/rungs'

/** How much an automation may do on its own, as a chip (AUTONOMY-GUARDRAILS §6.1).
 *
 *  Shown wherever a user looks at an automation — a trigger row, the ladder panel — and it
 *  answers ONE question: why is this allowed to run by itself? The visible label is the
 *  rung in behaviour words ("runs on its own", "asks first"), and the tooltip carries the
 *  provenance sentence the server composed: a declared floor, a promotion you clicked (with
 *  the record you were shown), or a granted rung the incident kill switch is currently
 *  holding down.
 *
 *  A held rung is drawn at the rung it ACTUALLY resolves to, not the granted one, with the
 *  incident said in the tooltip. Rendering the granted rung would tell a user their
 *  automation runs unattended at the exact moment the kill switch has stopped it. */
export function RungChip({ type, ladder = null }: { type: AutonomyType; ladder?: AutonomyLadder | null }) {
  const meta = rungMeta(type.resolved_rung, ladder)
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-pill px-2 py-0.5 text-[0.6875rem]"
      style={{ background: `color-mix(in srgb, ${meta.tone} 14%, transparent)`, color: meta.tone }}
      title={rungReason(type, ladder)}>
      <meta.icon size={11} className="shrink-0" />
      {meta.label}
      {type.held_by_incident && ' · held'}
    </span>
  )
}
