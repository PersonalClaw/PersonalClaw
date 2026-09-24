import { useEffect, useState } from 'react'
import type { SessionMark } from './sessionMap'

/** SESSION MAP — THE CURRENT REGION (SEMANTIC-SESSION-MAP §A.1/§A.5, atom SSM-5).
 *
 *  SSM-4 shipped the accent PAINT and derived the accented set from the marks alone: the newest
 *  `visibleIndex` won, always. That is exactly right on load and wrong the moment anyone scrolls —
 *  the rail then says "you are at the bottom" while the reader is halfway up the transcript. This
 *  module is the viewport DRIVER that makes the accent mean "what is on screen".
 *
 *  Two pieces, deliberately split so the arithmetic is testable without a DOM:
 *   · `useVisibleTurns` — an `IntersectionObserver` over `ChatPage`'s `turnNodes` registry
 *     (keyed through `sessionMap.ts`'s `markCoordOf`, the same rule the marks use), rooted on
 *     the transcript scroll container (`ChatPage`'s `scrollRef`). It answers ONE question:
 *     which turn coordinates are on screen.
 *   · `currentMarkRange` — pure: which MARKS belong to those coordinates.
 */

/** First index whose mark sits at or after `coord`. */
function lowerBound(marks: SessionMark[], coord: number): number {
  let lo = 0
  let hi = marks.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (marks[mid].visibleIndex < coord) lo = mid + 1
    else hi = mid
  }
  return lo
}

/** First index whose mark sits strictly after `coord`. */
function upperBound(marks: SessionMark[], coord: number): number {
  let lo = 0
  let hi = marks.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (marks[mid].visibleIndex <= coord) lo = mid + 1
    else hi = mid
  }
  return lo
}

/** The INCLUSIVE mark range that is "current" — `[lo, hi]`, and `[0, -1]` for the empty range so
 *  `i >= lo && i <= hi` is simply false for every index rather than needing a null check.
 *
 *  🔑 BINARY SEARCH, NOT A SCAN, AND THAT IS A CONTRACT NOT AN OPTIMISATION. `sessionMapMarks`
 *  emits marks in turn order and gives every sub-event its owning turn's coordinate, and appends
 *  subagent marks carrying the LAST turn's coordinate — so `visibleIndex` is non-decreasing across
 *  the array. That ordering is what lets a range of turns become a contiguous SLICE of marks, which
 *  is in turn why the accent can be a range test instead of a per-mark set membership. A future
 *  mark kind that broke the ordering would break this, so `sessionMapRegion.test.ts` asserts the
 *  ordering directly rather than trusting it.
 *
 *  🪤 THE EMPTY VISIBLE SET IS NOT A PLACEHOLDER BRANCH. Between mount and the observer's first
 *  callback (and in any environment with no layout at all) nothing is reported on screen. The
 *  honest answer there is the newest turn — which is where an unscrolled transcript IS — so this is
 *  the empty case of one total function, not a second mechanism to keep in step with the first.
 */
export function currentMarkRange(marks: SessionMark[], visibleTurns: number[]): [number, number] {
  const EMPTY: [number, number] = [0, -1]
  if (!marks.length) return EMPTY
  const newest = marks[marks.length - 1].visibleIndex
  const first = visibleTurns.length ? Math.min(...visibleTurns) : newest
  const last = visibleTurns.length ? Math.max(...visibleTurns) : newest
  const lo = lowerBound(marks, first)
  const hi = upperBound(marks, last) - 1
  return hi < lo ? EMPTY : [lo, hi]
}

/** Which turn coordinates are currently inside the transcript viewport.
 *
 *  @param turnNodes  `ChatPage`'s live registry, keyed by the same `visibleIndex` a mark carries.
 *  @param scrollRef  the transcript scroll container — the observer ROOT. Not the window: the
 *                    transcript is a pane with a header above it and a composer below, so "in the
 *                    window" and "in the transcript" are different rectangles.
 *  @param coords     the turn coordinates the map currently indexes.
 *
 *  🔑 `coords` IS THE RE-OBSERVE KEY, AND CHOOSING IT IS THE WHOLE DESIGN. `turnNodes` is a
 *  `useRef` Map mutated in place, so its identity never changes and cannot say "a new turn
 *  mounted". The marks array changes on EVERY streamed token (a mark's `preview` grows), so keying
 *  on the marks would tear down and rebuild the observer once per token — which is precisely the
 *  re-measure storm SSM-5's `done_when` forbids. The coordinate LIST is the discriminator that
 *  separates the two events: streaming text cannot change it, and a new turn always does.
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
