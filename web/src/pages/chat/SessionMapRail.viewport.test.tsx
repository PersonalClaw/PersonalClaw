import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapMarks } from './sessionMap'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-5 (the DOM half) — the IntersectionObserver repaints the accent ──────────────────────
//
// done_when: an IntersectionObserver over `turnNodes` repaints visible-turn marks to
// `--color-primary`; changing which turnNodes intersect makes the accented mark set match the
// visible turn set (via `currentMarkRange`'s binary search over `visibleIndex`); and streaming text
// mutations do NOT trigger a re-measure.
//
// VACUITY FLOOR — the trap this atom's audit named explicitly:
//
//  🪤 A TEST THAT ONLY MOUNTS AN UNSCROLLED TRANSCRIPT PASSES WITH ZERO NEW CODE. SSM-4 already
//     painted the newest turn coral from the marks alone, so "the newest turn is accented" is the
//     SHIPPED behaviour and proves nothing about a viewport driver. Every assertion below is
//     therefore about a SCROLLED state, and each is paired with an assertion that the answer
//     DIFFERS from the on-load one.
//  🪤 "AN OBSERVER EXISTS" is satisfiable by one that is constructed and never read. The stub here
//     captures the callback and the test DRIVES it, so the accent is observed to move.
//  🪤 "STREAMING DOES NOT RE-MEASURE" is satisfiable by a rail that never observes anything at all,
//     so the re-observe count is asserted in BOTH directions: unchanged when a preview grows,
//     incremented when a turn is added.

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

/** 🪤 PER-FILE, NEVER IN `test/setup.ts`, AND THIS COST TWO REDS TO LEARN. The obvious move is a
 *  no-op `IntersectionObserver` in the shared setup next to the `ResizeObserver` and `matchMedia`
 *  fixtures. It breaks `pages/artifacts/ArtifactCard.tsx`: that card guards
 *  `typeof IntersectionObserver === 'undefined'` and takes an "everything is near" branch when it is
 *  absent, so jsdom's LACK of the API is what makes its thumbnails render at all — defining a
 *  never-firing stub globally leaves `near` false forever and silently reds two of its assertions.
 *  A capability guard reads a global stub as a real capability. The rail needs no shared fixture
 *  anyway: `useVisibleTurns` returns before constructing an observer when there are no turn nodes,
 *  which is exactly what SSM-4's and SSM-6's tests pass. */
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

/** Six turns; the odd ones are assistant turns carrying two sub-events, so a turn range is never a
 *  mark range of the same size and "the accented set matches the visible turns" has content. */
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
 *  it, so a fresh object literal per render would rebuild the observer for the wrong reason and make
 *  the streaming assertion below measure the test's own churn instead of the rail's. */
const scrollRef = { current: null }

/** A turn-node registry keyed exactly as `ChatPage` keys it (`ChatPage.tsx:2974`). */
function nodesFor(t: ChatTurn[]): Map<number, Element> {
  return new Map(t.map((_, i) => [i, document.createElement('div')]))
}

/** Which marks are coral, in order.
 *
 *  Reads `color`, not `background`: since the Codex redesign the painted element is the LINE inside
 *  the mark's row and it inherits the tone through `currentColor`, so the row carries `color` and a
 *  32×10 pressable row is never filled. `SessionMapRail.test.tsx` owns the tone vocabulary itself;
 *  this file only needs to know which marks are in the current region. */
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

