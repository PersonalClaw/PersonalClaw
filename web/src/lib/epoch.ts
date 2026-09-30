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

/** The value a `<input type="datetime-local">` shows for a stamp — `2026-10-05T09:50`, in the
 *  BROWSER's zone, to the minute — or `''` when the stamp is unreadable (an empty picker).
 *
 *  The inverse of reading such a value back with `epochSeconds`, which parses a zone-less date-time
 *  as local: a form seeded from this and saved untouched lands on the same minute. */
export function localDateTimeInput(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  const at = new Date(secs * 1000)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())}T${two(at.getHours())}:${two(at.getMinutes())}`
}

/** ── ABSOLUTE stamps, for showing WHEN something happened rather than how long ago ────────────
 *
 *  These sit here rather than beside a caller because they share `epochSeconds`' parser AND its
 *  failure contract: an unreadable stamp renders **nothing**, never a fallback like "Invalid Date"
 *  or the epoch. A blank reads as "no timestamp" (which is true); anything else claims a time the
 *  app does not have.
 *
 *  Locale-resolved on purpose — no hardcoded 24-hour or AM/PM format. The reader's own locale
 *  decides, which is the only choice that is right in every region without a setting to argue over.
 */

/** Clock time for display beside a message: `14:32`, or `2:32 PM`, per the reader's locale. */
export function clockTime(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  return new Date(secs * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** When something short-lived stops working — a pairing code's ten minutes — as the reader's clock
 *  time, with the day in front when that is not today: `14:52`, or `Sep 30, 14:52`, per the reader's
 *  locale. A date alone ("until Sep 29, 2026") said nothing about a code that lasts minutes.
 *
 *  Unreadable ⇒ `''`, as its siblings. `now` is epoch MILLISECONDS, for a test's fixed clock. */
export function expiryStamp(ts?: number | string | null, now: number = Date.now()): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  const at = new Date(secs * 1000)
  if (at.toDateString() === new Date(now).toDateString()) {
    return at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  }
  return at.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** `sentence`, then until when the thing it is about works (`expiryStamp`), ended as a
 *  sentence: "It works once, until 05:45 a.m." — with one full stop. The time is the last word, and
 *  a locale that writes `05:45 a.m.` ended it "a.m.." when a stop was added after it regardless.
 *  An unreadable time leaves the clause out. */
export function untilSentence(sentence: string, ts?: number | string | null, join = ', until '): string {
  const at = expiryStamp(ts)
  const text = at ? `${sentence}${join}${at}` : sentence
  return /[.!?]$/.test(text) ? text : `${text}.`
}

/** The same instant fully spelled out, for the `title` of a clock time.
 *
 *  `14:32` alone is ambiguous the moment a conversation is more than a day old, and the visible
 *  form has to stay short — so the date lives in the hover/AT text instead of costing width. */
export function fullStamp(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  return new Date(secs * 1000).toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' })
}

/** The DAY only — `7 Sep 2026` — for a stamp whose time-of-day carries no meaning.
 *
 *  A registry listing's `last_validated` is the case this exists for: it says which day an
 *  index last checked a listing, and rendering `12:19` beside it would imply a precision the fact
 *  does not have while costing width on a card that has none to give. The full instant still
 *  belongs in the `title` — that is `fullStamp`.
 *
 *  Same failure contract as its siblings: unreadable ⇒ `''`, never `Invalid Date`. */
export function dayStamp(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  return new Date(secs * 1000).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** The machine-readable form for a `<time dateTime=…>` attribute. Empty when unreadable, so the
 *  caller can omit the attribute rather than emit `dateTime=""`, which would be a lie in markup. */
export function isoStamp(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  return new Date(secs * 1000).toISOString()
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
