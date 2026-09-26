import { useEffect, useRef } from 'react'

export interface WsMessage { type: string; data: Record<string, unknown> }

/** Subscribe to the tab's ONE WebSocket to /api/ws. Calls `onMessage` for every envelope;
 *  consumers filter by type + data.session. `onReconnect` fires when the socket reopens AFTER
 *  a drop this caller had seen it open before (not its first connect), so the caller can re-sync
 *  state missed during the outage. `onStatus(connected)` reports link state for a UI indicator:
 *  `true` on every open (at once, if the socket is already open when the caller subscribes),
 *  `false` on a drop after that.
 *
 *  Every caller shares one connection. Each call used to open its own, so an idle Home held
 *  eight sockets and a chat six, and the gateway serialised and wrote every broadcast frame
 *  once per socket. It also cost the chat its terminal frames: the chat's socket was its own,
 *  so the session-create remount closed it and the replacement was not listening yet while a
 *  fast turn finished (#3575). The shared socket outlives any one consumer. It opens with the
 *  first subscriber, reconnects with backoff while anyone listens, and closes when the last one
 *  leaves (checked after the current task, so a remount that unsubscribes and resubscribes in
 *  one commit keeps it).
 *
 *  An installed app's events are a different connection by design (`createAppEvents`): the
 *  gateway filters an app-token socket to the app's declared events, and that filter is the
 *  enforcement, so it cannot ride this one. */
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

  useEffect(() => subscribe({ onMessage: cb, onReconnect: reconnectCb, onStatus: statusCb, sawOpen: false }), [])
}

interface Subscriber {
  onMessage: { current: (m: WsMessage) => void }
  onReconnect: { current: (() => void) | undefined }
  onStatus: { current: ((connected: boolean) => void) | undefined }
  /** This subscriber has seen the socket open, so the next open is a REconnect for it. */
  sawOpen: boolean
}

// The tab's socket. Module state, because the point is that there is one per page.
const subscribers = new Set<Subscriber>()
let ws: WebSocket | null = null
let open = false
let retry = 0
let timer: number | undefined

/** Call one consumer. A consumer that throws must not take the frame from the rest. */
function call(fn: () => void): void {
  try { fn() } catch { /* the consumer's own failure; the others still get the frame */ }
}

function connect(): void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const sock = new WebSocket(`${proto}://${location.host}/api/ws`)
  ws = sock
  sock.onopen = () => {
    if (ws !== sock) return
    retry = 0
    open = true
    for (const s of [...subscribers]) {
      call(() => s.onStatus.current?.(true))
      if (s.sawOpen) call(() => s.onReconnect.current?.())
      s.sawOpen = true
    }
  }
  sock.onmessage = (ev) => {
    if (ws !== sock) return
    let m: WsMessage
    try { m = JSON.parse(ev.data) as WsMessage } catch { return }  // ignore non-JSON
    for (const s of [...subscribers]) call(() => s.onMessage.current(m))
  }
  sock.onclose = () => {
    if (ws !== sock) return
    ws = null
    open = false
    // Only flag drops after a real connection, as each caller's own socket did.
    for (const s of [...subscribers]) if (s.sawOpen) call(() => s.onStatus.current?.(false))
    retry = Math.min(retry + 1, 6)
    timer = window.setTimeout(() => { timer = undefined; if (subscribers.size) connect() }, 250 * 2 ** retry)
  }
  sock.onerror = () => sock.close()
}

function subscribe(s: Subscriber): () => void {
  subscribers.add(s)
  if (open) {
    s.sawOpen = true
    call(() => s.onStatus.current?.(true))
  } else if (!ws && timer === undefined) {
    connect()
  }
  return () => {
    subscribers.delete(s)
    queueMicrotask(() => {
      if (subscribers.size) return
      if (timer !== undefined) { window.clearTimeout(timer); timer = undefined }
      const sock = ws
      ws = null
      open = false
      retry = 0
      sock?.close()
    })
  }
}
