/** A flat linear meter for a MEASURED quantity — memory in use, disk consumed, quota
 *  spent. Distinct from WavyProgress on purpose: that one is for a TASK in flight
 *  (indeterminate by default, animated crest); this one reports a level that is simply
 *  true right now and is not going anywhere on its own.
 *
 *  `label` is required because a bare bar announces "progressbar, 63%" with no subject,
 *  which in a list of several meters says nothing. The tone is the caller's call — a
 *  threshold that matters (a configured warning percentage) belongs to the caller, not to
 *  a hardcoded number in here.
 */
export function Meter({
  label,
  pct,
  detail,
  tone = 'var(--color-primary)',
}: {
  label: string
  pct: number
  detail?: string
  tone?: string
}) {
  const clamped = Math.min(100, Math.max(0, pct))
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <div
        className="h-1.5 w-full overflow-hidden rounded-pill bg-surface-high"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(clamped)}
      >
        <div
          className="h-full rounded-pill transition-[width]"
          style={{ width: `${clamped}%`, background: tone }}
        />
      </div>
      {detail && <div className="text-on-surface-low text-[0.75rem] tabular-nums">{detail}</div>}
    </div>
  )
}
