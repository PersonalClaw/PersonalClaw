import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import type { ChatTurn } from './chatTypes'

// ── Click jumps, press-and-drag scrubs (#4776) ─────────────────────────────────────────────────
//
// A click jumps the transcript smoothly to its message. Pressing a marker and dragging onto the
// next captures the pointer: every marker the drag crosses jumps the transcript AT ONCE, the
// preview follows the drag, and letting go leaves the transcript where the drag ended without a
// second jump from the click the release produces.
//
// VACUITY FLOOR. "a scrub jumps" passes for a rail that jumps once per press; "no second jump"
// passes for a rail that never jumps. So the jumps are asserted as the exact sequence of
// coordinates and behaviours, the click after the release is really dispatched (and swallowed),
// and a plain click is asserted to still jump — once, smoothly.

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
afterEach(() => { vi.restoreAllMocks() })

const TS = '2026-09-16T10:00:00.000Z'
const turns: ChatTurn[] = Array.from({ length: 16 }, (_, i) => (i % 2 === 0
  ? { role: 'user', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `question ${i / 2 + 1}` }] }
  : { role: 'assistant', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `answer ${i}` }] }))

/** The rail with each marker laid out on its 24px pitch, as a browser would lay it out (jsdom
 *  computes no layout), and a list that records pointer capture. */
function mount() {
  const entries = sessionMapEntries(turns)
  const jumped = vi.fn()
  const { container } = render(
    <SessionMapRail entries={entries} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={jumped} />,
  )
  const ticks = [...container.querySelectorAll('[data-session-mark]')] as HTMLButtonElement[]
  ticks.forEach((el, i) => {
    el.getBoundingClientRect = () => ({ top: 100 + i * 24, bottom: 124 + i * 24, left: 16, right: 48, height: 24, width: 32, x: 16, y: 100 + i * 24, toJSON: () => ({}) }) as DOMRect
  })
  const list = container.querySelector('[data-session-map-list]') as HTMLElement
  const captured = new Set<number>()
  list.setPointerCapture = (id: number) => { captured.add(id) }
  list.releasePointerCapture = (id: number) => { captured.delete(id) }
  list.hasPointerCapture = (id: number) => captured.has(id)
  const preview = () => container.querySelector('[data-session-map-preview]')?.textContent ?? null
  return { entries, jumped, ticks, list, captured, preview }
}

const at = (i: number) => 112 + i * 24

describe('pressing and dragging scrubs the transcript', () => {
  it('🔑 a drag onto the next marker captures the pointer and jumps at once, the preview following', () => {
    const h = mount()
    act(() => { fireEvent.pointerDown(h.ticks[2], { pointerId: 7, button: 0, buttons: 1, pointerType: 'mouse', clientY: at(2) }) })
    // Pressing alone jumps nothing: it may still be a click.
    expect(h.jumped).not.toHaveBeenCalled()
    // Movement within the pressed marker is still not a scrub.
    act(() => { fireEvent.pointerMove(h.ticks[2], { pointerId: 7, buttons: 1, pointerType: 'mouse', clientY: at(2) + 3 }) })
    expect(h.jumped).not.toHaveBeenCalled()
    act(() => { fireEvent.pointerMove(h.ticks[3], { pointerId: 7, buttons: 1, pointerType: 'mouse', clientY: at(3) }) })
    expect(h.captured.has(7), 'the drag did not capture the pointer').toBe(true)
    expect(h.jumped.mock.calls).toEqual([[h.entries[3].visibleIndex, 'instant']])
    expect(h.preview()).toContain('question 4')
    // Captured, every move lands on the list; the marker under the pointer is read from the layout.
    act(() => { fireEvent.pointerMove(h.list, { pointerId: 7, buttons: 1, pointerType: 'mouse', clientY: at(6) }) })
    act(() => { fireEvent.pointerMove(h.list, { pointerId: 7, buttons: 1, pointerType: 'mouse', clientY: at(6) + 5 }) })
    act(() => { fireEvent.pointerMove(h.list, { pointerId: 7, buttons: 1, pointerType: 'mouse', clientY: at(5) }) })
    expect(h.jumped.mock.calls).toEqual([
      [h.entries[3].visibleIndex, 'instant'],
      [h.entries[6].visibleIndex, 'instant'],
      // Moving within marker 6 jumped nothing more.
      [h.entries[5].visibleIndex, 'instant'],
    ])
    expect(h.preview()).toContain('question 6')
    expect(document.querySelectorAll('[data-session-map-preview]'), 'one preview, however far the drag went').toHaveLength(1)
    // Past either end the scrub holds the end marker rather than jumping nowhere.
    act(() => { fireEvent.pointerMove(h.list, { pointerId: 7, buttons: 1, pointerType: 'mouse', clientY: 9999 }) })
    expect(h.jumped.mock.calls.at(-1)).toEqual([h.entries[7].visibleIndex, 'instant'])
  })

  it('🔑 letting go leaves the transcript where the drag ended, and its click fires no second jump', () => {
    const h = mount()
    act(() => { fireEvent.pointerDown(h.ticks[1], { pointerId: 3, button: 0, buttons: 1, pointerType: 'mouse', clientY: at(1) }) })
    act(() => { fireEvent.pointerMove(h.ticks[2], { pointerId: 3, buttons: 1, pointerType: 'mouse', clientY: at(2) }) })
    act(() => { fireEvent.pointerMove(h.list, { pointerId: 3, buttons: 1, pointerType: 'mouse', clientY: at(4) }) })
    const jumps = h.jumped.mock.calls.length
    act(() => { fireEvent.pointerUp(h.list, { pointerId: 3, button: 0, pointerType: 'mouse', clientY: at(4), clientX: 30 }) })
    expect(h.captured.has(3), 'the capture outlived the drag').toBe(false)
    // The release produces ONE click: on the list where the capture ended, or on the marker in a
    // browser that targets the element under the pointer. Either way it must not jump again.
    act(() => { fireEvent.click(h.ticks[4]) })
    expect(h.jumped.mock.calls.length, 'the release jumped a second time').toBe(jumps)
    // The keyboard carries on from where the drag ended.
    expect(h.ticks[4].tabIndex).toBe(0)
  })

  it('a plain click still jumps, once and smoothly — and the next click is not swallowed', () => {
    const h = mount()
    act(() => { fireEvent.pointerDown(h.ticks[2], { pointerId: 1, button: 0, buttons: 1, pointerType: 'mouse', clientY: at(2) }) })
    act(() => { fireEvent.pointerUp(h.ticks[2], { pointerId: 1, button: 0, pointerType: 'mouse', clientY: at(2) }) })
    act(() => { fireEvent.click(h.ticks[2]) })
    expect(h.jumped.mock.calls).toEqual([[h.entries[2].visibleIndex, 'smooth']])
    expect(h.captured.size, 'a click captured the pointer').toBe(0)
    act(() => { fireEvent.click(h.ticks[5]) })
    expect(h.jumped.mock.calls.at(-1)).toEqual([h.entries[5].visibleIndex, 'smooth'])
  })

  it('a touch press does not scrub: the touch form is the drawer, and a finger scrolls', () => {
    const h = mount()
    act(() => { fireEvent.pointerDown(h.ticks[1], { pointerId: 9, button: 0, buttons: 1, pointerType: 'touch', clientY: at(1) }) })
    act(() => { fireEvent.pointerMove(h.ticks[3], { pointerId: 9, buttons: 1, pointerType: 'touch', clientY: at(3) }) })
    expect(h.captured.size).toBe(0)
    expect(h.jumped).not.toHaveBeenCalled()
  })
})
