/** Branch mechanic (CHAT-CRAFT CC-7) — the two pure translations the branch
 *  affordance and the "Branched from" breadcrumb need, kept out of the page so
 *  both are testable without a DOM.
 *
 *  Branch duplicates a timeline; rewind replaces one. This module owns only the
 *  duplicate direction: turning a clicked turn into the coordinate `POST
 *  /api/chat/sessions/{key}/fork` speaks, and turning a child's persisted
 *  `forked_from` back into a link to its origin. */

import type { ChatTurn } from './chatTypes'

/** Resolve the `at_message_index` to branch at for `turns[i]`.
 *
 *  The backend indexes its VISIBLE list — every persisted `user`/`assistant`
 *  message, inclusive — while the rendered `turns` array is a lossy projection of
 *  it: `hydrateTurns` drops native loop re-injections and merges consecutive
 *  assistant messages into one turn. So `i` is NOT the coordinate, and the gap
 *  between them GROWS with each collapse. Using `i` branched a long tool-using
 *  conversation at an earlier message than the one the user clicked.
 *
 *  Hydrated turns carry the real coordinate in `visibleIndex`. Turns appended live
 *  from WS frames don't, so those are derived: walk back to the nearest stamped
 *  turn and add the user/assistant turns since. That is exact for the live tail
 *  (each live turn is one backend message) and degrades to `i` only for a
 *  transcript with no stamp at all — today's behaviour, unchanged. */
export function branchIndexOf(turns: ChatTurn[], i: number): number {
  const turn = turns[i]
  if (!turn) return i
  if (typeof turn.visibleIndex === 'number') return turn.visibleIndex
  for (let j = i - 1; j >= 0; j--) {
    const anchor = turns[j].visibleIndex
    if (typeof anchor !== 'number') continue
    // Count the message-bearing turns strictly between the anchor and i, plus this
    // one. A tool-only assistant turn holds no visible-list slot, so it is skipped.
    let steps = 0
    for (let k = j + 1; k <= i; k++) if (turnHoldsMessage(turns[k])) steps += 1
    return anchor + steps
  }
  return i
}

/** A turn occupies a slot in the backend's visible list only if it carries a
 *  message of its own: a user bubble always does; an assistant turn does once it
 *  has text (a turn made purely of tool cards / a permission row does not — those
 *  persist under their own roles, which the visible filter excludes). */
function turnHoldsMessage(turn: ChatTurn | undefined): boolean {
  if (!turn) return false
  if (turn.role === 'user') return true
  return turn.segments.some((s) => s.kind === 'text')
}

/** The parent's session key from a child's persisted `forked_from`.
 *
 *  `forked_from` stores the parent's HISTORY key (`_history_key_for` — always
 *  `dashboard:<key>` for a dashboard chat), while routes and the `#/chat/<key>`
 *  hash speak the bare key. Strip the namespace; tolerate an already-bare value
 *  (older/hand-written state) rather than producing a key that resolves to
 *  nothing. */
export function branchParentKey(forkedFrom: string | null | undefined): string {
  const raw = (forkedFrom || '').trim()
  if (!raw) return ''
  if (raw.startsWith('dashboard:')) return raw.slice('dashboard:'.length)
  return raw.replace(/^dashboard_+/, '')
}
