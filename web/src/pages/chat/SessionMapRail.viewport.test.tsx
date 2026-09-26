import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-5 (the DOM half) — the IntersectionObserver repaints the on-screen colour ─────────────
//
// done_when: an IntersectionObserver over EVERY turn the map's entries own repaints the markers of
// the exchanges on screen to `--color-primary`; changing which turn nodes intersect makes the coral
// set match the on-screen exchanges (via `currentMarkRange`); a reply on screen keeps its question
// lit after the question itself has scrolled away; and streaming text does NOT trigger a
// re-measure.
//
// VACUITY FLOOR — the traps this atom's audits named:
//
//  🪤 A TEST THAT ONLY MOUNTS AN UNSCROLLED TRANSCRIPT PASSES WITH ZERO NEW CODE. The newest message
//     is coral on load by construction, so every assertion below is about a SCROLLED state, and each
//     is paired with an assertion that the answer DIFFERS from the on-load one.
//  🪤 "AN OBSERVER EXISTS" is satisfiable by one that is constructed and never read. The stub here
//     captures the callback and the test DRIVES it, so the colour is observed to move.
//  🪤 "A REPLY KEEPS ITS QUESTION LIT" is satisfiable by a rail that lights everything. So the
//     reply-only state is asserted to light EXACTLY its question, not its neighbours.
//  🪤 "STREAMING DOES NOT RE-MEASURE" is satisfiable by a rail that never observes anything at all,
//     so the re-observe count is asserted in BOTH directions.

const TS = '2026-09-16T10:00:00.000Z'

interface Captured {
  cb: IntersectionObserverCallback
  root: Element | Document | null
  observed: Element[]
  disconnected: boolean
}
let observers: Captured[] = []

class CapturingObserver {
  private rec: Captured
  constructor(cb: IntersectionObserverCallback, opts?: IntersectionObserverInit) {
    this.rec = { cb, root: opts?.root ?? null, observed: [], disconnected: false }
    observers.push(this.rec)
  }
  observe(el: Element): void { this.rec.observed.push(el) }
  unobserve(): void {}
  disconnect(): void { this.rec.disconnected = true }
  takeRecords(): IntersectionObserverEntry[] { return [] }
}

/** 🪤 PER-FILE, NEVER IN `test/setup.ts`, AND THIS COST TWO REDS TO LEARN. A global no-op
 *  `IntersectionObserver` breaks `pages/artifacts/ArtifactCard.tsx`, which guards
 *  `typeof IntersectionObserver === 'undefined'` and takes an "everything is near" branch when it is
 *  absent — a capability guard reads a global stub as a real capability. The rail needs no shared
 *  fixture: `useVisibleTurns` returns before constructing an observer when there are no turn nodes. */
const original = globalThis.IntersectionObserver
beforeEach(() => {
  observers = []
  globalThis.IntersectionObserver = CapturingObserver as unknown as typeof IntersectionObserver
})
afterEach(() => { globalThis.IntersectionObserver = original })

const subs: Segment[] = [
  { kind: 'tool', id: 't', tool: 'Terminal', detail: 'npm run build', done: true },
  { kind: 'error', text: 'ValidationException: input is too long.' },
]

/** Six turns — three questions (0, 2, 4), each answered by an assistant turn carrying two
 *  sub-events (1, 3, 5) — so three entries owning two coordinates each. */
function turns(n = 6, tail = ''): ChatTurn[] {
  return Array.from({ length: n }, (_, i) => ({
    role: i % 2 === 0 ? 'user' : 'assistant',
    ts: TS,
    visibleIndex: i,
    segments: i % 2 === 0
      ? [{ kind: 'text', text: `prompt ${i}${tail}` } as Segment]
      : [{ kind: 'text', text: `reply ${i}${tail}` } as Segment, ...subs],
  }))
}

/** Stable across rerenders, exactly as `ChatPage`'s `useRef` is — the observer's dep set includes
 *  it, so a fresh object literal per render would rebuild the observer for the wrong reason. */
const scrollRef = { current: null }

/** A turn-node registry keyed exactly as `ChatPage` keys it (`markCoordOf`). */
function nodesFor(t: ChatTurn[]): Map<number, Element> {
  return new Map(t.map((_, i) => [i, document.createElement('div')]))
}

/** Which markers are coral, in order. Reads `color`: the painted element is the LINE inside the
 *  marker's row, which inherits the tone through `currentColor`. */
const coralOf = (c: HTMLElement) =>
  [...c.querySelectorAll('[data-session-mark]')].map((m) => (m as HTMLElement).style.color === 'var(--color-primary)')

/** Report an intersection change to the rail's observer, exactly as the platform would: only the
 *  entries that CHANGED, so a rail that treats a callback as the whole truth fails here. */
