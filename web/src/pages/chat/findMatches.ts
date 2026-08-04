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

/** A case-insensitive match as offsets into the ORIGINAL (unfolded) text. */
export interface TextMatch {
  start: number // char offset into the original text
  end: number // exclusive end offset into the original text
}

/** `text` lowercased, plus a per-code-unit map back to the original offsets.
 *
 *  Case folding is NOT length-preserving, so folded offsets are not original
 *  offsets: `İ` (U+0130) lowercases to `i` + U+0307, growing 1 → 2 code units. It
 *  is the only code point in Unicode that grows (and none shrink), but one is
 *  enough — every offset after it drifts, and a drifted offset handed to
 *  `Range.setEnd` throws. Uppercasing is no escape (`ß` → `SS`), so we keep the
 *  map instead of assuming alignment.
 *
 *  `srcStart[i]` / `srcEnd[i]` bound the original character that produced folded
 *  code unit `i`, so any match maps back to whole original characters and can
 *  never point past the end of `text`. Iteration is by code point, so surrogate
 *  pairs stay intact. */
function foldWithMap(text: string): { folded: string; srcStart: number[]; srcEnd: number[] } {
  let folded = ''
  const srcStart: number[] = []
  const srcEnd: number[] = []
  let at = 0
  for (const ch of text) {
    const lower = ch.toLowerCase()
    folded += lower
    for (let i = 0; i < lower.length; i++) { srcStart.push(at); srcEnd.push(at + ch.length) }
    at += ch.length
  }
  return { folded, srcStart, srcEnd }
}

/** Every case-insensitive occurrence of `query` in `text`, left to right, as
 *  offsets into the ORIGINAL `text` — so `text.slice(start, end)` is always the
 *  matched substring. Overlapping matches are NOT emitted (each match advances
 *  past its own end). Empty/whitespace query → no matches.
 *
 *  Shared by the pure scanner below and by FindBar's DOM painter: both need the
 *  identical offsets, and deriving them twice is how they drifted apart (#546). */
export function findInText(text: string, query: string): TextMatch[] {
  if (!query.trim()) return []
  const needle = foldWithMap(query).folded
  const { folded, srcStart, srcEnd } = foldWithMap(text)
  const out: TextMatch[] = []
  let from = 0
  for (;;) {
    const at = folded.indexOf(needle, from)
    if (at < 0) break
    out.push({ start: srcStart[at], end: srcEnd[at + needle.length - 1] })
    from = at + needle.length
  }
  return out
}

/** All matches of `query` across the turns, in reading order (turn, then segment,
 *  then left-to-right within the segment). Empty/whitespace query → no matches.
 *  Overlapping matches are NOT emitted (each match advances past its own end), so
 *  a query of "aa" over "aaaa" yields two matches, matching find-bar convention.
 *  Offsets index the original segment text, never a case-folded copy (#546). */
export function findMatches(turns: ChatTurn[], query: string): FindMatch[] {
  if (!query.trim()) return []
  const matches: FindMatch[] = []
  for (let ti = 0; ti < turns.length; ti++) {
    const segs = findSegments(turns[ti])
    for (let si = 0; si < segs.length; si++) {
      for (const m of findInText(segs[si], query)) {
        matches.push({ turnIndex: ti, segIndex: si, start: m.start, end: m.end })
      }
    }
  }
  return matches
}
