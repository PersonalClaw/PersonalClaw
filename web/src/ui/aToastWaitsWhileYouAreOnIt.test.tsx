/**
 * A toast waits while the pointer or the keyboard focus is on the stack, and then goes on with the
 * time it had left.
 *
 * 🔴 Measured before: each toast got one 5,000 ms timer when it arrived, and nothing paused it. A
 * keyboard user who tabbed to a toast's link or its Dismiss lost it while reading, and so did
 * anyone pointing at it (WCAG 2.2.1). The approval nudge's link to where the approval is answered
 * is one of those links.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { Toaster } from './Toaster'

vi.mock('../design/soundCues', () => ({
  playCue: () => {}, armCueAudio: () => {}, soundCuesEnabled: () => false, setSoundCuesEnabled: () => {},
}))

const TOAST_MS = 5_000

function toast(message: string) {
  act(() => { window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level: 'info', message } })) })
}

/** What the stack holds, read off its polite region, which lists exactly the toasts up. */
const shown = () => document.querySelector('[role="status"][aria-live="polite"]')?.textContent ?? ''
const after = (ms: number) => act(() => { vi.advanceTimersByTime(ms) })

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('a toast leaves on its own, but never while you are on it', () => {
  it('the control: with nothing on it, a toast leaves at 5 s', () => {
    render(<Toaster />)
    toast('Saved.')
    after(TOAST_MS - 1)
    expect(shown()).toContain('Saved.')
    after(1)
    expect(shown()).toBe('')
  })

  it('🔴 with focus on its Dismiss it is still there at 6 s, and leaves at the time it had left', () => {
    render(<Toaster />)
    toast('Loop stalled: it needs an answer')
    after(1_000)
    const dismiss = screen.getByRole('button', { name: /^Dismiss: Loop stalled/ })
    act(() => dismiss.focus())
    after(TOAST_MS * 2)
    expect(shown()).toContain('Loop stalled')

    act(() => dismiss.blur())
    after(TOAST_MS - 1_000 - 1)
    expect(shown()).toContain('Loop stalled')
    after(1)
    expect(shown()).toBe('')
  })

  it('🔴 with the pointer on the stack every toast waits, each with its own time left', () => {
    render(<Toaster />)
    toast('First')
    after(3_000)
    toast('Second')
    const card = screen.getByRole('button', { name: 'Dismiss: Second' }).parentElement!
    fireEvent.pointerEnter(card)
    after(TOAST_MS * 3)
    expect(shown()).toContain('First')
    expect(shown()).toContain('Second')

    fireEvent.pointerLeave(card)
    after(TOAST_MS - 3_000)
    expect(shown()).not.toContain('First')
    expect(shown()).toContain('Second')
    after(3_000)
    expect(shown()).toBe('')
  })

  it('a toast that arrives while you are on the stack starts its time when you leave', () => {
    render(<Toaster />)
    toast('First')
    const card = screen.getByRole('button', { name: 'Dismiss: First' }).parentElement!
    fireEvent.pointerEnter(card)
    toast('Arrived while held')
    after(TOAST_MS * 2)
    fireEvent.pointerLeave(card)
    after(TOAST_MS - 1)
    expect(shown()).toContain('Arrived while held')
    after(1)
    expect(shown()).toBe('')
  })

  it('🔴 dismissing the toast that holds focus lets the others go on with the time they had left', () => {
    // A removed element sends no blur (jsdom, Firefox), so a hold that waited for one held the
    // rest of the stack until focus next moved.
    render(<Toaster />)
    toast('First')
    after(1_000)
    toast('Second')
    after(1_000)
    const dismiss = screen.getByRole('button', { name: 'Dismiss: First' })
    act(() => dismiss.focus())
    after(TOAST_MS * 2)
    expect(shown()).toContain('Second')

    fireEvent.click(dismiss)
    expect(shown()).not.toContain('First')
    after(TOAST_MS - 1_000 - 1)
    expect(shown(), 'the rest of the stack stayed held').toContain('Second')
    after(1)
    expect(shown()).toBe('')
  })

  it('a pointer still on the stack keeps holding after a Dismiss', () => {
    render(<Toaster />)
    toast('First')
    toast('Second')
    const dismiss = screen.getByRole('button', { name: 'Dismiss: First' })
    fireEvent.pointerEnter(dismiss.parentElement!)
    act(() => dismiss.focus())
    fireEvent.click(dismiss)
    after(TOAST_MS * 2)
    expect(shown()).toContain('Second')
  })
})
