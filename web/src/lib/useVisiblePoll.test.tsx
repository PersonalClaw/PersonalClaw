import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import {
  IDLE_AFTER_MS, LONG_IDLE_AFTER_MS, MAX_IDLE_INTERVAL_MS, idleInterval, noteInputForTests, useVisiblePoll,
} from './useVisiblePoll'

// ── An idle tab stops hammering the gateway ──────────────────────────────────────────────────────
//
// Measured on day 8: one idle Home tab, 428 requests in 3 minutes, from eleven pollers that each
// paused only when the tab was HIDDEN. A tab left open on a second monitor is visible and idle, and
// it polled at full rate forever — each request a row in the security log. These pin the back-off
// that fixes it, and the two things it must not break: a user who comes back sees fresh state at
// once, and a live mirror the user is watching keeps its cadence.

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.useFakeTimers()
  noteInputForTests()
  setHidden(false)
})
afterEach(() => {
  vi.useRealTimers()
  setHidden(false)
})

const input = () => act(() => { window.dispatchEvent(new Event('pointerdown')) })

describe('idleInterval — the back-off rule', () => {
  it('is the base interval while the user is active', () => {
    expect(idleInterval(8000, 0)).toBe(8000)
    expect(idleInterval(8000, IDLE_AFTER_MS - 1)).toBe(8000)
  })
  it('stretches once idle, further once long idle, and never past the ceiling', () => {
    expect(idleInterval(8000, IDLE_AFTER_MS)).toBe(32_000)
    expect(idleInterval(8000, LONG_IDLE_AFTER_MS)).toBe(80_000)
    expect(idleInterval(60_000, LONG_IDLE_AFTER_MS)).toBe(MAX_IDLE_INTERVAL_MS)
    // A poll already slower than the ceiling keeps its own cadence rather than speeding up.
    expect(idleInterval(10 * 60_000, LONG_IDLE_AFTER_MS)).toBe(10 * 60_000)
  })
})

describe('useVisiblePoll', () => {
  it('polls at its cadence while the user is active', () => {
    const fn = vi.fn()
    renderHook(() => useVisiblePoll(fn, 8000))
    expect(fn).toHaveBeenCalledTimes(1)  // on mount
    for (let i = 0; i < 3; i++) { act(() => { vi.advanceTimersByTime(8000) }); input() }
    expect(fn).toHaveBeenCalledTimes(4)
  })

  it('backs off when nobody is using the tab — the idle-Home defect', () => {
    const fn = vi.fn()
    renderHook(() => useVisiblePoll(fn, 8000))
    // Three idle minutes. At the old fixed cadence this is 1 + 22 calls.
    act(() => { vi.advanceTimersByTime(180_000) })
    const calls = fn.mock.calls.length
    expect(calls).toBeLessThanOrEqual(1 + 30_000 / 8000 + 150_000 / 32_000 + 1)
    expect(calls).toBeGreaterThan(1)  // …but it still keeps a heartbeat
  })

  it('catches up at once when the user comes back', () => {
    const fn = vi.fn()
    renderHook(() => useVisiblePoll(fn, 8000))
    act(() => { vi.advanceTimersByTime(IDLE_AFTER_MS + 20_000) })
    const before = fn.mock.calls.length
    input()
    expect(fn.mock.calls.length).toBe(before + 1)
    // …and resumes the ACTIVE cadence from there.
    act(() => { vi.advanceTimersByTime(8000) })
    expect(fn.mock.calls.length).toBe(before + 2)
  })

  it('pauses while hidden and catches up when visible again', () => {
    const fn = vi.fn()
    renderHook(() => useVisiblePoll(fn, 8000))
    act(() => { setHidden(true) })
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(fn).toHaveBeenCalledTimes(1)
    act(() => { setHidden(false) })
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('a live mirror opts out and keeps its cadence while watched without input', () => {
    const fn = vi.fn()
    renderHook(() => useVisiblePoll(fn, 2500, { idleBackoff: false }))
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(fn).toHaveBeenCalledTimes(1 + 60_000 / 2500)
  })

  it('does not read twice on mount when the caller already did', () => {
    const fn = vi.fn()
    renderHook(() => useVisiblePoll(fn, 8000, { immediate: false }))
    expect(fn).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(8000) })
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('stops when disabled', () => {
    const fn = vi.fn()
    renderHook(() => useVisiblePoll(fn, null))
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(fn).not.toHaveBeenCalled()
  })
})
