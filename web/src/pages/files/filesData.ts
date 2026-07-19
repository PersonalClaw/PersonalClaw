import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type FsEntry, type FsRoot } from '../../lib/api'

/** Load the allowed root directories the explorer may browse. */
export function useFileRoots() {
  const [roots, setRoots] = useState<FsRoot[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    api.fileRoots().then((r) => { if (alive) { setRoots(r.roots); setLoading(false) } }).catch(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])
  return { roots, loading }
}

// Session-persisted dir cache: survives a page refresh so the tree paints its last-known
// listing INSTANTLY instead of flashing empty/"Loading…" for a few seconds while the first
// fetch resolves (observed live). sessionStorage (not local) so it's per-tab + naturally
// drops on tab close; bounded so it can't grow unbounded across many browsed roots.
const DIR_CACHE_KEY = 'files-dir-cache'
const DIR_CACHE_MAX_PATHS = 400

function loadPersistedCache(): Record<string, FsEntry[]> {
  try {
    const raw = sessionStorage.getItem(DIR_CACHE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed as Record<string, FsEntry[]> : {}
  } catch { return {} }
}

function persistCache(cache: Record<string, FsEntry[]>): void {
  try {
    const paths = Object.keys(cache)
    // Keep the cache bounded — drop oldest-inserted paths past the cap (insertion order).
    const trimmed = paths.length > DIR_CACHE_MAX_PATHS
      ? Object.fromEntries(paths.slice(paths.length - DIR_CACHE_MAX_PATHS).map((p) => [p, cache[p]]))
      : cache
    sessionStorage.setItem(DIR_CACHE_KEY, JSON.stringify(trimmed))
  } catch { /* quota/serialization failure → skip persistence, in-memory still works */ }
}

/** Per-directory listing cache + lazy loader for the tree. */
export function useDirCache() {
  // Seed from sessionStorage so a refresh repaints the last-known tree immediately
  // (then the live fetch reconciles), instead of flashing empty.
  const [cache, setCache] = useState<Record<string, FsEntry[]>>(loadPersistedCache)
  const inflight = useRef<Record<string, boolean>>({})
  // Mirror the cache in a ref so the callbacks below can read the latest listing
  // WITHOUT depending on the `cache` state. Otherwise `load`/`invalidateSubtree` (and
  // the returned object) take a new identity on every cache mutation — each 8s poll +
  // every invalidate — which re-fires every consumer's `[…, dirs]` effect: the root
  // FileTree re-runs `dirs.load(rootPath)` + setEntries, so the tree VISIBLY RELOADS
  // on the cockpit's frequent re-renders while a worker writes files (observed live).
  const cacheRef = useRef(cache)
  cacheRef.current = cache
  // Mirror the live cache into sessionStorage so the next refresh paints instantly.
  useEffect(() => { persistCache(cache) }, [cache])
  // Per-path invalidation generation. An invalidate bumps the path's gen; a load that
  // STARTED before that bump must NOT write its (now-stale) result back — else an
  // in-flight listing begun before the worker wrote a file resolves AFTER the
  // invalidate and re-populates the just-cleared entry with the pre-write listing,
  // making the new file vanish from the tree until the next manual refresh.
  const gen = useRef<Record<string, number>>({})

  // All callbacks are identity-stable (empty dep arrays + cacheRef reads) so the
  // returned object never changes — consumers' effects keyed on `dirs` run once.
  const load = useCallback(async (path: string, force = false): Promise<FsEntry[]> => {
    const cached = cacheRef.current[path]
    if (!force && cached) return cached
    if (inflight.current[path]) return cached ?? []
    inflight.current[path] = true
    const startGen = gen.current[path] ?? 0
    try {
      const r = await api.fileList(path)
      // Drop the result if this path was invalidated while the fetch was in flight.
      if ((gen.current[path] ?? 0) === startGen) setCache((c) => ({ ...c, [path]: r.entries }))
      return r.entries
    } catch {
      return []
    } finally {
      inflight.current[path] = false
    }
  }, [])

  const bumpGen = (path: string) => { gen.current[path] = (gen.current[path] ?? 0) + 1 }

  const invalidate = useCallback((path: string) => {
    bumpGen(path)
    setCache((c) => { const next = { ...c }; delete next[path]; return next })
  }, [])

  // Drop the cached listing for `root` AND every loaded directory under it. A
  // live worker writes files into subdirectories too (src/, tests/), so a
  // root-only invalidate would leave an expanded subdir's listing stale — the new
  // file wouldn't appear there until the user manually re-expanded it.
  const invalidateSubtree = useCallback((root: string) => {
    const r = root.replace(/\/$/, '')
    setCache((c) => {
      const next: Record<string, FsEntry[]> = {}
      for (const [k, v] of Object.entries(c)) {
        if (k === r || k.startsWith(r + '/')) continue  // drop at-or-under root
        next[k] = v
      }
      return next
    })
    // Bump the gen for the root + every CURRENTLY-cached path under it (kept out of the
    // setCache updater so it stays pure). The root is bumped even if uncached, so an
    // in-flight load of it (begun pre-write) is rejected when it resolves.
    bumpGen(r)
    for (const k of Object.keys(gen.current)) if (k === r || k.startsWith(r + '/')) bumpGen(k)
    for (const k of Object.keys(cacheRef.current)) if (k === r || k.startsWith(r + '/')) bumpGen(k)
  }, [])

  // Stable object identity (functions never change) so `[…, dirs]` consumer effects
  // don't re-fire; only `cache` flips, which is consumed via render, not effects.
  return useMemo(() => ({ cache, load, invalidate, invalidateSubtree }), [cache, load, invalidate, invalidateSubtree])
}

/** Git branch + per-file porcelain status for the active root (best-effort).
 *  `state` distinguishes the three outcomes that otherwise all present as an empty
 *  `statuses` map — loading, loaded (genuinely clean OR with changes), and error —
 *  so a consumer can avoid showing "working tree is clean" for an in-flight or
 *  failed fetch (which would be a false "clean"). */
export function useGitStatus(rootPath: string | null, nonce = 0) {
  const [branch, setBranch] = useState('')
  const [statuses, setStatuses] = useState<Record<string, string>>({})
  const [state, setState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle')
  // The backend returns an empty repoRoot when the path isn't inside a git repo — so a
  // consumer can tell "not version-controlled" apart from "clean repo" (both otherwise
  // present as empty statuses). Empty until loaded.
  const [repoRoot, setRepoRoot] = useState('')
  useEffect(() => {
    if (!rootPath) { setBranch(''); setStatuses({}); setRepoRoot(''); setState('idle'); return }
    let alive = true
    setState('loading')
    api.fileGitStatus(rootPath).then((r) => {
      if (!alive) return
      setBranch(r.branch || '')
      setStatuses(r.statuses || {})
      setRepoRoot(r.repoRoot || '')
      setState('loaded')
    }).catch(() => { if (alive) { setBranch(''); setStatuses({}); setRepoRoot(''); setState('error') } })
    return () => { alive = false }
  }, [rootPath, nonce])
  return { branch, statuses, state, repoRoot }
}
