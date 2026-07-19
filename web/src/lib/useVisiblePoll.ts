import { useEffect, useRef } from 'react'

/** Run `fn` immediately, then every `ms` — but PAUSE while the tab is hidden,
 *  and fire once immediately when it becomes visible again. Backgrounded tabs
 *  shouldn't keep hammering the gateway (battery/CPU/server load); a hidden tab
 *  has no UI to update anyway. `fn` is held in a ref so the interval isn't torn
 *  down on every render when an inline closure is passed.
 *  Pass `ms = null` to disable polling entirely (e.g. only poll while running). */
export function useVisiblePoll(fn: () => void, ms: number | null) {
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    if (ms === null) return  // polling disabled
    let timer: number | undefined
    const tick = () => { if (!document.hidden) fnRef.current() }
    const start = () => {
      stop()
      timer = window.setInterval(tick, ms)
    }
    const stop = () => { if (timer !== undefined) { clearInterval(timer); timer = undefined } }
    const onVisibility = () => {
      if (document.hidden) { stop() }
      else { fnRef.current(); start() }  // catch up immediately, then resume
    }
    fnRef.current()  // initial fetch
    start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility) }
  }, [ms])
}
