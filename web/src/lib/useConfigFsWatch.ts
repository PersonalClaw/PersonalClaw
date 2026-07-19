import { useEffect, useRef } from 'react'
import { api } from './api'

/** Live-watch the config trees (config.json, agents/, skills/, workflows/) for
 *  out-of-band edits via the /api/config-fs/stream SSE feed. Calls `onChange(path)`
 *  whenever a watched file changes on disk — so a page editing that state can
 *  refresh instead of showing a stale view (filesystem-as-truth, #44).
 *
 *  EventSource carries the auth cookie through the same-origin proxy. Disabled
 *  when `enabled` is false. The backend emits named `changed` events. */
export function useConfigFsWatch(enabled: boolean, onChange: (path: string) => void) {
  const cbRef = useRef(onChange)
  cbRef.current = onChange

  useEffect(() => {
    if (!enabled) return
    let es: EventSource | null = null
    try {
      es = new EventSource(api.configFsStreamUrl())
    } catch {
      return
    }
    es.addEventListener('changed', (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        if (typeof data.path === 'string') cbRef.current(data.path)
      } catch { /* ignore malformed frame */ }
    })
    es.onerror = () => { /* transient — EventSource auto-reconnects */ }
    return () => { es?.close() }
  }, [enabled])
}
