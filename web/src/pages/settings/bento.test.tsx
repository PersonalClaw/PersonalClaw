import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import { Switch } from './bento'

// ── Inline card-control interaction (settings bento) ─────────────────────────
// A bento card wraps its inline controls over a full-card <button> nav overlay:
// the content layer is `pointer-events-none` and each control re-enables
// `pointer-events-auto` + stops the click from reaching the overlay. The subtle
// contract is WHEN it stops: the stop must happen in the BUBBLE phase, so the
// click first reaches the inner switch button (firing its toggle) and only then
// is prevented from bubbling to the nav overlay. A capture-phase stop preempts
// the inner button entirely — the switch renders but is inert. This test locks
// both halves so that regression can't return silently.

describe('bento Switch', () => {
  it('fires its toggle AND does not bubble to the card nav overlay', async () => {
    const onToggle = vi.fn()
    const onNav = vi.fn() // stands in for the full-card nav overlay onClick
    const { getByRole } = render(
      <div onClick={onNav}>
        <Switch on={false} onToggle={onToggle} label="Send on Enter" />
      </div>,
    )
    // Switch flips its busy state around the (async) onToggle — flush in act().
    await act(async () => { fireEvent.click(getByRole('switch')) })
    // The inner switch button's onClick ran (would be skipped by a capture stop).
    expect(onToggle).toHaveBeenCalledTimes(1)
    expect(onToggle).toHaveBeenCalledWith(true)
    // …and the click never reached the nav overlay behind it.
    expect(onNav).not.toHaveBeenCalled()
  })

  it('is a no-op when disabled', () => {
    const onToggle = vi.fn()
    const { getByRole } = render(<Switch on={false} onToggle={onToggle} label="x" disabled />)
    fireEvent.click(getByRole('switch'))
    expect(onToggle).not.toHaveBeenCalled()
  })
})
