/** The runs-inbox archive split (WF2AUT-10 · §1.3).
 *
 *  §1.3: "inert outcomes collapse to ledger rows and archive OUT of the default inbox view — the
 *  runs inbox is for what the machine DID." The backend has returned `did_ids`/`suppressed` since
 *  S132 and the wrapper typed them since S163; S165 got them as far as ORDERING the widget
 *  (work-first) — but a suppression storm still filled the six visible slots, because ordering
 *  only helps until the real fires run out. This is the fold the section actually asked for: the
 *  default view shows work, the suppressed rows archive behind a disclosure, and §7 criterion 8's
 *  "zero silent drops" holds because they are one click away, not gone.
 *
 *  Kept as a pure module rather than inline in the widget so the partition is unit-testable and
 *  there is ONE copy: the widget's own comments record that a second copy of `is_inert`'s rule
 *  drifts the moment a new `skipped_*` outcome lands, and the earlier inline `order()` in the test
 *  was exactly that second copy. Membership comes from the SERVER's `did_ids`, never from
 *  re-testing the outcome here. */

/** The minimum a run row needs for the split: an identity. Everything else the widget reads
 *  (outcome, timestamps, names) is irrelevant to WHICH bucket a row lands in. */
export interface FoldableRun {
  id?: string
  run_id?: string
}

export interface RunFold<T extends FoldableRun> {
  /** Fires that DID something — the default inbox view, work-first order preserved. */
  did: T[]
  /** Suppressed/inert fires — archived behind the disclosure, never dropped. */
  suppressed: T[]
}

/** Partition a run feed into work vs. suppressions using the server's `did_ids`.
 *
 *  A row with no `id` is treated as work, not a suppression: a legacy `ScheduleRun` carries no
 *  `id` (a projected `FireRecord` does), and the fail direction has to be "shown" — an unknown row
 *  is more likely real work than a gate hit, and hiding a real fire is the failure the split
 *  exists to prevent. */
export function partitionRuns<T extends FoldableRun>(runs: T[], didIds: string[]): RunFold<T> {
  const did = new Set(didIds)
  const isDid = (r: T) => !r.id || did.has(r.id)
  return { did: runs.filter(isDid), suppressed: runs.filter((r) => !isDid(r)) }
}
