import { type AutonomyLadder, type AutonomyType } from '../lib/api'
import { rungMeta, rungReason } from '../lib/rungs'
import { accentChip } from '../design/accent'

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
  // The accent-chip failure cycle 146 measured, reached through a REGISTRY tone instead of a literal
  // token. `--color-primary` as ink over a 14% tint of ITSELF is **3.97:1** in light against a 4.5
  // floor — measured live on `#/triggers`, 7 chips desktop / 6 phone, and it is the 14% row of that
  // cycle's own table. Only the `autonomous` rung is coral (`lib/rungs.ts`); the other three tones
  // measure 4.99-7.46 in light and keep the tint, which is why this is a one-tone remap and not a
  // sweep. `accentChip` is the pair the system already ships for an accent-tinted surface
  // (`primary-container` + `on-primary-container`, 13.1:1 light / 10.43:1 dark, guaranteed for all 12
  // schemes by `schemeContrast`) — and being a solid fill it is also ground-independent, where the
  // tint's contrast depended on whatever surface the chip happened to land on.
  const skin = meta.tone === 'var(--color-primary)'
    ? accentChip
    : { background: `color-mix(in srgb, ${meta.tone} 14%, transparent)`, color: meta.tone }
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-pill px-2 py-0.5 text-[0.6875rem]"
      style={skin}
      title={rungReason(type, ladder)}>
      <meta.icon size={11} className="shrink-0" />
      {meta.label}
      {type.held_by_incident && ' · held'}
    </span>
  )
}
