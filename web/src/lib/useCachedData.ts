import { useEffect, useRef, useState } from 'react'

/**
 * Stale-while-revalidate data hook. The app shell loads instantly, but each page
 * fetched its data fresh on every mount (useState(null) + useEffect(load)) — so
 * revisiting a page always flashed "Loading…" and refetched from zero, and the
 * delay tracked whatever else the single-process gateway was busy with.
 *
 * This caches the last result per `key` in a module-level store (survives unmount/
 * remount + navigation). On revisit it returns the cached value IMMEDIATELY (no
 * loading flash) and revalidates in the background, swapping in fresh data when it
 * lands. `loading` is only true on the genuine first fetch for a key (nothing cached).
 *
 * Usage mirrors the old pattern:
 *   const { data, loading, refresh } = useCachedData('knowledge:items:'+filter, () => listKnowledge(...))
 */

const _cache = new Map<string, unknown>()

// Optional sessionStorage backing so a key's last value survives a FULL page
// reload (not just in-app unmount/remount). The module-level Map alone resets
// on reload, so config that "won't change too quick" would re-flash a skeleton
// every hard refresh. Persisted keys seed the Map from sessionStorage on first
// read, so a revisit-after-reload paints instantly and revalidates in the
// background. Opt-in per call via `{ persist: true }` — use for slow, rarely-
// changing config (providers, schemas), NOT for fast-moving live data.
const _SS_PREFIX = 'cache:'

function _seedFromSession<T>(key: string): T | undefined {
  if (_cache.has(key)) return _cache.get(key) as T
  try {
    const raw = sessionStorage.getItem(_SS_PREFIX + key)
    if (raw == null) return undefined
    const val = JSON.parse(raw) as T
    _cache.set(key, val)
    return val
  } catch { return undefined }
}

function _writeSession(key: string, val: unknown): void {
  try { sessionStorage.setItem(_SS_PREFIX + key, JSON.stringify(val)) } catch { /* quota/serialize — cache stays in-memory only */ }
}

export function useCachedData<T>(
  key: string,
  fetcher: () => Promise<T>,
  opts: { persist?: boolean } = {},
): { data: T | undefined; loading: boolean; error: unknown; refresh: () => void } {
  const { persist = false } = opts
  const cached = (persist ? _seedFromSession<T>(key) : _cache.get(key) as T | undefined)
  const [data, setData] = useState<T | undefined>(cached)
  const [loading, setLoading] = useState(cached === undefined)
  const [error, setError] = useState<unknown>(null)
  const [tick, setTick] = useState(0)
  // Keep the latest fetcher without making it a re-run dependency.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let alive = true
    const seeded = persist ? _seedFromSession<T>(key) : _cache.get(key) as T | undefined
    if (seeded === undefined) setLoading(true)
    // Show cached value instantly while we revalidate.
    setData(seeded)
    fetcherRef.current()
      .then((res) => {
        if (!alive) return
        _cache.set(key, res)
        if (persist) _writeSession(key, res)
        setData(res)
        setError(null)
      })
      .catch((e) => { if (alive) setError(e) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [key, tick, persist])

  return { data, loading, error, refresh: () => setTick((t) => t + 1) }
}

/** Read the last-cached value for a key WITHOUT triggering a fetch — for callers
 *  that already own an authoritative fetch (e.g. a mount effect + SSE) and only
 *  want the cached snapshot for an instant first paint, avoiding the duplicate
 *  network request `useCachedData` would fire. Pair with `writeCache` from the
 *  owning fetch so the snapshot stays warm across same-session re-navigations. */
export function peekCache<T>(key: string): T | undefined {
  return _cache.get(key) as T | undefined
}

/** Write a value into the shared cache from an external authoritative fetch, so a
 *  later `peekCache`/`useCachedData` read paints it instantly. In-memory only
 *  (matches the non-persist path); use for live data that re-pulls fresh anyway. */
export function writeCache(key: string, val: unknown): void {
  _cache.set(key, val)
}

/** Drop a cached entry (or all entries with a prefix) — e.g. after a mutation so
 *  the next read revalidates against a known-changed resource. Clears both the
 *  in-memory Map and any sessionStorage-persisted copy. */
export function invalidateCache(keyOrPrefix: string, prefix = false): void {
  const drop = (k: string) => { _cache.delete(k); try { sessionStorage.removeItem(_SS_PREFIX + k) } catch { /* ignore */ } }
  if (!prefix) { drop(keyOrPrefix); return }
  for (const k of [..._cache.keys()]) if (k.startsWith(keyOrPrefix)) drop(k)
}
