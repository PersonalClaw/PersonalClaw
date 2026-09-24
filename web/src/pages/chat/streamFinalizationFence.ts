import type { HistMsg } from './chatTypes'

function sameMessageIdentity(left: HistMsg, right: HistMsg): boolean {
  if (left.role !== right.role) return false
  if (left.ts || right.ts) return !!left.ts && left.ts === right.ts
  return left.content === right.content
}

function sameUserTurn(left: HistMsg, right: HistMsg): boolean {
  return (
    left.role === 'user'
    && right.role === 'user'
    && sameMessageIdentity(left, right)
    && left.content === right.content
    && left.variant_idx === right.variant_idx
    && JSON.stringify(left.variants ?? []) === JSON.stringify(right.variants ?? [])
  )
}

function refreshFinalizedSeedTail(seed: HistMsg[], refreshed: HistMsg[]): boolean {
  const latestUser = seed.at(-1)
  if (!latestUser || latestUser.role !== 'user' || refreshed.length <= seed.length) return false

  for (let i = 0; i < seed.length; i++) {
    if (!sameMessageIdentity(seed[i], refreshed[i])) return false
  }
  if (!sameUserTurn(latestUser, refreshed[seed.length - 1])) return false

  const appended = refreshed.slice(seed.length)
  return !appended.some((message) => message.role === 'user')
    && appended.some((message) => message.role === 'assistant')
}

/**
 * Prevent a first-send session refresh from rendering the same answer through
 * both transports.
 *
 * A newly created chat remounts with an optimistic cache containing the user
 * message. Its session-detail request can then return the finalized assistant
 * message before the WebSocket's terminal text frames reach the browser. Once
 * that happens, persisted history is authoritative for the text of this turn;
 * later chat_chunk frames are transport replay until chat_done closes the turn.
 * The refresh must be a strict extension of the same cached user-tail snapshot.
 * A stale cache being backfilled earlier in history must never fence the current
 * turn's stream.
 *
 * Only text chunks are fenced. Tool, approval, activity, and thinking frames
 * still refine their existing segments, so a multi-segment answer keeps its
 * live cards and ordering.
 */
export class StreamFinalizationFence {
  private suppressText = false

  armFromRefresh(seed: HistMsg[] | null, refreshed: HistMsg[]): boolean {
    if (!seed || !refreshFinalizedSeedTail(seed, refreshed)) return false
    this.suppressText = true
    return true
  }

  allows(frameType: string): boolean {
    return frameType !== 'chat_chunk' || !this.suppressText
  }

  startTurn(): void {
    this.suppressText = false
  }

  finishTurn(): void {
    this.suppressText = false
  }
}
