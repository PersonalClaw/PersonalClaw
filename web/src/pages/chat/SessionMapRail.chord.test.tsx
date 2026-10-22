import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import { bindingOf } from '../../app/keymap'
import type { ChatTurn } from './chatTypes'

// ── ⌥↑ / ⌥↓ move between your messages from anywhere (#4776) ──────────────────────────────────
//
// Two global keymap rows whose runner is the rail: from the composer, the transcript or anywhere
// else, ⌥↑ jumps to the message before the one in view and ⌥↓ to the one after, without tabbing
// into the rail first. Each jump says so out loud, since focus stays where it was.
//
// VACUITY FLOOR. A chord that jumped to a fixed message would pass "it jumps". So the sequence of
// coordinates is asserted across repeated presses, from the region the transcript reports on
// screen, and from inside a text field, where most typing-only handlers would stand down.

interface Captured { cb: IntersectionObserverCallback; observed: Element[] }
let observers: Captured[] = []
const original = globalThis.IntersectionObserver
beforeEach(() => {
  observers = []
  globalThis.IntersectionObserver = class {
    private rec: Captured
    constructor(cb: IntersectionObserverCallback) { this.rec = { cb, observed: [] }; observers.push(this.rec) }
    observe(el: Element): void { this.rec.observed.push(el) }
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): [] { return [] }
  } as unknown as typeof IntersectionObserver
})
afterEach(() => { globalThis.IntersectionObserver = original; vi.useRealTimers() })

const TS = '2026-09-16T10:00:00.000Z'
const turns: ChatTurn[] = Array.from({ length: 12 }, (_, i) => (i % 2 === 0
  ? { role: 'user', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `question ${i / 2 + 1}` }] }
  : { role: 'assistant', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `answer ${i}` }] }))

function mount() {
  const entries = sessionMapEntries(turns)
  const nodes = new Map(turns.map((_, i) => [i, document.createElement('div')] as const))
  const jumped = vi.fn()
  const { container, unmount } = render(
    <SessionMapRail entries={entries} turnNodes={nodes} scrollRef={{ current: document.createElement('div') }} onJumpTo={jumped} />,
  )
  const onScreen = (coords: number[]) => act(() => {
    observers.at(-1)!.cb(coords.map((c) => ({ target: nodes.get(c)!, isIntersecting: true })) as unknown as IntersectionObserverEntry[], {} as IntersectionObserver)
  })
  const live = () => container.querySelector('[data-session-map-live]')!.textContent
  return { entries, jumped, onScreen, live, unmount }
}

const chord = (key: 'ArrowUp' | 'ArrowDown', target: Element | Window = window) =>
  act(() => { fireEvent.keyDown(target, { key, code: key, altKey: true }) })

describe('⌥↑ / ⌥↓', () => {
  it('are two global rows of the keymap', () => {
    expect(bindingOf('prev-message')).toMatchObject({ scope: 'global', chords: ['alt+arrowup'] })
    expect(bindingOf('next-message')).toMatchObject({ scope: 'global', chords: ['alt+arrowdown'] })
  })

  it('🔑 step from the exchange on screen, one message per press, and say so', () => {
    vi.useFakeTimers()
    const h = mount()
    // Reading the third exchange, its question on screen too.
    h.onScreen([4, 5])
    chord('ArrowUp')
    expect(h.jumped.mock.calls).toEqual([[h.entries[1].visibleIndex, 'smooth']])
    expect(h.live()).toBe('Jumped to message 2 of 6')
    // A second press while the first jump is still bringing its message on screen steps on from
    // that message, not from wherever the transcript is mid-scroll.
    chord('ArrowUp')
    expect(h.jumped.mock.calls.at(-1)).toEqual([h.entries[0].visibleIndex, 'smooth'])
    // At the first message it stays there.
    chord('ArrowUp')
    expect(h.jumped.mock.calls.at(-1)).toEqual([h.entries[0].visibleIndex, 'smooth'])
    chord('ArrowDown')
    expect(h.jumped.mock.calls.at(-1)).toEqual([h.entries[1].visibleIndex, 'smooth'])
    h.unmount()
  })

  it('from a long answer whose question has scrolled away, ⌥↑ goes to that question first', () => {
    const h = mount()
    h.onScreen([7])
    chord('ArrowUp')
    expect(h.jumped.mock.calls).toEqual([[h.entries[3].visibleIndex, 'smooth']])
    h.unmount()
  })

  it('⌥↓ goes to the message after the last one on screen', () => {
    const h = mount()
    h.onScreen([2, 3, 4])
    chord('ArrowDown')
    expect(h.jumped.mock.calls).toEqual([[h.entries[3].visibleIndex, 'smooth']])
    h.unmount()
  })

  it('work from inside a text field, where the owner usually is', () => {
    const h = mount()
    const field = document.createElement('textarea')
    document.body.appendChild(field)
    field.focus()
    chord('ArrowUp', field)
    expect(h.jumped).toHaveBeenCalledTimes(1)
    field.remove()
    h.unmount()
  })

  it('do nothing once the rail is gone', () => {
    const h = mount()
    h.unmount()
    chord('ArrowUp')
    expect(h.jumped).not.toHaveBeenCalled()
  })
})
