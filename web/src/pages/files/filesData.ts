import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, type FsEntry, type FsRoot } from '../../lib/api'

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

/** Why a listing failed, in the user's words. The server already computed the
 *  distinction — a refused path answers 400/403, a missing one 404 — and the bare
 *  `catch { return [] }` below used to discard it, so "you are not allowed here",
 *  "that path does not exist" and "this folder is empty" all rendered as the tree's
 *  `emptyLabel` ("Empty"). The go-to-path box is the surface where that matters:
 *  it leaves the rejected path in the URL, so an unexplained empty tree reads as a
 *  real-but-empty location (#298). */
function listErrorLabel(e: unknown): string {
  const status = e instanceof ApiError ? e.status : 0
  if (status === 403) return 'You are not allowed to browse that location.'
  if (status === 404) return 'That path does not exist.'
  // 400 is the confinement validator refusing the path (outside the allowed roots,
  // or a traversal attempt) — a different sentence from "not found", because the
  // path may well exist and simply not be browsable from here.
  if (status === 400) return 'That path is outside the folders PersonalClaw can browse.'
  return 'Could not load that folder.'
}

/** Per-directory listing cache + lazy loader for the tree. */
export function useDirCache() {
  // Seed from sessionStorage so a refresh repaints the last-known tree immediately
  // (then the live fetch reconciles), instead of flashing empty.
  const [cache, setCache] = useState<Record<string, FsEntry[]>>(loadPersistedCache)
  // Per-path listing failure, so a consumer can say WHICH refusal it was instead of
  // falling through to the empty-tree label (#298). Deliberately NOT persisted: a
  // stale "not allowed" must never outlive the request that produced it.
  const [errors, setErrors] = useState<Record<string, string>>({})
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
      if ((gen.current[path] ?? 0) === startGen) {
        setCache((c) => ({ ...c, [path]: r.entries }))
        // A path that listed is no longer failing — clear a previous refusal so a
        // re-navigated-to path doesn't keep the old sentence. Same-object return when
        // there was nothing to clear, so the memo below keeps its identity.
        setErrors((m) => (path in m ? Object.fromEntries(Object.entries(m).filter(([k]) => k !== path)) : m))
      }
      return r.entries
    } catch (e) {
      // Keep the REASON instead of discarding it. Returning [] still holds (every
      // caller expects an array), but the consumer can now read why it is empty.
      // Same-message writes return the SAME object: a fresh object each time would
      // give the memo a new identity, re-fire the consumer effect, and refetch the
      // failing path forever.
      const msg = listErrorLabel(e)
      if ((gen.current[path] ?? 0) === startGen) setErrors((m) => (m[path] === msg ? m : { ...m, [path]: msg }))
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
  // don't re-fire; only `cache`/`errors` flip, and both are consumed via render, not
  // effects — a consumer that loads in an effect must key on `dirs.load` (stable),
  // never on `dirs`, or a failing path refetches on every error write.
  return useMemo(() => ({ cache, errors, load, invalidate, invalidateSubtree }), [cache, errors, load, invalidate, invalidateSubtree])
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
