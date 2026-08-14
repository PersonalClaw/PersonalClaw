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

/** A chat session's recency in MILLISECONDS for sorting — `last_activity_ts`, else `last_ts`, else
 *  `created`, else 0.
 *
 *  🔴 THE FALLBACK CHAIN HAS TO USE `||`, NOT `??`, AND THE DIFFERENCE IS LIVE. `/api/chat/sessions`
 *  returns `last_ts` as an EMPTY STRING — measured on 31 of 32 sessions in a real dev home — so `??`
 *  (which only guards null/undefined) passes `''` through, `new Date('')` is an Invalid Date, and
 *  `.getTime()` is **NaN**. A comparator that returns NaN makes the sort order implementation-defined:
 *  the "recent chats" list would shuffle rather than fail, which is the kind of bug nobody files.
 *
 *  `#/chat` already had this right in two places (`Date.parse(a || b || c || '') || 0`) while
 *  `#/dashboard` used `new Date(a ?? b ?? 0).getTime()`. This is that shape, once, routed through
 *  `epochSeconds` so the empty string and the unparseable string are handled by the parser that
 *  already knows about both — and so a fourth copy has somewhere to converge instead of diverging.
 *
 *  Milliseconds, because that is what both existing call sites already produced; only the ORDER matters
 *  to every consumer, but keeping the unit means adopting this changes no behaviour at all. */
export function sessionRecencyMs(s: SessionStamps): number {
  const secs = sessionActivitySeconds(s)
  return secs == null ? 0 : secs * 1000
}

type SessionStamps = { last_activity_ts?: string; last_ts?: string; created?: string }

/** WHICH field is a session's activity time, in epoch SECONDS, or `undefined` when none reads.
 *
 *  The chain above answered that for SORTING; `#/chat`'s history list answered it again for
 *  DISPLAY (`relTimeShort(s.last_activity_ts || s.last_ts || s.created)`) with its own
 *  `Date.parse`. One question, two answers, and they can drift apart in either direction — a
 *  sort that ranks by `last_activity_ts` beside a label that fell back to `created` would show
 *  a list ordered by a number the user cannot see.
 *
 *  So the field choice lives here once and both callers read it. `undefined` rather than `0`
 *  is the failure value, because a formatter needs to tell "no timestamp" (render nothing)
 *  apart from "the epoch" — `sessionRecencyMs` still collapses it to 0, which is what a
 *  comparator wants.
 */
export function sessionActivitySeconds(s: SessionStamps): number | undefined {
  // `||`, not `??`: `/api/chat/sessions` sends `last_ts` as an EMPTY STRING on 31 of 32
  // sessions, and `??` would pass that through to be parsed. Documented on the sorter above.
  return epochSeconds(s.last_activity_ts) ?? epochSeconds(s.last_ts) ?? epochSeconds(s.created)
}
