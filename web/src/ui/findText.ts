/** Case-insensitive substring matching for `ui/FindBar` — pure, DOM-free, and
 *  surface-neutral.
 *
 *  Lifted out of `pages/chat/findMatches.ts` when the find bar was promoted to a
 *  shared primitive (KL-16): the folding rules below are properties of TEXT, not of
 *  a chat transcript, and a `ui/` component may not reach into `pages/`. What stayed
 *  behind in chat is the one genuinely chat-shaped function — `findSegments`, which
 *  knows what part of a ChatTurn a user can search.
 *
 *  Both the counter and the painter go through here, which is the point: they need
 *  the IDENTICAL offsets, and deriving them twice is how they drifted apart (#546). */

/** A case-insensitive match as offsets into the ORIGINAL (unfolded) text. */
export interface TextMatch {
  start: number // char offset into the original text
  end: number // exclusive end offset into the original text
}

/** Case-fold `text` one code point at a time.
 *
 *  🪤 NOT `text.toLowerCase()`. Whole-string lowercasing is CONTEXT-SENSITIVE: Greek
 *  `'ΟΔΟΣ'.toLowerCase()` is `'οδος'` (final sigma), while folding per code point
 *  gives `'οδοσ'`. So a query folded one way and a haystack folded the other stop
 *  matching each other — and the two would have been folded differently the moment
 *  `hasMatch` took the cheap whole-string route while `findInText` kept the per-code-
 *  point one. Every folder in this module uses THIS function so that cannot happen;
 *  `findTextEquivalence` in the test pins it with the sigma case.
 *
 *  Iteration is by code point (`for…of`), so surrogate pairs stay intact. */
function fold(text: string): string {
  let out = ''
  for (const ch of text) out += ch.toLowerCase()
  return out
}

/** `text` folded, plus a per-code-unit map back to the original offsets.
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
 *  never point past the end of `text`. */
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
 *  past its own end). Empty/whitespace query → no matches. */
export function findInText(text: string, query: string): TextMatch[] {
  if (!query.trim()) return []
  const needle = fold(query)
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

/** Whether `text` contains `query`, case-insensitively — exactly
 *  `findInText(text, query).length > 0`, without building the offset map or the
 *  match list.
 *
 *  This is the question the find bar's COUNTER asks (does this item match?), once
 *  per searchable segment per keystroke; only the painter needs offsets. The chat
 *  version collected every occurrence in the whole transcript just to count the
 *  turns that had one. */
export function hasMatch(text: string, query: string): boolean {
  if (!query.trim()) return false
  return fold(text).includes(fold(query))
}

/** The indices of `items` that contain at least one match, in order — the find bar's
 *  scroll-target set and its counter total.
 *
 *  ONE index per matching item, however many occurrences it holds: these are the stops
 *  ↑/↓ can actually make, so counting occurrences would promise the user positions the
 *  arrows cannot reach. `segmentsOf` decides what is searchable AND where the seams
 *  are — a match never spans two segments, so a caller keeps a heading apart from its
 *  body rather than joining them with a space and inventing a match across the join.
 *
 *  Empty/whitespace query → no indices (the bar shows no counter at all, not `0/0`). */
export function matchingIndices<T>(
  items: readonly T[],
  segmentsOf: (item: T) => string[],
  query: string,
): number[] {
  if (!query.trim()) return []
  const out: number[] = []
  for (let i = 0; i < items.length; i++) {
    if (segmentsOf(items[i]).some((seg) => hasMatch(seg, query))) out.push(i)
  }
  return out
}
