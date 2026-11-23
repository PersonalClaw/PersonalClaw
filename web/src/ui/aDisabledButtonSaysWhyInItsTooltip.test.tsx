import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Button } from './Button'
import { QuietButton } from './QuietButton'
import { TAP_SHOWS_MS } from './Tooltip'

// ── An unavailable button says why in the app's own tooltip ─────────────────────────────────────
//
// `Button` and `QuietButton` take a `disabledReason`, and showed it only as the browser's native
// title: slow to appear, unstyled, never on touch, and read differently by different screen
// readers. The attention pages had already moved their refused-action reasons into the themed
// Tooltip. Now both buttons say it there: on hover, at once on keyboard focus, and on a tap, for a
// while. It is the button's description, while the button's name stays its action, and no native
// title carries it.

afterEach(() => { cleanup(); vi.useRealTimers() })

const REASON = 'Enter a name first'
const bubble = () => document.querySelector('[data-tooltip-bubble]')

const CASES = [
  {
    name: 'Button',
    mount: (onClick: () => void) => render(<Button onClick={onClick} disabled disabledReason={REASON}>Create project</Button>),
  },
  {
    name: 'QuietButton',
    mount: (onClick: () => void) => render(<QuietButton onClick={onClick} disabled disabledReason={REASON}>Create project</QuietButton>),
  },
] as const

describe.each(CASES)('an unavailable $name', ({ mount }) => {
  it('🔴 says why in the tooltip on keyboard focus, never in a native title', () => {
    const onClick = vi.fn()
    mount(onClick)
    const button = screen.getByRole('button', { name: 'Create project' })

    expect(button.getAttribute('title') ?? '', 'no native title carries the reason').not.toContain(REASON)
    expect(button).toHaveAccessibleDescription(REASON)

    fireEvent.focus(button)
    expect(bubble()?.textContent).toBe(REASON)
    fireEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('🔴 says why on a tap, for a while', () => {
    vi.useFakeTimers()
    mount(vi.fn())
    const button = screen.getByRole('button', { name: 'Create project' })

    fireEvent.pointerDown(button, { pointerType: 'touch' })
    fireEvent.pointerUp(button, { pointerType: 'touch' })
    // A tap focuses the button after its pointerup, as Chrome on Android does.
    fireEvent.focus(button)
    expect(bubble()?.textContent).toBe(REASON)

    act(() => { vi.advanceTimersByTime(TAP_SHOWS_MS + 50) })
    expect(bubble()).toBeNull()
  })
})

describe('an available button', () => {
  it('opens no tooltip and keeps its title', () => {
    vi.useFakeTimers()
    render(<Button onClick={() => {}} title="Make the project">Create project</Button>)
    const button = screen.getByRole('button', { name: 'Create project' })

    fireEvent.focus(button)
    fireEvent.pointerEnter(button, { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(1000) })

    expect(bubble()).toBeNull()
    expect(button).toHaveAttribute('title', 'Make the project')
    // Its own title stays its title, and the only description it has.
    expect(button).toHaveAccessibleDescription('Make the project')
  })

  it('keeps its focus as it becomes available', () => {
    const { rerender } = render(<Button onClick={() => {}} disabled disabledReason={REASON}>Create project</Button>)
    const button = screen.getByRole('button', { name: 'Create project' })
    button.focus()

    rerender(<Button onClick={() => {}}>Create project</Button>)

    expect(document.activeElement).toBe(button)
    expect(screen.getByRole('button', { name: 'Create project' })).toBe(button)
  })
})
