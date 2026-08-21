import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { fetchKey, isStale, readEntry, subscribeKey } from './store'
import { staleAfterMsFor } from './keys'

/** ── The one way to read server data ──────────────────────────────────────────────────────
 *
 *  Replaces `useCachedData`. Same stale-while-revalidate idea — paint what we have, refresh
 *  behind it — with the three things the old hook structurally could not say:
 *
 *  · **Is this paint current?** `stale` is computed from the entry's recorded age against its
 *    namespace's declared freshness window (`lib/data/keys.ts`). The old hook had no age, so
 *    a cached value was painted identically whether it landed 40ms or 40 minutes ago, with no
 *    indication, and was then silently replaced. That repaint is what reads as a bug even
 *    when both values are right.
 *  · **Is anyone else already asking?** Concurrent readers of one key share ONE request
 *    (`fetchKey`). The old hook fired one per hook instance.
 *  · **Did someone change this?** An invalidation reaches every MOUNTED reader, so a write is
 *    reflected without the caller hand-calling `refresh()` and without a reload.
 *
 *  🪤 `loading` IS NOT `revalidating`, AND NEITHER IS `stale`. Three different questions:
 *      loading       there is nothing to show yet          → gate a skeleton on this
 *      revalidating  a request is on the wire right now    → gate a spinner/pulse on this
 *      stale         what IS shown is past its window      → LABEL the paint with this
 *    Keying a freshness indicator on `loading` looks correct and never fires once a value is
 *    cached; that mistake is why the old hook grew a `revalidating` field, and `stale` is the
 *    third distinction it never grew.
 */
export type QueryStatus = 'loading' | 'success' | 'error'

export interface QueryResult<T> {
  /** The cached value, painted immediately when there is one. */
  data: T | undefined
  /** Nothing to show yet — the genuine first read for this key. Gate skeletons on this. */
  loading: boolean
  /** A request is in flight, INCLUDING a revalidation over a value already on screen. */
  revalidating: boolean
  /** There IS a value and it is past its namespace's freshness window. A surface that paints
   *  `data` while this is true must say so — see `ui/StaleNotice`. */
  stale: boolean
  error: unknown
  status: QueryStatus
  /** Re-run the fetch. STABLE identity across renders — see the note at the bottom. */
  refresh: () => void
}

export function useQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  opts: {
    /** Mirror this key into sessionStorage so its value (and its age) survive a full reload.
     *  For slow, rarely-changing config — not for live data that re-pulls anyway. */
    persist?: boolean
    /** Override the namespace's declared window. Prefer declaring the namespace. */
    staleAfterMs?: number
  } = {},
): QueryResult<T> {
  const { persist = false, staleAfterMs } = opts
  const window_ = staleAfterMs ?? staleAfterMsFor(key)

  // The value comes from the store, not from local state, so every mounted reader of a key
  // paints the same bytes — the property that makes "one cache" true rather than aspirational.
  const subscribe = useCallback((fn: () => void) => subscribeKey(key, fn), [key])
  const getSnapshot = useCallback(() => readEntry<T>(key, persist), [key, persist])
  const entry = useSyncExternalStore(subscribe, getSnapshot, () => undefined)

  const [tick, setTick] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const [inFlight, setInFlight] = useState(true)

  // Keep the latest fetcher without making its per-render identity a re-run dependency.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const epoch = entry?.epoch ?? 0

  useEffect(() => {
    let alive = true
    // A mount ALWAYS revalidates, exactly as the helper this replaced did. That is deliberate and
    // it is the conservative half of the design: the freshness window's job is to decide whether
    // the cached paint may go UNLABELLED, not whether to ask the server. Skipping the request for
    // a declared-fresh value was tried and measured — with `settings` at a 2-minute window the
    // `#/settings` tile painted a value the server had already changed and then never corrected
    // it inside that window, which is the same silent stale paint with a longer tail. Requests
    // are cheap here because `fetchKey` dedups them; a wrong number on screen is not.
    setInFlight(true)
    fetchKey<T>(key, () => fetcherRef.current(), persist)
      .then(() => { if (alive) setError(null) })
      .catch((e) => { if (alive) setError(e) })
      .finally(() => { if (alive) setInFlight(false) })
    return () => { alive = false }
    // `epoch` is the invalidation signal: a mutation bumps it and every mounted reader of the
    // key lands here and re-fetches (deduped to one request). A landed VALUE deliberately
    // does not bump it, or the fetch would re-trigger the fetch that produced it.
  }, [key, epoch, tick, persist, window_])

  const data = entry?.value as T | undefined
  const stale = isStale(key, entry, window_)

  // STABLE identity. `refresh` was a fresh closure on every render, and a consumer that
  // depends on it — `useEffect(() => { if (reloadKey) refresh() }, [reloadKey, refresh])` in
  // `dashboard/PinnedTiles` is the shipped example — then re-ran that effect on EVERY render,
  // called refresh, bumped `tick`, refetched, re-rendered, and looped. Measured in a browser:
  // 289,116 failed requests and `net::ERR_INSUFFICIENT_RESOURCES`, after which every artifact
  // fetch failed and the tile sat in its loading state forever. The effect looks correctly
  // dependency-listed, which is what makes an unstable identity a trap rather than a smell.
  const refresh = useCallback(() => setTick((t) => t + 1), [])

  const status: QueryStatus = data === undefined && error != null ? 'error'
    : data === undefined ? 'loading'
    : 'success'

  return { data, loading: data === undefined && inFlight, revalidating: inFlight, stale, error, status, refresh }
}
