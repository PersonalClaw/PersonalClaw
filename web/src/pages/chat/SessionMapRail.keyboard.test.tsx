import { beforeAll, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import { sessionMapMarkName } from './SessionMapCard'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-7 — click + keyboard jump, roving tabindex ───────────────────────────────────────────
//
// done_when: ArrowUp/Down move the selection with ONE tab stop; Home/End jump to the ends;
// Enter/Space call `onJumpTo(entry.visibleIndex)` and that scrolls the matching turnNode; Escape
// closes the card; the focused `aria-label` is the short "Message X of N: …" (never the full text);
// `aria-live` announces ONLY while focused.
//
// VACUITY FLOOR:
//
//  1. "ARROWS MOVE THE SELECTION" is satisfiable by a rail that moves a data attribute nothing can
//     see. The move is asserted through `document.activeElement` AND the roving `tabIndex` together,
//     because either alone can drift from the other (a focused element with tabIndex -1 is a
//     keyboard trap the next Tab escapes from the wrong place).
//  2. "ONE TAB STOP" is satisfiable by a rail with one focusable marker. Asserted as an invariant that
//     survives every move: exactly one `tabIndex === 0`, and it is the focused one.
//  3. "Enter CALLS onJumpTo" is satisfiable while the transcript never moves, and satisfiable TWICE
//     (the browser also synthesises a click from Enter on a `<button>`). So the spy is asserted to
//     fire EXACTLY once, and wired to a `jumpToTurn`-shaped scroller over the same `turnNodes` map
//     `ChatPage` owns — the matching node's `scrollIntoView` runs and no other node's does.
//  4. "aria-live ANNOUNCES" is satisfiable by a region that is never empty, which is the double-speak
//     §A.6 forbids. Asserted in three states: empty at rest, filled on the jump, empty again once
//     focus leaves the rail.

/** A no-op IntersectionObserver, LOCAL to this file. jsdom ships none, and this harness hands the
 *  rail real turn nodes (the keyboard cursor must be tested against a real current region), so
 *  `useVisibleTurns` constructs one. It must NOT go in `test/setup.ts`: a global stub defeats
 *  `pages/artifacts/ArtifactCard.tsx`'s `typeof IntersectionObserver === 'undefined'` capability
 *  guard, which relies on jsdom's absence of the API to take its "everything is near" branch — see
 *  the note in `SessionMapRail.viewport.test.tsx`. Never firing is the right default here: nothing is
 *  reported on screen, so the region is the newest turn, i.e. the unscrolled state. */
beforeAll(() => {
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    globalThis.IntersectionObserver = class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    } as unknown as typeof IntersectionObserver
  }
})

const TS = '2026-09-16T10:00:00.000Z'
const subs: Segment[] = [
  { kind: 'tool', id: 't', tool: 'Terminal', detail: 'npm run build', done: true },
  { kind: 'error', text: 'ValidationException: input is too long.' },
]

/** `n` turns alternating question / answer, each answer busy with a tool call and an error — so
 *  every marker's index differs from its jump coordinate (marker k is turn 2k). */
function turns(n = 12): ChatTurn[] {
  return Array.from({ length: n }, (_, i) => ({
    role: i % 2 === 0 ? 'user' : 'assistant',
    ts: TS,
    visibleIndex: i,
    segments: i % 2 === 0
      ? [{ kind: 'text', text: `prompt ${i}` } as Segment]
      : [{ kind: 'text', text: `reply ${i}` } as Segment, ...subs],
  }))
}

interface Harness {
  container: HTMLElement
  entries: ReturnType<typeof sessionMapEntries>
  ticks: HTMLButtonElement[]
  nodes: Map<number, Element>
  scrolled: number[]
  jumped: ReturnType<typeof vi.fn>
  live: () => string
}

function mount(n = 12): Harness {
  const t = turns(n)
  const entries = sessionMapEntries(t)
  const nodes = new Map<number, Element>()
  const scrolled: number[] = []
  t.forEach((_, i) => {
    const el = document.createElement('div')
    // The node records the scroll so "the MATCHING turnNode" is falsifiable, not assumed.
    ;(el as unknown as { scrollIntoView: () => void }).scrollIntoView = () => { scrolled.push(i) }
    nodes.set(i, el)
  })
  // `ChatPage.tsx:2219`'s jumpToTurn, verbatim in shape: look the coordinate up in turnNodes and
  // scroll that node. The rail owns no scroll machinery, so this is the seam under test.
  const jumped = vi.fn((visibleIndex: number) => {
    ;(nodes.get(visibleIndex) as unknown as { scrollIntoView?: () => void } | undefined)?.scrollIntoView?.()
  })
  const { container } = render(
    <SessionMapRail entries={entries} turnNodes={nodes} scrollRef={{ current: null }} onJumpTo={jumped} />,
  )
  return {
    container,
    entries,
    ticks: [...container.querySelectorAll('[data-session-mark]')] as HTMLButtonElement[],
    nodes,
    scrolled,
    jumped,
    live: () => container.querySelector('[data-session-map-live]')!.textContent ?? '',
  }
}

