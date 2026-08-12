/** Coerce whatever a timestamp field actually holds into epoch SECONDS, or `undefined`.
 *
 *  Every relative-time formatter in this app was typed `ts?: number | null` and did arithmetic
 *  on it directly. That is fine until an endpoint sends an ISO string — then `Date.now()/1000 - ts`
 *  is `NaN`, every `if (s < …)` comparison is false, and the formatter falls out of its last
 *  branch rendering the unit with NaN in front of it. Measured on `#/dashboard`: six rows reading
 *  **"in NaNd"**, because `/api/triggers/history` returns `started_at` /`finished_at` as
 *  `"2026-08-12T08:00:00.006315+00:00"` while `lib/api.ts` declared them `number`.
 *
 *  So the parsing lives in one place, and it has an honest failure value. A formatter that cannot
 *  read its input must say nothing — `NaN` on screen is worse than a blank, because a blank reads
 *  as "no data" (which is true) and `NaNd` reads as a broken product.
 *
 *  Seconds, not milliseconds: that is what the app's `next_run_ts` / `last_run_ts` /
 *  `started_at` numeric fields already are, so a number passes through untouched.
 */
export function epochSeconds(ts?: number | string | null): number | undefined {
  if (ts == null || ts === '') return undefined
  if (typeof ts === 'number') return Number.isFinite(ts) ? ts : undefined
  const ms = Date.parse(ts)
  return Number.isFinite(ms) ? ms / 1000 : undefined
}