function report(nodes: Map<number, Element>, changes: [number, boolean][]) {
  const entries = changes.map(([i, isIntersecting]) => ({
    target: nodes.get(i)!, isIntersecting,
  })) as unknown as IntersectionObserverEntry[]
  act(() => { observers[observers.length - 1].cb(entries, {} as IntersectionObserver) })
}

describe('SessionMapRail — the on-screen colour follows the viewport (SSM-5)', () => {
  it('🔑 observes EVERY turn node — the replies too — rooted on the transcript scroller', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const scroller = document.createElement('div')
    render(<SessionMapRail entries={sessionMapEntries(t)} turnNodes={nodes} scrollRef={{ current: scroller }} onJumpTo={() => {}} />)
    expect(observers).toHaveLength(1)
    // Three markers, six observed nodes: a rail that watched only its own markers' turns could
    // never see a reply on screen, which is the state a reader spends the most time in.
    expect(observers[0].observed).toHaveLength(6)
    expect(new Set(observers[0].observed)).toEqual(new Set(nodes.values()))
    // The transcript is a pane with a header above and a composer below — "in the window" and "in
    // the transcript" are different rectangles, so a null root would measure the wrong one.
    expect(observers[0].root).toBe(scroller)
  })

  it('repaints to the exchanges on screen — a scrolled state, not the on-load one', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const { container } = render(<SessionMapRail entries={sessionMapEntries(t)} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />)
    // On load nothing has been reported: the newest message is current.
    const onLoad = coralOf(container)
    expect(onLoad).toEqual([false, false, true])

    // Scroll up: the first reply (turn 1) and the second question (turn 2) are on screen.
    report(nodes, [[1, true], [2, true]])
    const scrolled = coralOf(container)
    expect(scrolled, 'the colour did not move when the viewport did').not.toEqual(onLoad)
    expect(scrolled).toEqual([true, true, false])
  })

  it('🔑 a reply ALONE on screen keeps its question lit — and only its question', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const { container } = render(<SessionMapRail entries={sessionMapEntries(t)} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />)
    // Deep in the second answer: turn 3 is on screen and its question, turn 2, has scrolled off
    // the top. The previous rule lit a marker only when its OWN turn was on screen, so this state
    // lit nothing — while the reader was reading the exchange it indexes.
    report(nodes, [[3, true]])
    expect(coralOf(container)).toEqual([false, true, false])
  })

  it('an exchange scrolling OUT of view loses its colour (the callback is a delta, not the truth)', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const { container } = render(<SessionMapRail entries={sessionMapEntries(t)} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />)
    report(nodes, [[1, true], [2, true]])
    expect(coralOf(container)).toEqual([true, true, false])
    // Only turn 1 left the viewport. A rail that rebuilt its set from this one entry would now
    // think NOTHING is on screen.
    report(nodes, [[1, false]])
    expect(coralOf(container)).toEqual([false, true, false])
  })

  it('🪤 a streaming text mutation does NOT re-measure', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const { rerender } = render(
      <SessionMapRail entries={sessionMapEntries(t)} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />,
    )
    expect(observers).toHaveLength(1)
    const first = observers[0]

    // Six re-renders with growing text — one per streamed chunk. `entries` is a NEW array with NEW
    // entry objects every time, which is exactly why keying the observer on it would rebuild the
    // observer once per token.
    for (const tail of [' a', ' ab', ' abc', ' abcd', ' abcde', ' abcdef']) {
      rerender(<SessionMapRail entries={sessionMapEntries(turns(6, tail))} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />)
    }
    expect(observers, 'the observer was rebuilt while text streamed').toHaveLength(1)
    expect(first.disconnected, 'the observer was torn down while text streamed').toBe(false)
    expect(first.observed, 'the turn nodes were re-observed while text streamed').toHaveLength(6)
  })

  it('…but a NEW TURN does re-measure — even a reply, which adds no marker', () => {
    // The newest question is still waiting for its answer: five turns, three markers.
    const t = turns(5)
    const nodes = nodesFor(t)
    const { rerender, container } = render(
      <SessionMapRail entries={sessionMapEntries(t)} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />,
    )
    expect(observers).toHaveLength(1)
    expect(observers[0].observed).toHaveLength(5)
    // The answer arrives. It adds NO marker — and it must still be observed, because a reply on
    // screen is what keeps its question lit; a rail keyed on its own markers would miss it.
    const t2 = turns(6)
    const nodes2 = nodesFor(t2)
    rerender(<SessionMapRail entries={sessionMapEntries(t2)} turnNodes={nodes2} scrollRef={scrollRef} onJumpTo={() => {}} />)
    expect(container.querySelectorAll('[data-session-mark]'), 'a reply grew a marker').toHaveLength(3)
    expect(observers, 'the new reply must be observed — it cannot light its question otherwise').toHaveLength(2)
    expect(observers[0].disconnected).toBe(true)
    expect(observers[1].observed).toHaveLength(6)
  })
})