describe('SessionMapRail — the current region follows the viewport (SSM-5)', () => {
  it('observes every turn node, rooted on the transcript scroll container (not the window)', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const scroller = document.createElement('div')
    render(<SessionMapRail marks={sessionMapMarks(t)} turnNodes={nodes} scrollRef={{ current: scroller }} onJumpTo={() => {}} />)
    expect(observers).toHaveLength(1)
    expect(observers[0].observed).toHaveLength(6)
    expect(new Set(observers[0].observed)).toEqual(new Set(nodes.values()))
    // The transcript is a pane with a header above and a composer below — "in the window" and "in
    // the transcript" are different rectangles, so a null root would measure the wrong one.
    expect(observers[0].root).toBe(scroller)
  })

  it('🔑 repaints the accent to the VISIBLE turns — a scrolled state, not the on-load one', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const marks = sessionMapMarks(t)
    const { container } = render(<SessionMapRail marks={marks} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />)

    // On load nothing has been reported: the newest turn is current. This is SSM-4's shipped
    // behaviour, captured so the scrolled assertions below can be shown to DIFFER from it.
    const onLoad = coralOf(container)
    // Turn 5 is an assistant turn: its own mark plus a tool and an error sub-event — three marks
    // sharing one coordinate, which is why the accent is a mark RANGE and not a per-turn flag.
    expect(onLoad.filter(Boolean)).toHaveLength(3)
    expect(onLoad[marks.length - 1]).toBe(true)

    // Scroll up: turns 1 and 2 are now on screen.
    report(nodes, [[1, true], [2, true]])
    const scrolled = coralOf(container)
    expect(scrolled, 'the accent did not move when the viewport did').not.toEqual(onLoad)
    marks.forEach((m, i) => {
      const shouldBeCurrent = m.visibleIndex === 1 || m.visibleIndex === 2
      expect(scrolled[i], `mark ${i} (${m.kind}, turn ${m.visibleIndex})`).toBe(shouldBeCurrent)
    })
    // Turn 1 is an assistant turn (3 marks) + turn 2 a user turn (1 mark) — four, not two, which is
    // what makes this an assertion about MARKS rather than about turns.
    expect(scrolled.filter(Boolean)).toHaveLength(4)
    // And the newest turn is explicitly NO LONGER current, which is the defect SSM-5 exists to fix.
    expect(scrolled[marks.length - 1]).toBe(false)
  })

  it('a turn scrolling OUT of view loses the accent (the callback is a delta, not the truth)', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const marks = sessionMapMarks(t)
    const { container } = render(<SessionMapRail marks={marks} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />)
    report(nodes, [[0, true], [1, true]])
    expect(coralOf(container).filter(Boolean)).toHaveLength(4)   // turn 0 (1) + turn 1 (3)
    // Only turn 0 left the viewport. A rail that rebuilt its set from this one entry would now
    // think NOTHING is on screen.
    report(nodes, [[0, false]])
    const after = coralOf(container)
    marks.forEach((m, i) => expect(after[i], `mark ${i}`).toBe(m.visibleIndex === 1))
    expect(after.filter(Boolean)).toHaveLength(3)
  })

  it('🪤 a streaming text mutation does NOT re-measure', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const { rerender } = render(
      <SessionMapRail marks={sessionMapMarks(t)} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />,
    )
    expect(observers).toHaveLength(1)
    const first = observers[0]

    // Six re-renders with growing preview text — one per streamed chunk. `marks` is a NEW array with
    // NEW mark objects every time, which is exactly why keying the observer on it would rebuild the
    // observer once per token.
    for (const tail of [' a', ' ab', ' abc', ' abcd', ' abcde', ' abcdef']) {
      rerender(<SessionMapRail marks={sessionMapMarks(turns(6, tail))} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />)
    }
    expect(observers, 'the observer was rebuilt while text streamed').toHaveLength(1)
    expect(first.disconnected, 'the observer was torn down while text streamed').toBe(false)
    expect(first.observed, 'the turn nodes were re-observed while text streamed').toHaveLength(6)
  })

  it('…but a NEW TURN does re-measure, so the negative above is not "never observes"', () => {
    const t = turns()
    const nodes = nodesFor(t)
    const { rerender } = render(
      <SessionMapRail marks={sessionMapMarks(t)} turnNodes={nodes} scrollRef={scrollRef} onJumpTo={() => {}} />,
    )
    expect(observers).toHaveLength(1)
    const t2 = turns(7)
    const nodes2 = nodesFor(t2)
    rerender(<SessionMapRail marks={sessionMapMarks(t2)} turnNodes={nodes2} scrollRef={scrollRef} onJumpTo={() => {}} />)
    expect(observers, 'a new turn must be observed — it cannot be on screen otherwise').toHaveLength(2)
    expect(observers[0].disconnected).toBe(true)
    expect(observers[1].observed).toHaveLength(7)
  })
})
