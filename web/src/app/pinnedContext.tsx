import { useState, useSyncExternalStore } from 'react'
import { useChatSocket } from '../lib/useChatSocket'
import { contextLineSaid, type ContextReading } from '../ui/composer/ContextDisclosure'
import { useGatewayConnection } from './gatewayConnection'

/** The context the owner pinned to the status line (#3823's approved update: the thin status line
 *  carries only connection, sync and the context the owner pinned; cost and context stay collapsed
 *  otherwise).
 *
 *  Two halves, kept apart on purpose:
 *  - **The pin** is the owner's, for this browser: `localStorage`, so it holds across reloads and
 *    every tab, set from the composer's Context control.
 *  - **The reading** is the open chat's, published by its composer while it is mounted and cleared
 *    when it goes. The line shows only that: no chat open, nothing shown; a chat whose context the
 *    gateway has not measured says so. It never shows a reading kept from an earlier chat, an
 *    earlier load, or from before a dropped link as if it were current. */

const PIN_KEY = 'pc:status:context-pinned'

let reading: ContextReading | null = null
const listeners = new Set<() => void>()
const emit = () => { for (const l of listeners) l() }

function subscribe(l: () => void): () => void {
  listeners.add(l)
  // Another tab moving the pin moves it here too.
  const onStorage = (e: StorageEvent) => { if (e.key === PIN_KEY) l() }
  window.addEventListener('storage', onStorage)
  return () => { listeners.delete(l); window.removeEventListener('storage', onStorage) }
}

function readPinned(): boolean {
  try { return localStorage.getItem(PIN_KEY) === '1' } catch { return false }
}

/** Whether the owner pinned the context to the status line, live. */
export function useContextPinned(): boolean {
  return useSyncExternalStore(subscribe, readPinned, () => false)
}

/** Pin or unpin the context to the status line, for this browser. */
export function setContextPinned(pinned: boolean): void {
  try {
    if (pinned) localStorage.setItem(PIN_KEY, '1')
    else localStorage.removeItem(PIN_KEY)
  } catch { /* private mode: the pin lasts this page only, through `emit` */ }
  emit()
}

/** The open chat's composer says what its context reads, or `null` as it goes. */
export function publishContextReading(next: ContextReading | null): void {
  reading = next
  emit()
}

/** Whether the tab's live-update link is open: `null` before it first opens, then `true` while it
 *  is and `false` from a drop until it reopens (`useChatSocket`'s status). */
function useLiveLink(): boolean | null {
  const [open, setOpen] = useState<boolean | null>(null)
  useChatSocket(ignoreFrame, null, setOpen)
  return open
}
const ignoreFrame = () => {}

/** The status line's right slot (#3823: the line is ambient: the connection, sync, and the context
 *  the owner pinned).
 *
 *  - **Sync**, the tab's live-update link, is quiet while it is open. From a drop until it reopens
 *    it says so, unless the gateway itself is lost, which the left slot already says.
 *  - **The pinned context** shows the open chat's reading while the link is open. While it is down
 *    a reading may have been missed (a compaction's), so the line says the link is reconnecting
 *    instead of showing the last reading as current; once it is back the chat reads itself again
 *    and publishes what the snapshot says. */
export function PinnedContextReading() {
  const pinned = useContextPinned()
  const current = useSyncExternalStore(subscribe, () => reading, () => null)
  const link = useLiveLink()
  const { state } = useGatewayConnection()
  if (link === false) {
    return state === 'disconnected' ? null : <span className="min-w-0 truncate">Reconnecting live updates…</span>
  }
  if (!pinned || !current) return null
  const { line, sentence } = contextLineSaid(current)
  return <span className="min-w-0 truncate tabular-nums" title={sentence}>{line}</span>
}
