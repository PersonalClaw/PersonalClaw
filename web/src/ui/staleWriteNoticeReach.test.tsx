import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, renderHook, screen } from '@testing-library/react'
import { HeldChange, StaleWriteNotice } from './StaleWriteNotice'
import { useStaleWriteGuard } from '../lib/useStaleWriteGuard'
import type { Revisioned } from '../lib/staleWrite'

// ── A refused save is seen, and nothing typed after it is lost ─────────────────────────────────
//
// Two defects the two-tab drive measured, both about what the user can SEE once a save is refused:
//
//   · the notice rendered where it was placed — at the end of a long form it sat at y=2114 in a
//     1000px window, under the sticky Save bar, and focus dropped to <body>. The only sign that the
//     save had not happened was Save turning grey;
//   · the form stayed editable, and Reload and reapply puts back the change as it was at Save — so
//     text typed after the refusal was dropped without a word, though the notice says "It’s kept".

const stale = () => Object.assign(new Error('changed'), { status: 409, code: 'stale_write' })

function refusedGuard() {
  const read = () => Promise.resolve<Revisioned<string>>({ value: 'theirs', revision: 'r2' })
  const { result } = renderHook(() => useStaleWriteGuard<string>({ read, write: () => Promise.reject(stale()) }))
  return result
}

describe('the notice reaches the user when it appears', () => {
  const scrolled = vi.fn()
  const original = Element.prototype.scrollIntoView
  beforeEach(() => { Element.prototype.scrollIntoView = scrolled; scrolled.mockClear() })
  afterEach(() => { Element.prototype.scrollIntoView = original })

  it('scrolls itself into view and takes focus, once, when a refusal arrives', async () => {
    const guard = refusedGuard()
    const { rerender } = render(<StaleWriteNotice guard={guard.current} what="This note" />)
    expect(scrolled, 'nothing to show before a refusal').not.toHaveBeenCalled()
    await act(async () => { await guard.current.save({ value: 'base', revision: 'r1' }, 'mine', () => 'mine') })
    rerender(<StaleWriteNotice guard={guard.current} what="This note" />)
    const band = screen.getByRole('alert')
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' })
    expect(document.activeElement, 'a keyboard user lands on the choice, not on <body>').toBe(band)
    // The re-read landing changes the conflict, not whether there is one: no second jump.
    await act(async () => { await Promise.resolve() })
    rerender(<StaleWriteNotice guard={guard.current} what="This note" />)
    expect(scrolled).toHaveBeenCalledTimes(1)
  })
})

describe('HeldChange', () => {
  it('turns the editor off while a change is held, and back on when it is settled', async () => {
    const guard = refusedGuard()
    const view = (g: typeof guard.current) => (
      <HeldChange guard={g}><input aria-label="Body" defaultValue="mine" /></HeldChange>
    )
    const { rerender } = render(view(guard.current))
    expect(screen.getByLabelText('Body')).toHaveProperty('disabled', false)
    await act(async () => { await guard.current.save({ value: 'base', revision: 'r1' }, 'mine', () => 'mine') })
    rerender(view(guard.current))
    const field = screen.getByLabelText('Body') as HTMLInputElement
    expect(field.matches(':disabled'), 'nothing typed now can be left out of what Reapply puts back').toBe(true)
    act(() => guard.current.discard())
    rerender(view(guard.current))
    expect((screen.getByLabelText('Body') as HTMLInputElement).matches(':disabled')).toBe(false)
  })
})