/** The rail's single tab stop, asserted to BE single every time it is read. */
function tabStop(h: Harness): number {
  const stops = h.ticks.map((el, i) => [el.tabIndex, i] as const).filter(([t]) => t === 0)
  expect(stops, `the rail has ${stops.length} tab stops`).toHaveLength(1)
  return stops[0][1]
}

const key = (h: Harness, k: string) => act(() => { fireEvent.keyDown(h.ticks[tabStop(h)], { key: k }) })

describe('SessionMapRail — the roving cursor (SSM-7)', () => {
  it('starts its one tab stop on the current region', () => {
    const h = mount()
    // Nothing reported on screen → the newest message is current (SSM-5's empty case), so the slot
    // sits on the LAST marker — and it is the lit one, not merely the last one.
    expect(tabStop(h)).toBe(h.entries.length - 1)
    expect(h.ticks[tabStop(h)].getAttribute('data-current')).toBe('true')
    expect(h.ticks.filter((el) => el.getAttribute('data-current') === 'true')).toHaveLength(1)
  })

  it('ArrowUp/ArrowDown step the cursor, moving BOTH focus and the tab stop', () => {
    const h = mount()
    const start = tabStop(h)
    key(h, 'ArrowUp')
    expect(tabStop(h)).toBe(start - 1)
    expect(document.activeElement, 'the cursor moved without taking focus with it').toBe(h.ticks[start - 1])
    key(h, 'ArrowUp')
    expect(tabStop(h)).toBe(start - 2)
    expect(document.activeElement).toBe(h.ticks[start - 2])
    key(h, 'ArrowDown')
    expect(tabStop(h)).toBe(start - 1)
    expect(document.activeElement).toBe(h.ticks[start - 1])
  })

  it('clamps at the ends instead of wrapping or walking off the array', () => {
    const h = mount()
    for (let i = 0; i < h.entries.length + 4; i++) key(h, 'ArrowUp')
    expect(tabStop(h)).toBe(0)
    expect(document.activeElement).toBe(h.ticks[0])
    for (let i = 0; i < h.entries.length + 4; i++) key(h, 'ArrowDown')
    expect(tabStop(h)).toBe(h.entries.length - 1)
  })

  it('Home/End jump to the ends, and PageUp/PageDown step further than an arrow', () => {
    const h = mount()
    key(h, 'Home')
    expect(tabStop(h)).toBe(0)
    key(h, 'End')
    expect(tabStop(h)).toBe(h.entries.length - 1)
    key(h, 'PageUp')
    const paged = tabStop(h)
    expect(h.entries.length - 1 - paged, 'a page must be more than one marker').toBeGreaterThan(1)
    key(h, 'PageDown')
    expect(tabStop(h)).toBe(h.entries.length - 1)
  })

  it('the arrows do not let the browser scroll the transcript out from under the cursor', () => {
    const h = mount()
    const ev = new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true })
    act(() => { h.ticks[tabStop(h)].dispatchEvent(ev) })
    expect(ev.defaultPrevented).toBe(true)
  })
})

describe('SessionMapRail — Enter/Space/click jump to the message (SSM-7)', () => {
  it('🔑 Enter calls onJumpTo(visibleIndex) EXACTLY once and scrolls the MATCHING turnNode', () => {
    const h = mount()
    key(h, 'Home')                      // cursor on marker 0 — the first question, turn 0
    key(h, 'ArrowDown')                 // marker 1 — the second question, turn 2
    const at = tabStop(h)
    key(h, 'Enter')
    expect(h.jumped, 'Enter fired twice — the browser’s own button activation was not cancelled')
      .toHaveBeenCalledTimes(1)
    expect(h.jumped).toHaveBeenCalledWith(h.entries[at].visibleIndex)
    expect(h.scrolled).toEqual([h.entries[at].visibleIndex])
    // The jump coordinate is the MESSAGE's, not the marker's: marker 1 is turn 2, so a rail that
    // handed back its own index would land on the first question's answer.
    expect(h.entries[at].visibleIndex).not.toBe(at)
  })

  it('Space jumps too, to the same coordinate Enter would', () => {
    const h = mount()
    key(h, 'End')
    key(h, 'ArrowUp')
    const at = tabStop(h)
    key(h, ' ')
    expect(h.jumped).toHaveBeenCalledTimes(1)
    expect(h.jumped).toHaveBeenCalledWith(h.entries[at].visibleIndex)
    expect(h.scrolled).toEqual([h.entries[at].visibleIndex])
    expect(at).not.toBe(h.entries[at].visibleIndex)
  })

  it('a pointer click jumps to the clicked marker — the same handler, not a second one', () => {
    const h = mount()
    act(() => { fireEvent.click(h.ticks[3]) })
    expect(h.jumped).toHaveBeenCalledTimes(1)
    expect(h.jumped).toHaveBeenCalledWith(h.entries[3].visibleIndex)
    expect(h.scrolled).toEqual([h.entries[3].visibleIndex])
  })

  it('an unhandled key is left entirely alone', () => {
    const h = mount()
    const before = tabStop(h)
    const ev = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true })
    act(() => { h.ticks[before].dispatchEvent(ev) })
    expect(ev.defaultPrevented, 'the rail swallowed a key it does not handle').toBe(false)
    expect(tabStop(h)).toBe(before)
    expect(h.jumped).not.toHaveBeenCalled()
  })
})

