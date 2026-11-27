import { useCallback, useEffect, useRef, useState } from 'react'
import { api, isTransientFailure, type Loop } from './api'
import { useVisiblePoll } from './useVisiblePoll'

/** How long a read that failed waits before it is made again, as the cockpit's first load does
 *  (`useCodeCockpit`). */
const AGAIN_MS = 4000

/** Read the loop *id* for a section that routes on its status (a loop that has not launched opens
 *  on its planning or its Plan Review, not its cockpit), and read it again every 4 s, while the
 *  page shows, after a read that failed in a way that passes: a 5xx, a 429 or a dropped request.
 *  Before, one failed read on a reload left a still-planning loop on its cockpit, which said it had
 *  finished, and nothing read its status again.
 *
 *  *onLoop* is handed each loop read, and *onGone* a refusal no retry mends (a 4xx: no such loop,
 *  or no such id). `null` reads nothing. The function returned reads it once more: for a status
 *  the cockpit's own read or stream brought in. */
export function useLoopStatusRead(
  id: string | null,
  onLoop: (loop: Loop | null | undefined) => void,
  onGone: () => void,
): () => void {
  const on = useRef({ onLoop, onGone })
  on.current = { onLoop, onGone }
  const [attempt, setAttempt] = useState(0)
  const [failing, setFailing] = useState(false)
  useEffect(() => {
    if (!id) { setFailing(false); return }
    let alive = true
    api.uLoop(id).then((loop) => {
      if (!alive) return
      setFailing(false)
      on.current.onLoop(loop)
    }).catch((e) => {
      if (!alive) return
      const passes = isTransientFailure(e)
      setFailing(passes)
      if (!passes) on.current.onGone()
    })
    return () => { alive = false }
  }, [id, attempt])
  useVisiblePoll(() => setAttempt((n) => n + 1), failing ? AGAIN_MS : null, { immediate: false })
  return useCallback(() => setAttempt((n) => n + 1), [])
}
