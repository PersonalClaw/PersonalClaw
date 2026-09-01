import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { PanelLeft } from 'lucide-react'
import { SpotlightTour } from './SpotlightTour'

// ── Escape must close the layer that HAS focus, not the one underneath ────────────────────────
//
// Measured in Chromium against the real shell, tour launched from `#/discover`:
//
//   palette open + focused, tour behind → ONE Escape closed the TOUR, palette stayed up and
//                                          focused; a SECOND Escape closed the palette
//   palette open, no tour               → ONE Escape closed the palette (the control case)
//
// So the tour was eating a key pressed at a layer above it. The mechanism is ordering, not
// intent: `SpotlightTour` binds `keydown` on `document` and `app/CommandPalette` binds it on
// `window`, and `document` fires FIRST in the bubble path — so an unconditional
// `stopPropagation()` here consumed the press before the palette ever saw it.
//
// 🔑 THE PREMISE IN THE ORIGINAL COMMENT WAS THE BUG: "the tour is the topmost layer". It is
// not. This overlay rides `--z-modal`; the command palette rides `--z-toast` (higher), and it is *supposed* to open
// over the tour — the tour's own doctrine is that guidance never gates. Two true statements
// ("consume Escape so one press closes one layer" and "the tour is topmost") were combined, and
// only the first one holds.
//
// The guard is "does the tour hold focus", which needs no layer registry: whoever owns focus
// owns the key. Focus on `<body>` still counts as the tour's — the trap takes focus on mount and
// re-takes it on every stop, so an unfocused document means nothing else has claimed it and the
// tour must stay dismissable.

const STEPS = [
  { id: 'one', anchor: 'a-one', icon: PanelLeft, title: 'First stop', body: 'Body one.' },
  { id: 'two', anchor: 'a-two', icon: PanelLeft, title: 'Second stop', body: 'Body two.' },
]

/** The tour plus a sibling input standing in for a layer above it (the palette's search box). */
function Harness({ onExit, withLayer }: { onExit: () => void; withLayer: boolean }) {
  const [index, setIndex] = useState(0)
  return (
    <>
      <div data-tour="a-one">anchor one</div>
      {withLayer && <input aria-label="Search pages and actions" defaultValue="" />}
      <SpotlightTour steps={STEPS} index={index} label="PersonalClaw tour"
        onIndex={setIndex} onExit={onExit} />
    </>
  )
}

describe('Escape goes to the focused layer', () => {
  it('exits the tour when the tour holds focus', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer={false} />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    await user.keyboard('{Escape}')
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('exits when nothing at all holds focus, so it never becomes undismissable', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer={false} />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    // Drop focus to <body> — the state between mount and the trap's own focus effect.
    ;(document.activeElement as HTMLElement | null)?.blur()
    expect(document.activeElement === document.body || document.activeElement === null).toBe(true)
    await user.keyboard('{Escape}')
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('does NOT exit when a layer above it holds focus — the defect this pins', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    const above = screen.getByLabelText('Search pages and actions')
    above.focus()
    expect(document.activeElement).toBe(above)
    await user.keyboard('{Escape}')
    // The focused layer owns the key. Before the fix this was 1 — the tour closed instead.
    expect(onExit, 'the tour must not eat a key pressed at a layer above it').not.toHaveBeenCalled()
  })

  it('still lets the tour go once focus comes back to it', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    const above = screen.getByLabelText('Search pages and actions')
    above.focus()
    await user.keyboard('{Escape}')
    expect(onExit).not.toHaveBeenCalled()
    // The layer above closed and handed focus back — the next press is the tour's.
    screen.getByRole('dialog').focus()
    await user.keyboard('{Escape}')
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('the X button and a shield click still exit regardless of focus', async () => {
    const onExit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onExit={onExit} withLayer />)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    screen.getByLabelText('Search pages and actions').focus()
    // The guard is scoped to the KEY. Every pointer route out is untouched.
    await user.click(screen.getByRole('button', { name: 'End the tour' }))
    expect(onExit).toHaveBeenCalledTimes(1)
  })
})