describe('SessionMapRail — what the rail says (SSM-7 / §A.6)', () => {
  it('the focused marker’s accessible name is the SHORT message position, never the message', () => {
    const h = mount()
    const long = 'x'.repeat(400)
    const t: ChatTurn[] = [
      { role: 'user', ts: TS, visibleIndex: 0, segments: [{ kind: 'text', text: long }] },
      { role: 'assistant', ts: TS, visibleIndex: 1, segments: [{ kind: 'text', text: 'ok' }] },
      { role: 'user', ts: TS, visibleIndex: 2, segments: [{ kind: 'text', text: 'and next' }] },
    ]
    const entries = sessionMapEntries(t)
    const { container } = render(
      <SessionMapRail entries={entries} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={() => {}} />,
    )
    const first = container.querySelector('[data-session-mark]') as HTMLElement
    const name = first.getAttribute('aria-label')!
    expect(name).toBe(sessionMapMarkName(entries, 0))
    expect(name).toMatch(/^Message 1 of 2: /)
    expect(name.length, 'the whole message leaked into the accessible name').toBeLessThan(80)
    expect(name).not.toContain(long)
    // Every marker is named, so none is announced as a bare "button".
    for (const el of h.ticks) expect(el.getAttribute('aria-label')).toBeTruthy()
  })

  it('🔑 aria-live is SILENT at rest, speaks the jump, and goes silent again when focus leaves', () => {
    const h = mount()
    const region = h.container.querySelector('[data-session-map-live]')!
    expect(region.getAttribute('aria-live')).toBe('polite')
    expect(region.getAttribute('role')).toBe('status')
    expect(h.live(), 'the live region must not speak before anything happened').toBe('')

    key(h, 'ArrowUp')
    // Moving the cursor says nothing HERE: the focused marker's own aria-label already names the
    // position, and repeating it would be the double-speak §A.6 forbids.
    expect(h.live()).toBe('')

    const at = tabStop(h)
    key(h, 'Enter')
    // The one thing focus does not announce: that the transcript moved.
    expect(h.live()).toMatch(/^Jumped to message \d+ of \d+$/)
    expect(h.live()).not.toBe(h.ticks[at].getAttribute('aria-label'))

    // Focus out of the rail entirely → silent again, so a later hover cannot re-announce it.
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    act(() => { outside.focus() })
    expect(h.live(), 'the rail kept announcing after losing focus').toBe('')
    outside.remove()
  })
})

describe('SessionMapRail — the card the cursor reveals (SSM-7 / §A.6)', () => {
  const card = () => document.querySelector('[data-session-map-card]')

  it('🔑 a cursor key REVEALS the card, and Tab-through still does not', async () => {
    const h = mount()
    expect(card()).toBeNull()
    // Passive focus — what a Tab from the transcript does. SSM-6's contract: nothing opens.
    act(() => { h.ticks[tabStop(h)].focus() })
    await act(() => new Promise((r) => { window.setTimeout(r, 250) }))
    expect(card(), 'Tab-through strobed a card').toBeNull()
    // An explicit cursor key is a different intent, and it does open one.
    key(h, 'ArrowUp')
    await waitFor(() => expect(card()).not.toBeNull())
    // The newest message is prompt 10; one step up is the one before it.
    expect(card()!.textContent).toContain('prompt 8')
  })

  it('🪤 moving the cursor leaves EXACTLY ONE card open, never a stack', async () => {
    const h = mount()
    key(h, 'ArrowUp')
    await waitFor(() => expect(card()).not.toBeNull())
    for (const k of ['ArrowUp', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'ArrowUp']) {
      key(h, k)
      // The whole hazard `ui/Popover.openSignal` would have created: N cards, one per arrow press.
      await waitFor(() => expect(document.querySelectorAll('[data-session-map-card]')).toHaveLength(1))
    }
    // …and the one that is open belongs to the marker that now holds focus.
    const at = tabStop(h)
    expect(document.activeElement).toBe(h.ticks[at])
    expect(card()!.textContent).toContain(h.entries[at].preview)
  })

  it('Escape closes the card and leaves focus on the rail', async () => {
    const h = mount()
    key(h, 'ArrowUp')
    await waitFor(() => expect(card()).not.toBeNull())
    const at = tabStop(h)
    act(() => { fireEvent.keyDown(document, { key: 'Escape' }) })
    await waitFor(() => expect(card()).toBeNull())
    // Focus must not be dropped to <body> — that strands keyboard navigation mid-rail.
    expect(document.activeElement).toBe(h.ticks[at])
  })

  it('leaving the rail closes the card it revealed', async () => {
    const h = mount()
    key(h, 'ArrowUp')
    await waitFor(() => expect(card()).not.toBeNull())
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    act(() => { outside.focus() })
    await waitFor(() => expect(card()).toBeNull())
    outside.remove()
  })
})
