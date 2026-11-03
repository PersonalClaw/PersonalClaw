import { useSyncExternalStore } from 'react'

// Whether the update overlay's sheet is open, for a shell notice that waits until it closes. The
// interface-updated notice (`app/UpdatedInterfaceNotice`) comes with the reconnect that ends a
// restart, while the sheet still says "Restart complete" over a scrim; shown then, it would sit
// under the scrim and be announced over the sheet.

let open = false
const listeners = new Set<() => void>()

/** Set by `UpdateProgressOverlay` as its sheet opens and closes. */
export function setUpdateSheetOpen(next: boolean): void {
  if (next === open) return
  open = next
  for (const listener of [...listeners]) listener()
}

export function useUpdateSheetOpen(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    () => open,
  )
}
