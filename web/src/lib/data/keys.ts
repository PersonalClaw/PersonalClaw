/** ── Declared cache keys ──────────────────────────────────────────────────────────────────
 *
 *  Every cached read in the app is keyed by a string, and until this file existed those
 *  strings were declared nowhere: each call site invented one inline. That is what let the
 *  same collection be cached under two keys in two namespaces (`tasks` + `tasks-all`,
 *  `settings:models-loaded` + `dashboard:on-this-machine`) with a mutation able to bust only
 *  one of them — see `splitCollectionBusts.test.ts` for the four measured instances.
 *
 *  A key's NAMESPACE is the segment before its first `:` (a key with no `:` is its own
 *  namespace). The namespace is what carries policy: how long a cached value stays fresh,
 *  and therefore whether a cached first paint may be shown as final or must be labelled as
 *  still loading. `dataLayerAdoption.test.ts` walks every literal key in the tree and fails
 *  on one whose namespace is not declared here, so a new namespace is a deliberate entry
 *  rather than a typo that silently gets the default.
 *
 *  🔑 NAME A KEY AFTER THE COLLECTION IT READS, NOT THE SURFACE THAT READS IT. `chat:
 *  artifact-picker` could never be reached by `invalidateKeys('artifacts:', true)`; renaming
 *  it into its collection's namespace is what made the bust cover it.
 */

/** How long a cached value is considered FRESH. Past this age the value may still be
 *  painted instantly — that is the whole point of the cache — but the surface is told it is
 *  stale (`stale: true`) so it can label the paint instead of presenting it as current. */
export interface NamespacePolicy {
  staleAfterMs: number
  /** Why this number, in one line. Read by nobody at runtime; read by everybody editing. */
  why: string
}

const SECOND = 1000
const MINUTE = 60 * SECOND

/** Live data: re-read constantly, goes wrong fast. */
const LIVE: NamespacePolicy = { staleAfterMs: 5 * SECOND, why: 'live — a few seconds old is already suspect' }
/** Collections a user edits: a write elsewhere in the app invalidates them explicitly, so
 *  the timer is only a backstop for a change made outside this tab. */
const COLLECTION: NamespacePolicy = { staleAfterMs: 30 * SECOND, why: 'user-owned collection — busted on write, timer is the out-of-band backstop' }
/** Configuration and schemas: slow, large, and rarely changed behind the app's back. */
const CONFIG: NamespacePolicy = { staleAfterMs: 2 * MINUTE, why: 'config/schema — slow to fetch, rarely changes out of band' }

/** The one registry. Alphabetical; each entry is a namespace actually present in the tree. */
export const CACHE_NAMESPACES: Record<string, NamespacePolicy> = {
  agent: LIVE,
  agents: LIVE,
  'app-catalog': CONFIG,
  'app-config': CONFIG,
  'app-host': CONFIG,
  'app-uninstall': COLLECTION,
  apps: COLLECTION,
  artifacts: COLLECTION,
  autonomy: CONFIG,
  chat: COLLECTION,
  code: COLLECTION,
  companion: CONFIG,
  config: CONFIG,
  dashboard: LIVE,
  dirtree: COLLECTION,
  discover: COLLECTION,
  inbox: LIVE,
  /** `#/companion`'s four sections (`MC-6`). Each is hyphen-suffixed so it sits in its
   *  COLLECTION's namespace rather than a `companion:` one — `splitCollectionBusts.test.ts`
   *  derives a namespace by stripping `-…`, so `invalidateKeys('inbox', true)` reaches the
   *  phone's copy and a phone action reaches the desktop's. `namespaceOf` here splits on `:`
   *  only, which is why each suffixed key needs its own declaration (same as `tasks-all`).
   *  Policy matches the collection it projects, never the reader. */
  'inbox-companion': LIVE,
  knowledge: COLLECTION,
  /** The Learning page's proposal facets, week rollup, health and judge bench. Keys are built
   *  by `pages/learning/proposalCache.ts` rather than written inline, which is why a census of
   *  literal first arguments misses them — `dataLayerAdoption.test.ts` resolves module-level
   *  key constants for exactly this reason. */
  learning: COLLECTION,
  /** One loop's detail, seeded for an instant cockpit paint. Not LIVE: the cockpit runs its
   *  own authoritative fetch + SSE on mount, and this entry exists only to fill the first
   *  frame — under a 5s window every revisit would paint a labelled-stale header for nothing. */
  loop: COLLECTION,
  loops: LIVE,
  'loops-companion': LIVE,
  models: CONFIG,
  notifications: LIVE,
  'notifications-companion': LIVE,
  onboarding: CONFIG,
  // PA-5's digest card. LIVE, not COLLECTION: the digest is a read of what a scheduled run just
  // did, and it changes behind the app's back on every fire — a 30-second-fresh digest could
  // show a pending proposal the user already answered on another surface.
  proactive: LIVE,
  prompt: COLLECTION,
  'prompt-snippets': COLLECTION,
  prompts: COLLECTION,
  projects: COLLECTION,
  settings: CONFIG,
  skill: COLLECTION,
  'skill-proposals': COLLECTION,
  'skill-proposals-count': COLLECTION,
  skills: COLLECTION,
  snippet: COLLECTION,
  system: LIVE,
  tasklist: COLLECTION,
  tasks: COLLECTION,
  'tasks-all': COLLECTION,
  'tasks-companion': COLLECTION,
  tools: CONFIG,
  triggers: COLLECTION,
  /** Definitions, runs and the surfacing column. `#/workflows` hand-rolled these three reads with
   *  `useState` + `Promise.all` + a mount effect and had no cache at all, so it is a MIGRATION the
   *  `useCachedData` census could not see. Runs age fast, hence LIVE. */
  workflows: LIVE,
}

/** Applied to a key whose namespace is not declared. Deliberately the shortest policy: an
 *  undeclared namespace is a mistake, and the safe failure mode is "assume it went stale",
 *  which labels the paint rather than presenting unknown-age data as current. */
export const UNDECLARED_POLICY: NamespacePolicy = {
  staleAfterMs: 0,
  why: 'undeclared namespace — treated as always stale so nothing silently presents unknown-age data as fresh',
}

export function namespaceOf(key: string): string {
  const i = key.indexOf(':')
  return i === -1 ? key : key.slice(0, i)
}

export function policyFor(key: string): NamespacePolicy {
  return CACHE_NAMESPACES[namespaceOf(key)] ?? UNDECLARED_POLICY
}

export function staleAfterMsFor(key: string): number {
  return policyFor(key).staleAfterMs
}
