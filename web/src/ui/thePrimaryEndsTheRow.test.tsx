// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Pencil, Pin, Plus, Sparkles } from 'lucide-react'
import { HeaderActions, HeaderControl, HeaderMenu } from './HeaderActions'

// ── The primary action ends the row, after the `…` ───────────────────────────────────────────────
//
// The `…` was the row's last control, so on a page whose header held one (a thread's Save as
// starter, a narrow row's shed actions) the primary was second from the right, and the one order
// had an exception. A `variant="primary"` control draws in a slot after the `…` now. It is a portal
// into that slot, so the page's tree is unchanged, and the slot follows the `…` in the DOM, so a
// keyboard reaches the controls in the order they are seen.

/** The live row's buttons in DOM order, by name (the probes are `aria-hidden`). */
const row = () => screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.textContent?.trim())

describe('the primary action', () => {
  it('follows the `…`', () => {
    render(
      <HeaderActions>
        <HeaderControl icon={Pin} label="Pin" />
        <HeaderControl icon={Sparkles} label="Save as starter" inOverflow />
        <HeaderControl icon={Pencil} label="Edit" variant="primary" priority="primary" />
      </HeaderActions>,
    )
    expect(row()).toEqual(['Pin', 'More actions', 'Edit'])
  })

  it('a primary menu too, and with no `…` it is still last', () => {
    render(
      <HeaderActions>
        <HeaderMenu icon={Plus} label="New" variant="primary" priority="primary" items={[{ label: 'New task', onSelect: () => {} }]} />
        <HeaderControl icon={Pin} label="Pin" />
      </HeaderActions>,
    )
    expect(row()).toEqual(['Pin', 'New'])
  })

  it('draws once, and only in the live row', () => {
    const { container } = render(
      <HeaderActions>
        <HeaderControl icon={Pencil} label="Edit" variant="primary" priority="primary" />
      </HeaderActions>,
    )
    expect(container.querySelector('[data-header-trailing]')?.textContent).toBe('Edit')
    // The three probes each hold their own copy in place, for the widths.
    expect([...container.querySelectorAll('[aria-hidden] button')].map((b) => b.textContent)).toEqual(['Edit', 'Edit', ''])
  })
})
