import { staleAfterMsFor } from './keys'

/** ── The one cache ────────────────────────────────────────────────────────────────────────
 *
 *  There used to be a cache per mechanism: a module-level `Map` in `useCachedData`, a
 *  sessionStorage mirror beside it, and ad-hoc `useState(null) + useEffect(load)` in the
 *  surfaces that had not adopted the hook. Two caches over one endpoint is what produced the
 *  stale-then-flicker repaint the user reads as a bug: whichever copy painted first was
 *  whichever mounted first, and the other one replaced it a moment later.
 *
 *  This module is the only cache. It owns four things the old shape did not have:
 *
 *  1. **Dedup.** `fetchKey` collapses concurrent reads of one key onto ONE in-flight promise,
 *     so N components mounting at once produce one request. The old hook fired one request
 *     per hook instance; five widgets over `dashboard:health` meant five identical GETs.
 *  2. **Subscribers.** An entry change reaches every MOUNTED reader of that key. Without this
 *     an invalidation could only ever affect the next mount — measured and written up in
 *     `splitCollectionBusts.test.ts`: "a bust is only load-bearing while a reader is
 *     mounted", which was false of the old store, so every mutation had to hand-call
 *     `refresh()` beside its `invalidateCache()` and any reader it did not know about kept
 *     painting the pre-write value.
 *  3. **Age.** Every entry records WHEN it was written, so `stale` is a fact rather than a
 *     guess. A cached first paint can therefore be either fresh or explicitly labelled.
 *  4. **Epochs.** Invalidation does not DELETE the value — it stamps the entry stale and
 *     bumps its epoch. Deleting is what made the near-universal `invalidateCache(k);
 *     refresh()` idiom blank every panel: the value vanished, each panel's `if (!data) return
 *     <Skeleton/>` gate fired, and an interaction that changed almost nothing flashed a full
 *     remount. Stamping keeps the paint and marks it not-current, which is the honest state.
 */

export interface CacheEntry<T = unknown> {
  value: T
  /** `Date.now()` when this value landed. `0` means "invalidated — present but not current". */
  at: number
  /** Bumped by an invalidation. A reader watching this key re-fetches when it changes. */
  epoch: number
}

/** What a mutation declares it affects. A bare string is an exact key; `{ prefix }` covers
 *  every key in that subtree, which is how one entry keeps a collection's sibling keys in
 *  step — including a key added after the mutation was written. */
export type CacheKeySpec = string | { prefix: string }

const _entries = new Map<string, CacheEntry>()
const _inflight = new Map<string, Promise<unknown>>()
const _subs = new Map<string, Set<() => void>>()
/** Keys whose value should survive a FULL page reload, mirrored into sessionStorage. */
const _persisted = new Set<string>()

const _SS_PREFIX = 'cache:'

function _notify(key: string): void {
  const set = _subs.get(key)
  if (!set) return
  // Copy: a subscriber may unsubscribe (unmount) while we iterate.
  for (const fn of [...set]) fn()
}

function _readSession(key: string): CacheEntry | undefined {
  try {
    const raw = sessionStorage.getItem(_SS_PREFIX + key)
    if (raw == null) return undefined
    const parsed = JSON.parse(raw) as { v: unknown; at: number }
    // 🔑 SEEDED AT `at: 0` — "present, not current" — NO MATTER HOW RECENTLY IT WAS WRITTEN.
    //
    // The written-at time IS persisted (it is in the record, and it is worth having when reading
    // one by hand), and it is deliberately not trusted here. A value from a previous page load
    // says nothing about the server now: the app was not running, so nothing invalidated this key
    // when it changed, and "written 6 seconds ago" is not evidence of "current".
    //
    // Measured on the pre-fix build, `#/settings` Inbox tile, `cache:settings:inbox`: retention
    // set to 30, tile settled, retention changed to 7 out of band, hard reload with the
    // revalidation held. FIRST PAINT = "30 day retention", `[data-stale]` = 0, `[aria-busy]` = 0,
    // no "updating" copy anywhere — then it silently became 7. Trusting the persisted age
    // reproduced that same silent paint on the MIGRATED build (measured, `CONTROL` run), which is
    // what settled this line: across a reload, a persisted value is a labelled paint, never a
    // fresh one.
    return { value: parsed.v, at: 0, epoch: 0 }
  } catch { return undefined }
}

function _writeSession(key: string, entry: CacheEntry): void {
  try { sessionStorage.setItem(_SS_PREFIX + key, JSON.stringify({ v: entry.value, at: entry.at })) }
  catch { /* quota / not serializable — the in-memory entry is still authoritative */ }
}

function _dropSession(key: string): void {
  try { sessionStorage.removeItem(_SS_PREFIX + key) } catch { /* ignore */ }
}

/** The entry for a key, seeding from sessionStorage on the first read of a persisted key. */
export function readEntry<T>(key: string, persist = false): CacheEntry<T> | undefined {
  const hit = _entries.get(key) as CacheEntry<T> | undefined
  if (hit) return hit
  if (!persist) return undefined
  const seeded = _readSession(key) as CacheEntry<T> | undefined
  if (seeded) _entries.set(key, seeded)
  return seeded
}

/** Is this entry past its namespace's freshness window? An absent entry is not "stale" —
 *  it is absent, which is a loading state, not a labelled paint. */
export function isStale(key: string, entry: CacheEntry | undefined, staleAfterMs?: number): boolean {
  if (!entry) return false
  if (entry.at === 0) return true
  return Date.now() - entry.at > (staleAfterMs ?? staleAfterMsFor(key))
}

