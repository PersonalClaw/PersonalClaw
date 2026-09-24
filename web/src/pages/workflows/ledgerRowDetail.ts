/** What one ledger row SAYS, projected for the inspector's ledger list (SELF-VERIFICATION §SC6).
 *
 *  The runs surface used to render a ledger row as its `kind` and nothing else. For most engine
 *  rows that is nearly enough — `step_completed` on a node whose output is shown right above it
 *  carries little the rest of the drawer does not. For a row a PRODUCER wrote to answer a question,
 *  it is a total loss: the Self-QA companion records one `step_skipped` per test-only commit
 *  carrying the `sha`, the `impact` class and a one-line `rationale`, and a list of bare kinds
 *  renders three of those as three identical words. The row was written, the reader could read it,
 *  and the surface said nothing — so "a skip record with a one-line rationale, visible in the runs
 *  surface" was durably unmet by a renderer, not by a missing write.
 *
 *  Deliberately GENERIC rather than Self-QA-shaped. `sha`, `impact` and `rationale` are read off
 *  whatever row carries them, so any producer that already stamps them (and any future one) is
 *  legible here without another branch in this file. A row with none of them projects to empty
 *  strings and renders exactly as it did before.
 *
 *  Nothing here truncates. The rationale is a ONE-LINE contract on the writing side — the ledger
 *  refuses an empty one and rejects an embedded newline — so the only way to make it useless on
 *  this side is to clip it, and a reason cut to "assertion maintenance only — 3 test…" answers
 *  "why did nothing run?" no better than silence. The `sha` is likewise passed through whole: a
 *  forensics drawer that shows an abbreviation a user cannot paste back into `git show` is a
 *  surface they stop trusting.
 */

/** One row's legible detail. Every field is a string; `''` means "this row does not carry it",
 *  which the renderer treats as "omit the element" rather than "render an empty one". */
export interface LedgerRowDetail {
  kind: string
  sha: string
  impact: string
  rationale: string
}

/** Read a string field off a raw ledger row. Non-strings project to `''` rather than being
 *  stringified: a row whose `rationale` arrived as an object is a producer bug, and rendering
 *  `[object Object]` as the reason a commit was skipped would hide it behind something that
 *  looks like content. */
function str(row: Record<string, unknown>, key: string): string {
  const v = row[key]
  return typeof v === 'string' ? v.trim() : ''
}

export function ledgerRowDetail(row: Record<string, unknown>): LedgerRowDetail {
  return {
    kind: str(row, 'kind') || 'event',
    sha: str(row, 'sha'),
    impact: str(row, 'impact'),
    rationale: str(row, 'rationale'),
  }
}

/** A stable React key for a ledger row.
 *
 *  The ledger's own `event_id` when it has one (`<run>-evt-<seq>`, minted per append, so it is
 *  unique across a run by construction), otherwise the index. This matters more than a key
 *  usually does: SEVERAL rows can share one node id and one instance path — the companion writes
 *  one per commit — and keying such a list on anything derived from its CONTENT would make two
 *  commits with the same impact class collide and drop a row from the DOM. That is precisely the
 *  loss this surface exists to prevent, so the key is identity-based or positional, never content.
 */
export function ledgerRowKey(row: Record<string, unknown>, index: number): string {
  const id = str(row, 'event_id')
  return id || `row-${index}`
}
