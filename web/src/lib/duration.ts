/** How long something took, as the app shows it: `42s`, `3m 5s`, `1h 4m`.
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
 *  label unconditionally — use `elapsedStat`. */
export function fmtElapsed(secs: number | undefined): string {
  if (!secs || secs <= 0) return ''
  if (secs < 60) return `${Math.round(secs)}s`
  const m = Math.floor(secs / 60)
  if (m < 60) return `${m}m ${Math.round(secs % 60)}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}
