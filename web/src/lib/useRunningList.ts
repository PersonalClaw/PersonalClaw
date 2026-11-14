import { useCallback, useEffect, useRef } from 'react'
import { api, type RunningList } from './api'
import { useQuery } from './data'
import { useChatSocket, type WsMessage } from './useChatSocket'

/** Home's running read (`GET /api/running`), under Home's own key, so a page that shows a project's
 *  running work reads it once with Home rather than a second time. Read again when the list says it
 *  moved (`running_changed`), a burst of frames read once, and after a reconnect. */
export const RUNNING_NOW_KEY = 'dashboard:running-now'

/** The running list, or undefined for an answer that is not one. */
export function asRunningList(v: unknown): RunningList | undefined {
  const list = v as RunningList | undefined
  return list && Array.isArray(list.running) && Array.isArray(list.finished) ? list : undefined
}

export function useRunningList() {
  const { data, error, loading, refresh } = useQuery<RunningList>(RUNNING_NOW_KEY, async () => api.running())
  const debounce = useRef<number | undefined>(undefined)
  const onMessage = useCallback((m: WsMessage) => {
    if (m.type !== 'running_changed') return
    if (debounce.current) clearTimeout(debounce.current)
    debounce.current = window.setTimeout(refresh, 300)
  }, [refresh])
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current) }, [])
  useChatSocket(onMessage, refresh)
  return { data: asRunningList(data), error, loading, refresh }
}
