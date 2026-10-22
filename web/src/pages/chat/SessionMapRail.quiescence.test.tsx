import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import { MARK_MAX_PX } from './sessionMapGeometry'
import type { ChatTurn } from './chatTypes'

// ── The rail does no animation work at rest (#4776) ────────────────────────────────────────────
//
// At rest, in a hidden document and under reduced motion the rail schedules NO animation frame:
// a marker's length is a CSS transform on a CSS transition, which the browser runs off the main
// thread and the global reduced-motion rule collapses. These tests count the frames the rail asks
// for while it is mounted, pointed at, arrowed through, scrolled and left alone.
//
// VACUITY FLOOR. "no frames were scheduled" passes against a spy that never sees anything. So each
// test first proves its spy counts (a positive control that calls it and reads 1), and the
// interactions are asserted to have DONE something (the target grew, the preview opened), so a
// rail that ignored the pointer cannot pass by having nothing to animate.

const TS = '2026-09-16T10:00:00.000Z'
const turns: ChatTurn[] = Array.from({ length: 12 }, (_, i) => (i % 2 === 0
  ? { role: 'user', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `question ${i / 2 + 1}` }] }
  : { role: 'assistant', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `answer ${i}` }] }))

let reduce = false
const realMatchMedia = window.matchMedia
beforeEach(() => {
  reduce = false
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: reduce && query.includes('prefers-reduced-motion'),
      media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {},
      removeListener: () => {}, dispatchEvent: () => false, onchange: null,
    }) as unknown as MediaQueryList,
  })
})
afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: realMatchMedia })
  vi.restoreAllMocks()
  vi.useRealTimers()
})

/** Count every animation frame and interval asked for from here on, with a positive control. */
function countFrames() {
  const frames = vi.spyOn(window, 'requestAnimationFrame')
  const intervals = vi.spyOn(window, 'setInterval')
  const control = window.requestAnimationFrame(() => {})
  window.cancelAnimationFrame(control)
  expect(frames, 'the frame spy counts nothing — the test would pass vacuously').toHaveBeenCalledTimes(1)
  frames.mockClear()
  return () => frames.mock.calls.length + intervals.mock.calls.length
}

const lengthOf = (el: Element) => {
  const m = /scaleX\(([\d.]+)\)/.exec((el as HTMLElement).getAttribute('style') ?? '')
  return m ? Number(m[1]) * MARK_MAX_PX : NaN
}

/** Mount, then point at a marker until its preview opens, arrow through the rail, scroll its list,
 *  and leave it alone for a second. Returns the frames asked for. */
function exercise(): number {
  vi.useFakeTimers()
  const frames = countFrames()
  const { container } = render(
    <SessionMapRail entries={sessionMapEntries(turns)} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={() => {}} />,
  )
  const ticks = [...container.querySelectorAll('[data-session-mark]')] as HTMLButtonElement[]
  act(() => { fireEvent.pointerMove(ticks[2], { pointerType: 'mouse' }) })
  act(() => { vi.advanceTimersByTime(200) })
  expect(lengthOf(ticks[2].querySelector('[data-session-map-mark-line]')!), 'the target did not grow').toBe(MARK_MAX_PX)
  expect(container.querySelector('[data-session-map-preview]'), 'the preview did not open').not.toBeNull()
  act(() => { ticks[5].focus() })
  act(() => { fireEvent.keyDown(ticks[5], { key: 'ArrowUp' }) })
  act(() => { fireEvent.scroll(container.querySelector('[data-session-map-list]')!) })
  act(() => { fireEvent.pointerLeave(container.querySelector('nav')!) })
  act(() => { vi.advanceTimersByTime(1000) })
  return frames()
}

describe('the rail asks for no animation frame', () => {
  it('at rest, pointed at, arrowed through and left alone', () => {
    expect(exercise()).toBe(0)
  })

  it('under reduced motion — and the target still grows, it just does not travel there', () => {
    reduce = true
    expect(exercise()).toBe(0)
  })

  it('in a hidden document', () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    expect(exercise()).toBe(0)
  })

  it('and once it is gone, nothing it started is left running', () => {
    vi.useFakeTimers()
    const { container, unmount } = render(
      <SessionMapRail entries={sessionMapEntries(turns)} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={() => {}} />,
    )
    const tick = container.querySelector('[data-session-mark]')!
    act(() => { fireEvent.pointerMove(tick, { pointerType: 'mouse' }) })
    // An open intent is armed; unmounting must take it down rather than fire it into nothing.
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    unmount()
    expect(vi.getTimerCount(), 'a timer the rail armed outlived it').toBe(0)
  })
})
