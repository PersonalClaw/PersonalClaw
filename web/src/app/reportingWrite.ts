import { notify } from './appSdk'

/** Run a write the user just triggered; report a failure and say whether it landed.
 *
 * For a **data-driven** control — one whose rendered value comes from a refetch rather than a local
 * flip. A failed write there does not leave a lying control, it leaves **NOTHING**: the switch does
 * not move, no message appears, and the only reasonable guess is to click again. That is a distinct
 * defect from the optimistic-lie shape, and it needs two things rather than one:
 *
 *   1. tell the user, with the server's own sentence;
 *   2. return the outcome, so the caller can SKIP the refetch — refetching after a failure
 *      re-renders the same state and reads as "nothing happened, twice".
 *
 * Extracted from `pages/tools/ToolsPage`, which established this contract, when `knowledge/
 * KnowledgeListPage` became its second adopter. One implementation rather than two copies of nine
 * lines — the drift a per-page copy becomes.
 *
 * 🪤 NO `JSON.parse` UNWRAP HERE, deliberately. `lib/errText` is the app's single funnel for failure
 * text ("Every API failure message in the app funnels through here") and `api.ts` throws
 * `ApiError(await errText(r))`, so `e.message` is ALREADY the backend's sentence — `{"error": …}`
 * and `{"detail": …}` are unwrapped there, HTML and long bodies reduced to a status. The
 * `try { JSON.parse(msg) }` idiom in seven settings panels re-parses a string that can no longer be
 * JSON; it is dead defensive code, and copying it here would make an eighth.
 */
export async function reportingWrite(what: string, run: () => Promise<unknown>): Promise<boolean> {
  try {
    await run()
    return true
  } catch (e) {
    notify(`Couldn't ${what}: ${e instanceof Error ? e.message : String(e)}`, 'error')
    return false
  }
}
