import { RefreshCw } from 'lucide-react'

/** ── A cached paint that is not current SAYS SO ────────────────────────────────────────────
 *
 *  The third state a surface needs and never had. `useQuery` distinguishes:
 *
 *      loading       nothing to show yet                  → a skeleton
 *      stale         something IS shown, and it is old     → THIS
 *      error         the read failed                       → `LoadError`, which is an alert
 *
 *  Without the middle one, a cached first paint and a fresh one are indistinguishable on
 *  screen, so the app shows an old value with total confidence and then swaps it for a
 *  different one. Measured on the pre-fix build (`#/settings`, Inbox tile, retention changed
 *  from 30 to 7 out of band, hard reload with the revalidation held): FIRST PAINT read "30 day
 *  retention" with `[data-stale]` = 0, `[aria-busy]` = 0 and no "updating" copy anywhere on the
 *  page — then it became 7. Both numbers were once true; the screen never said which one it was
 *  showing. That is what reads as a bug even when the data is right.
 *
 *  🔑 NOT AN ALERT. `LoadError` interrupts because a failed read changes what the screen MEANS.
 *  "This is a moment old and I am re-reading it" is not bad news, so this is a polite
 *  `role="status"` — the same treatment the bare `Loading…` state gets, for the same reason.
 *
 *  🔑 NAMES ITS NOUN, like every other load-state in the kit. `LoadError what="triggers"` says
 *  "Couldn't load your triggers"; the skeleton beside it says "Loading triggers…"; this says
 *  "Updating triggers…". One vocabulary per surface, spoken the same way in all three states.
 */
export function StaleNotice({ stale, what, className, announce = true }: {
  /** Straight from `useQuery`. Self-gating so the call site is one line and cannot forget the
   *  `&&` — a stale label that renders unconditionally is worse than none. */
  stale: boolean
  /** The data being re-read, as a lowercase plural noun — the SAME noun the surface's
   *  `LoadError`/skeleton already declares. Copied, never invented. */
  what: string
  className?: string
  /** Whether to speak. Default true — one label on a page is worth announcing.
   *
   *  Set FALSE where many of these can be on screen at once. `#/settings` is the measured case:
   *  22 bento tiles read their config through the same layer and revalidate together on a cold
   *  open, so 22 polite live regions would queue 22 announcements for one page load. That is the
   *  same finding `BentoCard` already recorded for its own `aria-busy` (a property, not a live
   *  region), and this follows it rather than re-deciding it. The visible label and the
   *  `[data-stale]` hook are unaffected. */
  announce?: boolean
}) {
  if (!stale) return null
  return (
    <span
      // The machine-readable half. A test — and the browser probe that measured the defect —
      // asks for `[data-stale="true"]` rather than matching copy, so the claim "the paint was
      // labelled" survives a wording change. Present whether or not this instance announces.
      data-stale="true"
      role={announce ? 'status' : undefined}
      className={`inline-flex items-center gap-1 text-on-surface-low text-[0.75rem] ${className ?? ''}`}
    >
      <RefreshCw size={11} className="animate-pulse" aria-hidden />
      Updating {what}…
    </span>
  )
}
