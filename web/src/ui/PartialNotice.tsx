/** ── A view built from PART of the data says so ────────────────────────────────────────────
 *
 *  The fourth load-state, beside the three the kit already names:
 *
 *      loading    nothing to show yet                        → a skeleton
 *      stale      something IS shown, and it is old          → `StaleNotice`
 *      error      the read failed                            → `LoadError`, an alert
 *      partial    the read SUCCEEDED and returned some of it  → THIS
 *
 *  🔑 THE DEFECT IS SILENCE, NOT THE BOUND. `/api/tasks` served the server's default 50 rows
 *  because no caller asked for more, and every surface then derived structure from that window
 *  as though it were the whole set: the dependency graph drops edges to ids it does not hold, so
 *  a task whose prerequisite was row 51 drew as a clean UNBLOCKED node; the prerequisite picker
 *  could only offer the first 50 candidates. Nothing on screen said the view was a fragment, so
 *  the wrong answer looked exactly like a right one (#485).
 *
 *  🔑 NOT AN ALERT, and NOT `MoreRow`. `LoadError` interrupts because a failed read changes what
 *  the screen means; this is a true statement about a working screen, so it is a polite
 *  `role="status"`. `MoreRow` is the neighbouring but different case — a list that HAS all its
 *  rows and chose to render a slice, where the only casualty is the rows themselves. Here the
 *  missing rows are missing from the client entirely, so anything DERIVED from the set is
 *  affected too, which is what `detail` exists to say.
 *
 *  Self-gating on `complete`, so a call site passes what its loader returned and cannot forget
 *  the `&&` — a disclosure that renders unconditionally is worse than none.
 */
export function PartialNotice({ complete, shown, total, what, detail, className }: {
  /** Straight from the loader (e.g. `api.allTasks`). Renders nothing when true. */
  complete: boolean
  /** How many rows the caller actually holds. */
  shown: number
  /** How many exist — the number the bounded read could not reach. */
  total: number
  /** The rows, as a lowercase plural noun. The SAME noun this surface's `LoadError` and
   *  skeleton already use; copied, never invented. */
  what: string
  /** What is wrong BEYOND the missing rows, in this surface's terms — the graph not drawing an
   *  edge, the picker not offering a candidate. Optional because a surface that only lists rows
   *  has nothing further to disclose, and inventing a consequence would be worse than omitting
   *  one. */
  detail?: string
  className?: string
}) {
  if (complete) return null
  return (
    <div
      // The machine-readable half, like `StaleNotice`'s `[data-stale]`: a test asks for
      // `[data-partial="true"]` rather than matching copy, so "the view was labelled" survives
      // a wording change.
      data-partial="true"
      role="status"
      data-type="body-s"
      className={`text-on-surface-low ${className ?? ''}`}
    >
      Showing {shown.toLocaleString()} of {total.toLocaleString()} {what}
      {detail ? ` — ${detail}` : ''}
    </div>
  )
}
