import { useCallback, useEffect, useRef, useState } from 'react'
import { notify } from '../../app/appSdk'

/** One optimistic row mutation, with a REVERT on failure — the companion's whole
 *  interaction contract in one hook (MOBILE-COMPANION S2 T2.2: *"every action
 *  round-trips against a dev gateway; optimistic UI reverts on failure"*).
 *
 *  🪤 IT EXISTS BECAUSE THE OVERLAY MUST BE RECONCILED AGAINST THE SERVER.
 *  `MC-3` shipped this pattern by hand for approvals and the FIRST version was wrong
 *  in a way no unit test noticed: the optimistically-hidden ids were never pruned, so
 *  a row the backend was still serving stayed hidden forever and the phone rendered a
 *  live queue as "nothing waiting on you" (see the plan's Execution log — found by
 *  driving a real gateway, not by reading the code). `MC-6` adds four more sections
 *  with the same shape, so the reconciliation lives here ONCE rather than being
 *  re-derived four times and getting it wrong somewhere.
 *
 *  The rule: a patch survives only while its POST is still in flight. On every fetch,
 *  every patch whose call has SETTLED is dropped and the server's row is what renders.
 *  A successful call therefore shows its optimistic value until the confirming fetch
 *  lands (no flicker), and a *stale* optimistic value can never outlive one fetch.
 *
 *  Depends on `fetched` alone, never on `busy`: depending on `busy` would drop the
 *  patch the instant the POST returned — before the fetch that confirms it — which is
 *  exactly the flicker back to the old value the optimism exists to prevent.
 *
 *  @param fetched the query's `data`. Identity changes on every fetch; `undefined`
 *                 while the first one is in flight.
 */
export function useCompanionAction<P extends object>(fetched: unknown) {
  const [patches, setPatches] = useState<Map<string, P>>(new Map())
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const busyRef = useRef(busy)
  busyRef.current = busy

  useEffect(() => {
    if (fetched === undefined) return
    setPatches((m) => {
      const next = new Map([...m].filter(([id]) => busyRef.current.has(id)))
      return next.size === m.size ? m : next
    })
  }, [fetched])

  /** Apply *patch* to row *id*, call the backend, and REVERT the patch if it fails.
   *
   *  `what` is the sentence fragment the failure toast reads — "pause Nightly sweep" —
   *  so a user is told which action failed and on which row, not just "error".
   */
  const act = useCallback(
    async (id: string, patch: P, call: () => Promise<unknown>, what: string, after?: () => void) => {
      setBusy((s) => new Set(s).add(id))
      setPatches((m) => new Map(m).set(id, patch))
      let ok = false
      try {
        await call()
        ok = true
      } catch (e) {
        // REVERT. The row snaps back to the server's truth and the gateway's own
        // sentence is announced — `api.*` rejects with an ApiError whose message is
        // already user-readable (`lib/errText`).
        setPatches((m) => {
          const n = new Map(m)
          n.delete(id)
          return n
        })
        notify(`Couldn't ${what} — ${(e as Error)?.message || 'the gateway did not respond'}`, 'error')
      } finally {
        setBusy((s) => {
          const n = new Set(s)
          n.delete(id)
          return n
        })
        after?.()
      }
      return ok
    },
    [],
  )

  /** The row as the user should see it right now: the server's row with any in-flight
   *  optimistic patch laid over it. */
  const view = useCallback(<T extends object>(id: string, row: T): T => {
    const patch = patches.get(id)
    return patch ? { ...row, ...patch } : row
  }, [patches])

  return { act, view, busy }
}
