import { useEffect, useState } from 'react'

/** SESSION MAP — THE CURRENT REGION (SEMANTIC-SESSION-MAP §A.1/§A.5, atom SSM-5).
 *
 *  SSM-4 shipped the accent PAINT and derived the accented set from the marks alone: the newest
 *  `visibleIndex` won, always. That is exactly right on load and wrong the moment anyone scrolls —
 *  the rail then says "you are at the bottom" while the reader is halfway up the transcript. This
 *  module is the viewport DRIVER that makes the accent mean "what is on screen".
 *
 *  Two pieces, deliberately split so the arithmetic is testable without a DOM:
 *   · `useVisibleTurns` — an `IntersectionObserver` over `ChatPage`'s `turnNodes` registry
 *     (keyed through `chatTypes.ts`'s `markCoordOf`, the same rule the entries use), rooted on
 *     the transcript scroll container (`ChatPage`'s `scrollRef`). It answers ONE question:
 *     which turn coordinates are on screen.
 *   · `currentMarkRange` — pure: which ENTRIES those coordinates belong to.
 */

/** Anything the range arithmetic reads: an entry, keyed by the coordinate its exchange starts at. */
interface Starts { visibleIndex: number }

/** First index whose entry starts strictly after `coord`. */
function upperBound(entries: ReadonlyArray<Starts>, coord: number): number {
  let lo = 0
  let hi = entries.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (entries[mid].visibleIndex <= coord) lo = mid + 1
    else hi = mid
  }
  return lo
}

/** The INCLUSIVE entry range that is "current" — `[lo, hi]`, and `[0, -1]` for the empty range so
 *  `i >= lo && i <= hi` is simply false for every index rather than needing a null check.
 *
 *  🔑 A COORDINATE BELONGS TO THE LAST ENTRY THAT STARTS AT OR BEFORE IT. An entry is a user
 *  message and the exchange it opened (`sessionMapEntries`), so it owns every turn from its own
 *  message up to the next entry's. That is the whole of "reading a long answer keeps its question
 *  lit": the answer's turns are on screen, the question is not, and the answer's coordinate
 *  resolves to the question's entry. The previous rule (an entry is current only if its OWN
 *  coordinate is on screen) went dark in exactly that state, which is the one a reader spends the
 *  most time in.
 *
 *  🔑 BINARY SEARCH, NOT A SCAN, AND THAT IS A CONTRACT NOT AN OPTIMISATION. Entries are emitted in
 *  turn order, so `visibleIndex` is strictly increasing across the array; that ordering is what lets
 *  a range of turns become a contiguous SLICE of entries, which is in turn why the accent can be a
 *  range test instead of a per-entry set membership. `sessionMapRegion.test.ts` asserts the ordering
 *  directly rather than trusting it.
 *
 *  🪤 THE EMPTY VISIBLE SET IS NOT A PLACEHOLDER BRANCH. Between mount and the observer's first
 *  callback (and in any environment with no layout at all) nothing is reported on screen. The
 *  honest answer there is the newest entry — which is where an unscrolled transcript IS — so this is
 *  the empty case of one total function, not a second mechanism to keep in step with the first.
 */
export function currentMarkRange(entries: ReadonlyArray<Starts>, visibleTurns: number[]): [number, number] {
  const EMPTY: [number, number] = [0, -1]
  if (!entries.length) return EMPTY
  if (!visibleTurns.length) return [entries.length - 1, entries.length - 1]
  const owner = (coord: number) => upperBound(entries, coord) - 1
  const hi = owner(Math.max(...visibleTurns))
  // Only agent output from before the first user message is on screen: no entry owns it.
  if (hi < 0) return EMPTY
  return [Math.max(0, owner(Math.min(...visibleTurns))), hi]
}

/** Which turn coordinates are currently inside the transcript viewport.
 *
 *  @param turnNodes  `ChatPage`'s live registry, keyed by the same coordinate an entry carries.
 *  @param scrollRef  the transcript scroll container — the observer ROOT. Not the window: the
 *                    transcript is a pane with a header above it and a composer below, so "in the
 *                    window" and "in the transcript" are different rectangles.
 *  @param coords     every turn coordinate the map's entries own — replies included, which is what
 *                    lets an answer on screen light its question.
 *
 *  🔑 `coords` IS THE RE-OBSERVE KEY, AND CHOOSING IT IS THE WHOLE DESIGN. `turnNodes` is a
 *  `useRef` Map mutated in place, so its identity never changes and cannot say "a new turn
 *  mounted". The entries array changes on EVERY streamed token (a reply's `response` grows), so
 *  keying on the entries would tear down and rebuild the observer once per token — which is
 *  precisely the re-measure storm SSM-5's `done_when` forbids. The coordinate LIST is the
 *  discriminator that separates the two events: streaming text cannot change it, and a new turn
 *  always does.
 */
export function useVisibleTurns(
  turnNodes: ReadonlyMap<number, Element>,
  scrollRef: { current: Element | null },
  coords: number[],
): number[] {
  const [visible, setVisible] = useState<number[]>([])
  const key = coords.join(',')

  useEffect(() => {
    const ids = key ? key.split(',').map(Number) : []
    const observed = new Map<Element, number>()
    for (const id of ids) {
      const el = turnNodes.get(id)
      if (el) observed.set(el, id)
    }
    if (!observed.size) return
    // Accumulated across callbacks: an IntersectionObserver reports only what CHANGED, so a
    // callback carrying one entry is not a statement about the other turns.
    const onScreen = new Set<number>()
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const id = observed.get(entry.target)
        if (id === undefined) continue
        if (entry.isIntersecting) onScreen.add(id)
        else onScreen.delete(id)
      }
      setVisible([...onScreen].sort((a, b) => a - b))
    }, { root: scrollRef.current })
    for (const el of observed.keys()) io.observe(el)
    return () => io.disconnect()
  }, [key, turnNodes, scrollRef])

  return visible
}
