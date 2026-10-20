import { cx } from './cx'
import { isReading } from '../lib/readings'

/** A flat linear meter for a MEASURED quantity — memory in use, disk consumed, quota
 *  spent, an upload's bytes, a run's stages done. Distinct from WavyProgress on purpose:
 *  that one is for a TASK whose remaining time is unknown (indeterminate by default,
 *  animated crest); this one reports a level that is a plain fraction of a known total.
 *
 *  `label` is required because a bare bar announces "progressbar, 63%" with no subject,
 *  which in a list of several meters says nothing. The tone is the caller's call — a
 *  threshold that matters (a configured warning percentage) belongs to the caller, not to
 *  a hardcoded number in here.
 *
 *  `size` and `className` exist because eleven page-level bars had re-typed this track
 *  (`h-1 … overflow-hidden rounded-pill bg-surface-high` + an `h-full` fill) with no role
 *  and no `aria-valuenow`, so axe's `aria-progressbar-name` had nothing to fire on. Every
 *  one of them differed from this primitive in exactly two ways: a 4px track instead of
 *  6px, and a layout class on the outer box (`flex-1`, `w-32`). Those two axes are the
 *  whole gap, so they are the whole addition — `className` lands on the outer box, never
 *  on the track, so a caller cannot restyle the bar itself out of consistency.
 *
 *  `pct={null}` is a reading that was not taken (a host probe that timed out): the track stays
 *  empty and the bar reports no level, with "not measured" as its value text. A 0% fill there
 *  would be read out as a measurement nobody made.
 */
export function Meter({
  label,
  pct,
  detail,
  tone = 'var(--color-primary)',
  size = 'default',
  className,
}: {
  label: string
  pct: number | null
  detail?: string
  tone?: string
  size?: 'thin' | 'default'
  className?: string
}) {
  const clamped = isReading(pct) ? Math.min(100, Math.max(0, pct)) : null
  return (
    <div className={cx('flex min-w-0 flex-col gap-xs', className)}>
      <div
        className={cx(
          'w-full overflow-hidden rounded-pill bg-surface-high',
          size === 'thin' ? 'h-1' : 'h-1.5',
        )}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped === null ? undefined : Math.round(clamped)}
        aria-valuetext={clamped === null ? 'not measured' : undefined}
      >
        {clamped !== null && (
          <div
            className="h-full rounded-pill transition-[width]"
            style={{ width: `${clamped}%`, background: tone }}
          />
        )}
      </div>
      {detail && <div data-type="caption" className="text-on-surface-low tabular-nums">{detail}</div>}
    </div>
  )
}
