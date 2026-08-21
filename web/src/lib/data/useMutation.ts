import { useCallback, useRef, useState } from 'react'
import { invalidateSpecs, type CacheKeySpec } from './store'

/** ── A write declares what it makes stale ─────────────────────────────────────────────────
 *
 *  The old shape was `await api.deleteThing(id); invalidateCache('things'); refresh()` — three
 *  statements, of which only the first is the mutation. Two consequences, both measured:
 *
 *  · The invalidation was INFERRED by whoever wrote that line, from whatever they happened to
 *    know. `invalidateCache('tasks')` was written at all three task-write sites and
 *    `tasks-all` — the key feeding the only UI where a task's dependencies are chosen, and
 *    `persist: true` on top — was never dropped by anything, ever. Four instances of that
 *    family are written up in `splitCollectionBusts.test.ts` and `siblingCacheStaleness.test.ts`.
 *  · The `refresh()` beside it only refreshed the caller's OWN hook. Any other mounted reader
 *    of the same collection kept painting the pre-write value.
 *
 *  Here the invalidation map is part of the mutation's declaration, in the same object literal
 *  as the call that writes — so it is reviewed with the write, and a reader looking for "what
 *  does saving this affect?" finds the answer at the write rather than by grepping for busts.
 *  The store then reaches every mounted reader itself; nothing calls `refresh()`.
 *
 *  🔑 PREFER `{ prefix }` FOR A COLLECTION. `{ prefix: 'tasks' }` covers `tasks`, `tasks-all`
 *  and any key added later; `'tasks'` covers exactly one and rots the moment a second reader
 *  of that collection appears.
 */
export interface MutationSpec<A extends unknown[], R> {
  /** The write itself. */
  run: (...args: A) => Promise<R>
  /** Every cache key this write makes stale — the invalidation map, declared HERE.
   *
   *  A function form receives the result and the arguments, for a write whose blast radius
   *  depends on what it wrote (`{ prefix: `chat:session:${id}` }`). */
  invalidates: readonly CacheKeySpec[] | ((res: R, ...args: A) => readonly CacheKeySpec[])
  /** Anything that is not a cache concern — a toast, a navigation, closing a dialog. */
  onSuccess?: (res: R, ...args: A) => void
  /** Handle the rejection. Omit and `mutate` re-throws, so a caller can `await` and catch. */
  onError?: (err: unknown, ...args: A) => void
}

export interface MutationResult<A extends unknown[], R> {
  /** Runs the write, then applies the declared invalidation map. STABLE identity. */
  mutate: (...args: A) => Promise<R>
  pending: boolean
  error: unknown
}

export function useMutation<A extends unknown[], R>(spec: MutationSpec<A, R>): MutationResult<A, R> {
  // The spec is a fresh object literal on every render by design — it closes over props. Held
  // in a ref so `mutate` can keep a stable identity: an unstable callback that a consumer then
  // dependency-lists is the shape that produced 289k requests once already.
  const specRef = useRef(spec)
  specRef.current = spec
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const mutate = useCallback(async (...args: A): Promise<R> => {
    const { run, invalidates, onSuccess, onError } = specRef.current
    setPending(true)
    setError(null)
    try {
      const res = await run(...args)
      // AFTER the write lands, so a reader that re-fetches on the epoch bump cannot race ahead
      // of the change it is being told about.
      invalidateSpecs(typeof invalidates === 'function' ? invalidates(res, ...args) : invalidates)
      onSuccess?.(res, ...args)
      return res
    } catch (e) {
      setError(e)
      if (onError) { onError(e, ...args); return undefined as unknown as R }
      throw e
    } finally {
      setPending(false)
    }
  }, [])

  return { mutate, pending, error }
}
