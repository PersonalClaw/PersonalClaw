import { useEffect, useRef } from 'react'

export interface WsMessage { type: string; data: Record<string, unknown> }

/** Single multiplexed WebSocket to /api/ws. Reconnects with backoff. Calls
 *  onMessage for every envelope; consumers filter by type + data.session.
 *  `onReconnect` fires when the socket reopens AFTER a drop (not the first
 *  connect) — so consumers can re-sync state missed during the outage.
 *  `onStatus(connected)` reports link state for a UI indicator. */
export function useChatSocket(
  onMessage: (m: WsMessage) => void,
  onReconnect?: () => void,
  onStatus?: (connected: boolean) => void,
) {
  const cb = useRef(onMessage)
  cb.current = onMessage
  const reconnectCb = useRef(onReconnect)
  reconnectCb.current = onReconnect
  const statusCb = useRef(onStatus)
  statusCb.current = onStatus

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let retry = 0
    let everOpened = false
    let timer: number | undefined

    const connect = () => {
      if (closed) return
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/api/ws`)
      ws.onopen = () => {
        retry = 0
        statusCb.current?.(true)
        if (everOpened) reconnectCb.current?.()  // reopened after a drop → catch up
        everOpened = true
      }
      ws.onmessage = (ev) => {
        try { cb.current(JSON.parse(ev.data) as WsMessage) } catch { /* ignore non-JSON */ }
      }
      ws.onclose = () => {
        if (closed) return
        if (everOpened) statusCb.current?.(false)  // only flag drops after a real connection
        retry = Math.min(retry + 1, 6)
        timer = window.setTimeout(connect, 250 * 2 ** retry)
      }
      ws.onerror = () => ws?.close()
    }
    connect()
    return () => { closed = true; if (timer) clearTimeout(timer); ws?.close() }
  }, [])
}
