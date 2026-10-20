/** How long something took, as the app shows it: `under 1s`, `42s`, `3m 5s`, `1h 4m`.
 *
 *  The one duration format the app's surfaces share: the workflow list's and run page's chips, the
 *  chat's workflow card, the introspection timeline, the buddy's "done in" line, and through
 *  `elapsedStat` (`pages/workflows/workflowMeta`) every labelled cell that shows a step's time.
 *
 *  The empty string is the contract, not an oversight: nothing measured (no figure, zero or less)
 *  shows nothing, and every caller guards on it to decide whether the figure appears at all. A run
 *  that has not started has no elapsed time to show, and "0s" beside its name would read as a
 *  finished instant run.
 *
 *  🪤 It is therefore the WRONG formatter for a `<Stat>` cell or a rail cell, which render their
 *  label unconditionally — use `elapsedStat`.
 *
 *  The figure is rounded to whole seconds once, before minutes and hours are split off, so the
 *  seconds place never reads 60 (119.6 s is `2m 0s`, 3599.6 s is `1h 0m`). A measured time that
 *  rounds to nothing reads `under 1s`, never `0s`: something happened, just quickly. */
export function fmtElapsed(secs: number | undefined): string {
  if (!secs || secs <= 0) return ''
  const whole = Math.round(secs)
  if (whole < 1) return 'under 1s'
  if (whole < 60) return `${whole}s`
  const m = Math.floor(whole / 60)
  if (m < 60) return `${m}m ${whole % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}
