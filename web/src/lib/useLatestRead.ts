import { useCallback, useEffect, useRef } from 'react'

/** Read whenever `key` changes, with at most one of this caller's reads on the wire.
 *
 *  For a read a page re-asks on its own schedule: after every turn, on a socket frame, after a
 *  write. Each read aborts the one before it, and unmounting aborts the last, so no read nobody waits
 *  for keeps a connection, and an aborted read's answer or failure is never applied.
 *
 *  🔴 The chat's organize chip re-read after every turn and only stopped LISTENING to the read
 *  before (a `live` flag in its cleanup), so a slow answer kept its connection to the end. A browser
 *  keeps six HTTP/1.1 connections to the gateway for every tab together; seven of those reads held
 *  them all, and the chat's own send then waited 138 s inside the browser.
 *
 *  `key` null reads nothing, and aborts a read that is out. Returns `reread`, which aborts any read
 *  that is out and reads again now: for a frame that says the answer changed, or a reconnect. */
export function useLatestRead<T>(
  key: string | null,
  read: (signal: AbortSignal) => Promise<T>,
  apply: (value: T) => void,
  fail: (error: unknown) => void,
): () => void {
  const out = useRef<AbortController | null>(null)
  const latest = useRef({ key, read, apply, fail })
  latest.current = { key, read, apply, fail }

  const reread = useCallback(() => {
    out.current?.abort()
    out.current = null
    const { key: current, read: start } = latest.current
    if (current === null) return
    const mine = new AbortController()
    out.current = mine
    let asked: Promise<T>
    try { asked = start(mine.signal) } catch (error) { asked = Promise.reject(error) }
    asked.then(
      (value) => { if (out.current === mine) { out.current = null; latest.current.apply(value) } },
      (error) => { if (out.current === mine) { out.current = null; latest.current.fail(error) } },
    )
  }, [])

  useEffect(() => {
    reread()
    return () => { out.current?.abort(); out.current = null }
  }, [key, reread])

  return reread
}
