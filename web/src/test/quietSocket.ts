import { useEffect, useRef } from 'react'

// `useChatSocket` for a test that sends no frames: the tab's socket is open, and nothing arrives
// on it. A caller hears that it is open as soon as it subscribes, the way the real hook tells a
// caller that subscribes to a socket already open. A mock that tells a caller nothing is a socket
// that never opens, and a chat waits for its socket before it reads (`LISTEN_BEFORE_READ_MS` in
// `pages/chat/useChatStream.ts`), so each chat a test opened under one waited 2 s before it read
// its transcript.

/** Subscribes to an open socket that stays quiet. */
export function useChatSocket(
  _onMessage: unknown,
  _onReconnect: unknown,
  onStatus?: (connected: boolean) => void,
) {
  const status = useRef(onStatus)
  status.current = onStatus
  useEffect(() => { status.current?.(true) }, [])
}
