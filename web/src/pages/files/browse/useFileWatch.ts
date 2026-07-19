import { useEffect, useRef } from 'react'
import { api } from '../../../lib/api'

/** Live-watch a file's content via the /api/file-watch SSE stream. Calls
 *  `onChange(content)` whenever the file changes on disk. Disabled when
 *  `enabled` is false or `path` is empty. EventSource carries the auth cookie
 *  through the same-origin dev proxy (no header needed). */
export function useFileWatch(path: string | null, enabled: boolean, onChange: (content: string) => void) {
  const cbRef = useRef(onChange)
  cbRef.current = onChange

  useEffect(() => {
    if (!enabled || !path) return
    let es: EventSource | null = null
    try {
      // resolve=1: a relative chat file-mention watches from the same resolved
      // workspace path fileRead used (else the watch 400s and can't push updates).
      es = new EventSource(api.fileWatchUrl(path, true))
    } catch {
      return
    }
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (typeof data.content === 'string') cbRef.current(data.content)
      } catch { /* ignore malformed frame */ }
    }
    // On error the browser auto-reconnects; nothing to do but keep it quiet.
    es.onerror = () => { /* transient — EventSource retries */ }
    return () => { es?.close() }
  }, [path, enabled])
}
