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

/** How long a short run took, from milliseconds: `120 ms` under a second, seconds to one decimal
 *  under a minute (`1.2 s`, `12.0 s`), and from a minute on as `fmtElapsed` says it (`1m 5s`). For a
 *  hook's run, which mostly takes well under a second, so `fmtElapsed` would read every one
 *  `under 1s`.
 *
 *  The same contract as `fmtElapsed`: nothing measured (no figure, zero or less) is the empty
 *  string, and the caller shows nothing. Rounded once at each step, so 999.6 ms reads `1.0 s`, never
 *  `1000 ms`, and 59.96 s reads `1m 0s`. */
export function fmtRunTime(ms: number | undefined): string {
  if (!ms || ms <= 0) return ''
  if (Math.round(ms) < 1000) return `${Math.max(1, Math.round(ms))} ms`
  const tenths = Math.round(ms / 100)
  if (tenths < 600) return `${(tenths / 10).toFixed(1)} s`
  return fmtElapsed(ms / 1000)
}
