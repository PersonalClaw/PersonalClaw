import { useEffect, useRef, useState } from 'react'
import { api, type Loop } from '../../lib/api'

// Live (in-flight) statuses worth polling on a fast cadence; terminal ones we
// stop polling once reached.
const LIVE = new Set(['running', 'intake', 'planning', 'review'])
const TERMINAL = new Set(['complete', 'failed', 'stopped'])

/** A loop the chat shows, read and kept current: every 4s while it runs, every 10s while it
 *  waits, and not at all once it has ended (it never changes again). The in-chat card and the
 *  loop's detail tile read it the same way, so the two never disagree on what the loop is doing.
 *  `put` takes a loop an action returned, so a press shows at once rather than at the next read. */
export function useWatchedLoop(id: string): { entity: Loop | null; err: string | null; put: (l: Loop) => void } {
  const [entity, setEntity] = useState<Loop | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const gone = useRef(false)  // 404 → entity deleted; stop polling
  const loaded = useRef(false)  // have we ever fetched the entity? (ref, not stale state)
  // A press restarts the clock, so a loop it resumed is read on the live cadence again.
  const [poke, setPoke] = useState(0)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let cancelled = false
    // Reset the per-entity refs when the watched entity changes — they persist across
    // dep changes otherwise (a card reused for a new id would inherit the old entity's
    // gone/loaded state: never polling, or suppressing the new id's first-load error).
    gone.current = false
    loaded.current = false
    const tick = async () => {
      // Cadence is driven by the JUST-FETCHED status, not a closure's state, which is captured
      // at effect-creation (null on the first tick) and never updates inside this closure.
      let st: string | undefined
      try {
        const e = await api.uLoop(id); if (!cancelled) { setEntity(e); setErr(null) } st = e?.status
        loaded.current = true
      } catch (e) {
        const status = (e as { status?: number })?.status
        if (status === 404) { gone.current = true; if (!cancelled) setErr('No longer exists (deleted).'); return }
        // Only show the load error before the FIRST successful fetch: a transient blip on a
        // later poll must not flash an error over an already-rendered card.
        if (!cancelled && !loaded.current) setErr('Could not load progress.')
      }
      if (cancelled || gone.current) return
      // A TERMINAL entity never changes again: stop polling entirely, or an old chat with many
      // finished cards would each keep a perpetual background poll alive.
      if (st && TERMINAL.has(st)) return
      const delay = st && LIVE.has(st) ? 4000 : 10000
      timer = setTimeout(tick, delay)
    }
    void tick()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [id, poke])

  return { entity, err, put: (l: Loop) => { setEntity(l); setPoke((n) => n + 1) } }
}
