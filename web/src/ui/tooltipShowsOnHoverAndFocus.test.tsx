import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { Tooltip, TOOLTIP_DELAY_MS } from './Tooltip'

// ── A tooltip shows on hover and on keyboard focus ──────────────────────────────────────────
//
// A tooltip says what a control does before you use it. It shows after a short hover and at once
// on keyboard focus, stays while the pointer is over it, and hides on leave, blur and Escape. Its
// text is the control's description whether or not it is showing, and a touch never opens it.
// Escape is handled on the control itself, so closing a tooltip does not also stop a running turn.

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function mount() {
  render(
    <Tooltip text="Talk instead of typing.">
      {(trigger) => <button type="button" aria-label="Hands-free voice" {...trigger}>T</button>}
    </Tooltip>,
  )
  return screen.getByRole('button', { name: 'Hands-free voice' })
}

const bubble = () => document.querySelector('[data-tooltip-bubble]')

describe('a tooltip shows on hover and on keyboard focus', () => {
  it('is the control’s description whether or not it is showing', () => {
    const button = mount()
    const tip = screen.getByRole('tooltip', { hidden: true })
    expect(tip.textContent).toBe('Talk instead of typing.')
    expect(button.getAttribute('aria-describedby')).toBe(tip.id)
    expect(button.hasAttribute('title')).toBe(false)
    expect(bubble()).toBeNull()
  })

  it('shows after the hover delay, stays while hovered, and hides on leave', () => {
    vi.useFakeTimers()
    const button = mount()
    fireEvent.pointerEnter(button, { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS - 50) })
    expect(bubble()).toBeNull()
    act(() => { vi.advanceTimersByTime(60) })
    expect(bubble()?.textContent).toBe('Talk instead of typing.')
    // Onto the bubble itself: it stays.
    fireEvent.pointerLeave(button, { pointerType: 'mouse', relatedTarget: bubble() })
    fireEvent.pointerEnter(bubble()!, { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(500) })
    expect(bubble()).not.toBeNull()
    fireEvent.pointerLeave(bubble()!, { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(200) })
    expect(bubble()).toBeNull()
  })

  it('shows at once on keyboard focus and hides on blur', () => {
    const button = mount()
    fireEvent.focus(button)
    expect(bubble()?.textContent).toBe('Talk instead of typing.')
    fireEvent.blur(button)
    expect(bubble()).toBeNull()
  })

  it('is as wide as its text wherever it sits, so the window’s edge never squeezes it', () => {
    const button = mount()
    fireEvent.focus(button)
    const style = (bubble() as HTMLElement).style
    expect(style.width).toBe('max-content')
    expect(style.maxWidth).toMatch(/^min\(20rem/)
  })

  it('hides on Escape, handled on the control, so a running turn keeps running', () => {
    const button = mount()
    let stops = 0
    const stopTurn = (e: KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) stops += 1 }
    document.addEventListener('keydown', stopTurn)
    try {
      fireEvent.focus(button)
      expect(bubble()).not.toBeNull()
      fireEvent.keyDown(button, { key: 'Escape' })
      expect(bubble()).toBeNull()
      expect(stops).toBe(0)
      // With no tooltip showing, Escape is the turn's again.
      fireEvent.keyDown(button, { key: 'Escape' })
      expect(stops).toBe(1)
    } finally {
      document.removeEventListener('keydown', stopTurn)
    }
  })

  it('never opens on touch', () => {
    vi.useFakeTimers()
    const button = mount()
    fireEvent.pointerEnter(button, { pointerType: 'touch' })
    fireEvent.pointerDown(button, { pointerType: 'touch' })
    // A tap focuses the button after its pointerup, as Chrome on Android does.
    fireEvent.pointerUp(button, { pointerType: 'touch' })
    fireEvent.focus(button)
    act(() => { vi.advanceTimersByTime(1000) })
    expect(bubble()).toBeNull()
  })

  it('opens on a keyboard focus after a press that gave no focus, as in WebKit', () => {
    const button = mount()
    fireEvent.pointerDown(button, { pointerType: 'mouse' })
    fireEvent.pointerUp(button, { pointerType: 'mouse' })
    fireEvent.focus(button)
    expect(bubble()).not.toBeNull()
  })

  it('follows its control when the window resizes', () => {
    const button = mount()
    let left = 100
    button.getBoundingClientRect = () => ({ left, top: 400, right: left + 40, bottom: 440, width: 40, height: 40, x: left, y: 400, toJSON: () => ({}) }) as DOMRect
    fireEvent.focus(button)
    const before = (bubble() as HTMLElement).style.left
    left = 300
    act(() => { window.dispatchEvent(new Event('resize')) })
    expect((bubble() as HTMLElement).style.left).not.toBe(before)
  })

  it('does not open on the focus a mouse press gives', () => {
    const button = mount()
    fireEvent.pointerDown(button, { pointerType: 'mouse' })
    fireEvent.focus(button)
    expect(bubble()).toBeNull()
  })
})
