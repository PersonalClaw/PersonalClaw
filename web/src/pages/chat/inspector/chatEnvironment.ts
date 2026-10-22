import { useSyncExternalStore } from 'react'

// ── Where the open chat's next turn may act, for its inspector ─────────────────────────────────
//
// The composer's Execution Boundary control is the one authority over where a chat's turn may act
// (#3823): the permission mode, the working folder, the sandbox, the network reach and the memory
// mode. The session inspector's Environment section shows the same reading and changes nothing
// itself: its Change opens that control. So the composer publishes what its control shows while
// it is mounted (and clears it as it goes), and the inspector asks the control to open.

export interface ChatEnvironment {
  /** The chat's project id, `''` for none. */
  projectId: string
  /** The working folder in words: a path, or "the workspace". */
  folder: string
  permission: string
  memory: string
  sandbox: { label: string; said: string }
  reach: { label: string; said: string }
}

let current: ChatEnvironment | null = null
let boundaryAsks = 0
const listeners = new Set<() => void>()
const emit = () => { for (const l of listeners) l() }
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

/** The composer says what its Execution Boundary shows, or `null` as it goes. */
export function publishChatEnvironment(next: ChatEnvironment | null): void {
  current = next
  emit()
}

/** The open chat's environment, or `null` while no composer has said it. */
export function useChatEnvironment(): ChatEnvironment | null {
  return useSyncExternalStore(subscribe, () => current, () => null)
}

/** Open the composer's Execution Boundary control, where the environment is changed. */
export function openTheBoundary(): void {
  boundaryAsks += 1
  emit()
}

/** A count that goes up each time something asks for the Execution Boundary control (a
 *  `Popover`'s `openSignal`). */
export function useBoundaryAsks(): number {
  return useSyncExternalStore(subscribe, () => boundaryAsks, () => 0)
}