/** Store a value as freshly-landed and tell every mounted reader. Does NOT bump the epoch:
 *  a landed value must not re-trigger the fetch that produced it. */
export function writeQuery(key: string, value: unknown, persist = false): void {
  const prev = _entries.get(key)
  const entry: CacheEntry = { value, at: Date.now(), epoch: prev?.epoch ?? 0 }
  _entries.set(key, entry)
  if (persist || _persisted.has(key)) { _persisted.add(key); _writeSession(key, entry) }
  _notify(key)
}

/** The cached value WITHOUT triggering a fetch — for a caller that owns an authoritative
 *  fetch of its own (a mount effect + SSE, say) and only wants an instant first paint.
 *
 *  Returns the value only while it is FRESH. A stale snapshot handed to a caller that has no
 *  way to know it is stale is precisely the silent stale paint; such a caller gets
 *  `undefined` and shows its loading state until its own fetch lands. Use `peekEntry` when
 *  you want the value plus its age and intend to label it. */
export function peekQuery<T>(key: string): T | undefined {
  const entry = readEntry<T>(key, true)
  if (!entry || isStale(key, entry)) return undefined
  return entry.value
}

/** The raw entry — value plus age — for a caller that will label a stale paint itself. */
export function peekEntry<T>(key: string): CacheEntry<T> | undefined {
  return readEntry<T>(key, true)
}

/** Mark a key (or a whole prefix) not-current and make every mounted reader re-fetch.
 *
 *  The value is KEPT and stamped `at: 0`, so readers keep painting something while the
 *  refetch is in flight — labelled stale, never presented as current. `prefix: true` is how
 *  one mutation keeps a collection's sibling keys in step. */
export function invalidateKeys(keyOrPrefix: string, prefix = false): void {
  const hit = (k: string) => {
    const e = _entries.get(k)
    if (e) { _entries.set(k, { ...e, at: 0, epoch: e.epoch + 1 }) }
    else { _entries.set(k, { value: undefined, at: 0, epoch: 1 }) }
    _dropSession(k)
    // An in-flight fetch started BEFORE the write may resolve with pre-write data. Drop it
    // from the dedup table so the reader's post-invalidation fetch is a real new request
    // rather than a join onto the stale one already on the wire.
    _inflight.delete(k)
    _notify(k)
  }
  if (!prefix) { hit(keyOrPrefix); return }
  const keys = new Set<string>([..._entries.keys(), ..._subs.keys()])
  for (const k of keys) if (k.startsWith(keyOrPrefix)) hit(k)
}

/** Apply a mutation's declared invalidation map. */
export function invalidateSpecs(specs: readonly CacheKeySpec[]): void {
  for (const s of specs) {
    if (typeof s === 'string') invalidateKeys(s)
    else invalidateKeys(s.prefix, true)
  }
}

/** Run `fetcher` for `key`, collapsing concurrent callers onto ONE request.
 *
 *  This is the dedup. It is keyed on the cache key alone — deliberately not on the fetcher's
 *  identity, which is a fresh closure on every render at essentially every call site. Keying
 *  on a per-render identity is how a dedup table degenerates into no dedup at all, and how a
 *  `useEffect` that looks correctly dependency-listed becomes an unbounded fetch loop: 289k
 *  requests and `net::ERR_INSUFFICIENT_RESOURCES`, measured in a browser, from exactly that
 *  shape. `dataLayer.test.ts` bounds the request count for both cases. */
export function fetchKey<T>(key: string, fetcher: () => Promise<T>, persist = false): Promise<T> {
  const joined = _inflight.get(key) as Promise<T> | undefined
  if (joined) return joined
  const p = fetcher()
    .then((res) => { writeQuery(key, res, persist); return res })
    .finally(() => { if (_inflight.get(key) === p) _inflight.delete(key) })
  _inflight.set(key, p as Promise<unknown>)
  return p
}

/** Is a request for this key on the wire right now? */
export function isFetching(key: string): boolean {
  return _inflight.has(key)
}

/** Watch one key. The callback fires when the entry's value or epoch changes; the caller
 *  re-reads the entry and decides what that means. Returns the unsubscribe. */
export function subscribeKey(key: string, fn: () => void): () => void {
  let set = _subs.get(key)
  if (!set) { set = new Set(); _subs.set(key, set) }
  set.add(fn)
  return () => {
    const s = _subs.get(key)
    if (!s) return
    s.delete(fn)
    if (s.size === 0) _subs.delete(key)
  }
}

/** Test-only: wipe every cache, in-flight entry and subscriber. Production code must not
 *  call this — a mutation invalidates the keys it declares, never the whole store. */
export function resetDataStore(): void {
  _entries.clear()
  _inflight.clear()
  _subs.clear()
  _persisted.clear()
  // Sweep EVERY persisted key, not just the ones this module instance wrote. A test that calls
  // `vi.resetModules()` and re-imports the component under test gets a fresh copy of this
  // module — so its `_persisted` set is empty here while the sessionStorage rows the previous
  // copy wrote are still there, seeded straight back in with their original (fresh) age, and
  // the next mount never fetches at all. Measured: `savingsByCompressor.test.tsx` painted the
  // first case's numbers in the third case, twice.
  try {
    for (const k of Object.keys(sessionStorage)) if (k.startsWith(_SS_PREFIX)) sessionStorage.removeItem(k)
  } catch { /* no storage in this environment */ }
}

/** Test-only: the keys currently held, for asserting what a mutation's map actually reached. */
export function cachedKeys(): string[] {
  return [..._entries.keys()]
}
