/** ── One data layer ───────────────────────────────────────────────────────────────────────
 *
 *  The app's single way to read and invalidate server data. It replaced `lib/useCachedData.ts`
 *  outright — that file is DELETED, not kept beside this one, because two caches over one
 *  endpoint is exactly what produced the stale-then-flicker repaint: whichever copy painted
 *  first was whichever component mounted first, and the other replaced it a moment later.
 *
 *      read      useQuery(key, fetcher, { persist? })
 *      write     useMutation({ run, invalidates })          ← the map lives with the write
 *      keys      lib/data/keys.ts                            ← namespaces + freshness windows
 *      label     ui/StaleNotice                              ← a stale paint says so
 *
 *  `dataLayerAdoption.test.ts` is the completeness ratchet: it fails if a direct call site of
 *  the old helper reappears, if a literal cache key's namespace is undeclared, or if the count
 *  of bare invalidations outside a declared mutation map rises.
 */
export { useQuery, type QueryResult, type QueryStatus } from './useQuery'
export { useMutation, type MutationSpec, type MutationResult } from './useMutation'
export {
  invalidateKeys,
  invalidateSpecs,
  peekQuery,
  peekEntry,
  writeQuery,
  readEntry,
  isStale,
  isFetching,
  subscribeKey,
  resetDataStore,
  cachedKeys,
  type CacheEntry,
  type CacheKeySpec,
} from './store'
export {
  CACHE_NAMESPACES,
  UNDECLARED_POLICY,
  namespaceOf,
  policyFor,
  staleAfterMsFor,
  type NamespacePolicy,
} from './keys'
