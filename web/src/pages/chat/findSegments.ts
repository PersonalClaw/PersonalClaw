/** Find-in-conversation — the CHAT-SHAPED half of search: what part of a turn a user
 *  can actually search.
 *
 *  Everything surface-neutral (case folding, offsets, which items match, the ordering)
 *  lives in `ui/findText.ts` behind `ui/FindBar`, the shared primitive. This file is
 *  what is left once that is subtracted, and it is the whole of what chat has to teach
 *  the bar: a `ChatTurn` is not one string, it is a render-ordered list of segments,
 *  and two of its five kinds are not text at all. */

import type { ChatTurn } from './chatTypes'

/** The searchable text segments of a turn, in render order: every text segment,
 *  plus each tool card's title (the stable tool name + its one-line detail).
 *
 *  Segments are kept APART rather than joined, because a match must not span two of
 *  them — a query that straddles the seam between a tool title and the prose under it
 *  would highlight nothing findable on screen. `ui/findText`'s `matchingIndices`
 *  relies on that: it is handed this list, not a concatenation. */
export function findSegments(turn: ChatTurn): string[] {
  const out: string[] = []
  for (const seg of turn.segments) {
    if (seg.kind === 'text') out.push(seg.text)
    else if (seg.kind === 'tool') out.push([seg.tool, seg.detail].filter(Boolean).join(' '))
    else if (seg.kind === 'activity') out.push(seg.text)
    else if (seg.kind === 'error') out.push(seg.text)
    else out.push('') // approval — nothing user-searchable, keep index alignment
  }
  return out
}
