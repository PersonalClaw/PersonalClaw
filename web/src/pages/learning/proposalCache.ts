import { invalidateKeys } from '../../lib/data'

// ── The Learning page's two cached reads, and what a decision does to them ────
//
// Keys live here rather than inline at the call sites because the proposal list is keyed per FACET
// (the active kind tab): `learning:proposals:` for All, `learning:proposals:skill`, and one more for
// every tab the user has visited. That split is the whole reason a decision's invalidation has to be
// a SWEEP rather than a single drop, and spelling the prefix out three times in the component is how
// one of the three ends up disagreeing with the others.

export const PROPOSALS_KEY_PREFIX = 'learning:proposals:'
export const WEEK_KEY = 'learning:week'
export const HEALTH_KEY = 'learning:health'
export const JUDGE_BENCH_KEY = 'learning:judge-bench'
export const STUDIES_KEY = 'learning:studies'
export const STUDY_DETAIL_KEY_PREFIX = 'learning:study:'

/** The cache key for ONE study's drill-down. Keyed per study for the same reason the
 *  proposal list is keyed per facet: a shared key would make expanding a second study paint
 *  the first one's runs out of cache, and the numbers would look real. */
export function studyDetailKey(studyId: string): string {
  return STUDY_DETAIL_KEY_PREFIX + studyId
}

/** The cache key for one facet of the proposal list. `kind` is `''` for the All tab. */
export function proposalsKey(kind: string): string {
  return PROPOSALS_KEY_PREFIX + kind
}

/** Re-read the proposal list after an accept or a dismiss.
 *
 *  Two things have to happen and only one of them used to (#676): dropping the cache entry arms the
 *  NEXT mount, it does not re-render the live one. So the decided row sat on screen until the user
 *  navigated away, while the server had already deleted it — and a second Dismiss on that ghost row
 *  escalates the rejection cooldown (`learning/proposals.py:298-302`), so the stale row is not
 *  merely cosmetic. `refresh` is the half that refetches what the user is actually looking at.
 *
 *  The drop is a PREFIX sweep, not a single key. A row dismissed from the Skill tab is also gone
 *  from All, so dropping only the active facet leaves every other tab holding a decided row and a
 *  stale chip count — painted the instant that tab is selected, because the hook seeds a key change
 *  straight from cache.
 *
 *  The capture week is deliberately NOT refetched here. Its numbers all come from the staging
 *  store's `flush_records` and `staging` tables (`StagingStore.week`), which record what a capture
 *  PASS did; `accept` and `reject` write neither — they touch the proposal file, the decision
 *  memory, and the inbox item. A refetch would be a request that provably cannot return anything
 *  new. */
export function refreshAfterDecision(refreshProposals: () => void): void {
  invalidateKeys(PROPOSALS_KEY_PREFIX, true)
  refreshProposals()
}

/** Re-read everything the page shows, for the explicit Refresh control.
 *
 *  Unlike a decision this carries no claim about what changed — the user is asking for current
 *  server state, and a capture pass may well have run since the page mounted. So the week IS worth
 *  re-reading here, which is exactly the difference from `refreshAfterDecision`. The health panel
 *  moves for the same reason and on the same evidence: every one of its inputs (allocation samples,
 *  flush costs, judge verdicts, attribution grades) is written by a background cadence, so it is
 *  precisely the section most likely to be stale on a page that has been open a while. */
export function refreshEverything(
  refreshProposals: () => void,
  refreshWeek: () => void,
  refreshHealth: () => void = () => {},
  refreshJudgeBench: () => void = () => {},
  refreshStudies: () => void = () => {},
): void {
  invalidateKeys(PROPOSALS_KEY_PREFIX, true)
  invalidateKeys(WEEK_KEY)
  invalidateKeys(HEALTH_KEY)
  // The judge table moves only when `personalclaw judge-bench` runs, which the user does from a
  // terminal — so it is the one read on this page whose staleness the PAGE cannot detect, and an
  // explicit Refresh is exactly when re-asking is worth a request.
  invalidateKeys(JUDGE_BENCH_KEY)
  // The study list AND every expanded drill-down. A study is run from a terminal too, so the
  // page cannot detect its staleness either — and dropping the list while keeping a detail
  // entry would paint a fresh verdict beside stale runs, which is the worst of the three
  // states because both halves look current.
  invalidateKeys(STUDIES_KEY)
  invalidateKeys(STUDY_DETAIL_KEY_PREFIX, true)
  refreshProposals()
  refreshWeek()
  refreshHealth()
  refreshJudgeBench()
  refreshStudies()
}
