/** Find-in-conversation (CHAT-CRAFT S2) — the pure, DOM-free match scanner.
 *
 *  Scans the hydrated turn list for a case-insensitive substring query and
 *  returns every match as a stable coordinate the FindBar can cycle through and
 *  the highlighter can render. Kept pure (no React, no DOM) so it's fast on long
 *  sessions and unit-testable in isolation — the transcript is already fully
 *  hydrated in memory, so matching is an in-memory scan, not a fetch. */

import type { ChatTurn } from './chatTypes'

export interface FindMatch {
  turnIndex: number // index into the turns[] array
  segIndex: number // index into that turn's searchable-segment list (see findSegments)
  start: number // char offset of the match within the segment text
  end: number // exclusive end offset
}

/** The searchable text segments of a turn, in render order: every text segment,
 *  plus each tool card's title (the stable tool name + its one-line detail). The
 *  segIndex in a FindMatch indexes into THIS list, so the FindBar can resolve a
 *  match back to what to highlight without re-deriving the mapping. */
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

/** All matches of `query` across the turns, in reading order (turn, then segment,
 *  then left-to-right within the segment). Empty/whitespace query → no matches.
 *  Overlapping matches are NOT emitted (each match advances past its own end), so
 *  a query of "aa" over "aaaa" yields two matches, matching find-bar convention. */
export function findMatches(turns: ChatTurn[], query: string): FindMatch[] {
  const q = query.toLowerCase()
  if (!q.trim()) return []
  const matches: FindMatch[] = []
  for (let ti = 0; ti < turns.length; ti++) {
    const segs = findSegments(turns[ti])
    for (let si = 0; si < segs.length; si++) {
      const hay = segs[si].toLowerCase()
      let from = 0
      for (;;) {
        const at = hay.indexOf(q, from)
        if (at < 0) break
        matches.push({ turnIndex: ti, segIndex: si, start: at, end: at + q.length })
        from = at + q.length
      }
    }
  }
  return matches
}
