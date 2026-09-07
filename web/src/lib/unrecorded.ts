/** The ONE vocabulary for "this fact was never recorded", on the page side.
 *
 *  The Python half is `src/personalclaw/evals/provenance.py`; this is its twin, and
 *  `tests/test_evals_unrecorded_vocabulary.py` reds if the two spell the word differently. Not
 *  generated from it: a code generator for one string and one comparison would be more moving
 *  parts than the rail it replaces.
 *
 *  **The word is `unrecorded`.** Not `priced` — that word already exists in `loop_spend`,
 *  `usage_ledger` and `run_totals` and means precisely "a cost is unknown". #2630 ruled it must
 *  not be widened to cover token counts, because a row with a known cost and an unknown token
 *  count would have to pick one value and either choice lies to one of its two readers.
 *
 *  **Three states**, and collapsing any two re-creates the defect this module exists for:
 *
 *  1. RECORDED — the fact is known and has a value;
 *  2. RECORDED-NONE — the fact is known and its value is nothing (`null`, `{}`, a genuine `0`).
 *     This is a MEASUREMENT;
 *  3. UNRECORDED — the fact was never recorded. Not a weaker measurement: not one.
 */

/** The one word, for anywhere a state name is rendered or compared. */
export const UNRECORDED = 'unrecorded'

/** What a reader sees where an unrecorded value would otherwise be printed. Deliberately NOT the
 *  panels' house string "not measured": that one already means "we asked and got nothing back",
 *  and this one means "we never recorded whether we asked". Two claims, two strings. */
export const UNRECORDED_LABEL = 'not recorded'

/** The `report_schema` at and above which a learning-benchmark report RECORDS its provenance.
 *  Mirrors `learning_bench.PROVENANCE_SCHEMA`.
 *
 *  Below it, an absent `provider_binding` means UNRECORDED and NOT "nothing was bound" — ES-17
 *  added the field and left `REPORT_SCHEMA` at 1 (#2562), so consumers had to branch on
 *  `'provider_binding' in report`. That trick worked and left every future consumer to rediscover
 *  it, which is why the answer lives here and is read from the schema the report STATES. */
export const PROVENANCE_SCHEMA = 2

/** The schema a report was written under, as the report itself states it.
 *
 *  `null` when it states none — UNRECORDED. It deliberately does NOT default to 1: a report that
 *  cannot say what it recorded must not be treated as one that said it recorded nothing, which is
 *  the same absent-versus-declared collapse one level up. */
export function reportSchema(report: { report_schema?: number } | null | undefined): number | null {
  const raw = report?.report_schema
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null
}

/** Did this report record what its cells could reach?
 *
 *  `true` only at `PROVENANCE_SCHEMA` or above. A report stating no schema is `false`: an
 *  unreadable schema cannot certify that a field is present. */
export function provenanceRecorded(
  report: { report_schema?: number } | null | undefined,
): boolean {
  const schema = reportSchema(report)
  return schema !== null && schema >= PROVENANCE_SCHEMA
}

/** Is a nullable-scalar fact unrecorded, given the boolean that carries its recording state?
 *
 *  Both arguments, deliberately. A `null` scalar alone cannot say WHY it is null — the arms were
 *  never assembled, or the provider never reported the number — and an ABSENT recording flag is
 *  itself unrecorded (an older report never carried one). */
export function tokensUnrecorded(row: { tokens_recorded?: boolean }): boolean {
  return row.tokens_recorded === false
}
