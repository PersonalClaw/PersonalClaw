import { useCallback, useRef, useState } from 'react'
import { isStaleWrite, sameDocument, type Rebase, type Revisioned } from './staleWrite'

/** A save the gateway refused because the document changed since the page read it. */
export interface StaleConflict<T> {
  /** The copy the refused save was built from. */
  base: T
  /** What the user was saving. Kept until they choose, so nothing they typed is lost. */
  mine: T
  /** The stored document, re-read after the refusal. `undefined` while that read is out. */
  theirs?: Revisioned<T>
  /** The user's change re-applied onto `theirs`, or `null` when both sides changed the same
   *  part and it cannot be re-applied without choosing. `undefined` until `theirs` is read. */
  rebased?: T | null
  /** A failure while recovering (the re-read, or a re-applied save that failed for another
   *  reason), as the sentence to show. */
  error?: string
}

export interface StaleWriteGuard<T> {
  conflict: StaleConflict<T> | null
  /** A recovery step is on the wire. */
  busy: boolean
  /** Save `mine` over `base`. Resolves `true` when it landed and `false` when the gateway refused it
   *  as stale — the conflict is then set and the page keeps its draft. Any other failure rejects,
   *  for the page's own error line. */
  save: (base: Revisioned<T>, mine: T, rebase: Rebase<T>) => Promise<boolean>
  /** `save` for a change that IS an operation on the list: `op(base.value)` is saved, and the same
   *  `op` re-applies it onto what is stored now. */
  apply: (base: Revisioned<T>, op: Rebase<T>) => Promise<boolean>
  /** Save the user's change re-applied onto the stored document. */
  reapply: () => Promise<boolean>
  /** Save the user's version over the stored one — only after they reviewed a change that cannot
   *  be re-applied on its own. */
  keepMine: () => Promise<boolean>
  /** Drop the user's change; the page shows what is stored. */
  discard: () => void
  /** Read the stored document again after that read failed (`conflict.error`). */
  retry: () => Promise<void>
}

/** The one recovery for a stale whole-document save, shared by every surface that makes one.
 *
 *  `read` re-reads the stored document with its revision; `write` sends a whole document over a base
 *  revision. `onSaved` runs after any save that lands — the first try or a re-applied one — with the
 *  value now stored, so the page can drop its draft and refresh its cache. `onDiscard` runs when the
 *  user drops their change. */
export function useStaleWriteGuard<T>({ read, write, onSaved, onDiscard }: {
  read: () => Promise<Revisioned<T>>
  write: (next: T, base: string) => Promise<unknown>
  onSaved?: (saved: T) => void
  onDiscard?: () => void
}): StaleWriteGuard<T> {
  const [conflict, setConflict] = useState<StaleConflict<T> | null>(null)
  const [busy, setBusy] = useState(false)
  // The latest callbacks, without making their per-render identity part of the guard's.
  const opts = useRef({ read, write, onSaved, onDiscard })
  opts.current = { read, write, onSaved, onDiscard }
  const rebaseRef = useRef<Rebase<T> | null>(null)
  const conflictRef = useRef<StaleConflict<T> | null>(null)
  const put = (c: StaleConflict<T> | null) => { conflictRef.current = c; setConflict(c) }

  /** Re-read what is stored and work out whether the change still applies on top of it.
   *
   *  The answer lands only on the conflict it was asked for: a user who pressed Discard (or saved
   *  through) while the read was out has already moved on, and putting the conflict back when it
   *  lands would resurrect a notice they dismissed. */
  const reload = useCallback(async (c: StaleConflict<T>) => {
    try {
      const theirs = await opts.current.read()
      if (conflictRef.current !== c) return
      const rebased = rebaseRef.current ? rebaseRef.current(theirs.value) : null
      put({ ...c, theirs, rebased, error: undefined })
    } catch (e) {
      if (conflictRef.current !== c) return
      put({ ...c, error: `Couldn't read the current version: ${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  const refused = useCallback((base: T, mine: T) => {
    const c: StaleConflict<T> = { base, mine }
    put(c)
    void reload(c)
  }, [reload])

  const landed = useCallback((saved: T) => {
    put(null)
    rebaseRef.current = null
    opts.current.onSaved?.(saved)
  }, [])

  const save = useCallback(async (base: Revisioned<T>, mine: T, rebase: Rebase<T>) => {
    rebaseRef.current = rebase
    try {
      await opts.current.write(mine, base.revision)
    } catch (e) {
      if (!isStaleWrite(e)) throw e
      refused(base.value, mine)
      return false
    }
    landed(mine)
    return true
  }, [landed, refused])

  const apply = useCallback((base: Revisioned<T>, op: Rebase<T>) => {
    const mine = op(base.value)
    // An operation that no longer applies to the copy on screen has nothing to save.
    if (mine === null) return Promise.resolve(false)
    return save(base, mine, op)
  }, [save])

  /** Save `next` over the re-read document, staying in the conflict if it went stale again. */
  const saveOver = useCallback(async (next: T) => {
    const c = conflictRef.current
    if (!c?.theirs) return false
    setBusy(true)
    try {
      // Already there: the other change made the same edit, so there is nothing to write.
      if (!sameDocument(next, c.theirs.value)) await opts.current.write(next, c.theirs.revision)
      landed(next)
      return true
    } catch (e) {
      // It moved again while the user was deciding. Same change, same original base — only
      // what it lands on is newer.
      if (isStaleWrite(e)) {
        const again: StaleConflict<T> = { base: c.base, mine: c.mine }
        put(again)
        await reload(again)
        return false
      }
      put({ ...c, error: e instanceof Error ? e.message : String(e) })
      return false
    } finally {
      setBusy(false)
    }
  }, [landed, reload])

  const reapply = useCallback(async () => {
    const c = conflictRef.current
    if (c?.rebased == null) return false
    return saveOver(c.rebased)
  }, [saveOver])

  const keepMine = useCallback(async () => {
    const c = conflictRef.current
    if (!c) return false
    return saveOver(c.mine)
  }, [saveOver])

  const discard = useCallback(() => {
    put(null)
    rebaseRef.current = null
    opts.current.onDiscard?.()
  }, [])

  const retry = useCallback(async () => {
    const c = conflictRef.current
    if (!c) return
    const fresh: StaleConflict<T> = { base: c.base, mine: c.mine }
    put(fresh)
    await reload(fresh)
  }, [reload])

  return { conflict, busy, save, apply, reapply, keepMine, discard, retry }
}
