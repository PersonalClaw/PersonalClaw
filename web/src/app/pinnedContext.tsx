import { useSyncExternalStore } from 'react'
import { contextLineSaid, type ContextReading } from '../ui/composer/ContextDisclosure'

/** The context the owner pinned to the status line (#3823's approved update: the thin status line
 *  carries only connection, sync and the context the owner pinned; cost and context stay collapsed
 *  otherwise).
 *
 *  Two halves, kept apart on purpose:
 *  - **The pin** is the owner's, for this browser: `localStorage`, so it holds across reloads and
 *    every tab, set from the composer's Context control.
 *  - **The reading** is the open chat's, published by its composer while it is mounted and cleared
 *    when it goes. The line shows only that: no chat open, nothing shown; a chat whose context the
 *    gateway has not measured says so. It never shows a reading kept from an earlier chat or an
 *    earlier load as if it were current. */

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

/** The status line's right slot: the open chat's reading, when the owner pinned it. */
export function PinnedContextReading() {
  const pinned = useContextPinned()
  const current = useSyncExternalStore(subscribe, () => reading, () => null)
  if (!pinned || !current) return null
  const { line, sentence } = contextLineSaid(current)
  return <span className="min-w-0 truncate tabular-nums" title={sentence}>{line}</span>
}
