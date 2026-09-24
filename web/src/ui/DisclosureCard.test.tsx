import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Globe } from 'lucide-react'
import { DisclosureCard } from './DisclosureCard'

// The card was extracted from two byte-identical copies (`settings/ModelsPanel` and
// `settings/SearchPanel`). These assert the behaviour the copies had plus the two things neither of
// them did: `aria-controls` naming the region, and a pill that disappears rather than reading "0".

describe('DisclosureCard', () => {
  it('starts collapsed and does not render the body', () => {
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={3}>
        <p>body content</p>
      </DisclosureCard>,
    )
    expect(screen.getByRole('button', { name: /General search/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('body content'), 'a collapsed card must not mount its body').toBeNull()
  })

  it('discloses the body on click and flips aria-expanded', async () => {
    const user = userEvent.setup()
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={3}>
        <p>body content</p>
      </DisclosureCard>,
    )
    const header = screen.getByRole('button', { name: /General search/ })
    await user.click(header)
    expect(header).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('body content')).toBeInTheDocument()
  })

  it('points aria-controls at the region it actually discloses', async () => {
    // 🔑 NEITHER ORIGINAL COPY DID THIS. `aria-expanded` alone says "this thing expands" without
    // saying WHAT, so a screen reader could not move from the control to the region it governs.
    const user = userEvent.setup()
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={3}>
        <p>body content</p>
      </DisclosureCard>,
    )
    const header = screen.getByRole('button', { name: /General search/ })
    const controls = header.getAttribute('aria-controls')
    expect(controls, 'the header must name its region').toBeTruthy()
    await user.click(header)
    const body = document.getElementById(controls!)
    expect(body, 'and that id must resolve to the disclosed body').not.toBeNull()
    expect(body!.textContent).toContain('body content')
  })

  it('gives two cards on one page distinct region ids', () => {
    // A hardcoded id would collide the moment a panel renders more than one card — which is the
    // normal case at both call sites (four search use-cases, sixteen model use-cases).
    render(
      <>
        <DisclosureCard icon={Globe} label="First" subtitle="a" active={false}><p>one</p></DisclosureCard>
        <DisclosureCard icon={Globe} label="Second" subtitle="b" active={false}><p>two</p></DisclosureCard>
      </>,
    )
    const a = screen.getByRole('button', { name: /First/ }).getAttribute('aria-controls')
    const b = screen.getByRole('button', { name: /Second/ }).getAttribute('aria-controls')
    expect(a).not.toBe(b)
  })

  it('is keyboard-operable, because the header is a real button', async () => {
    const user = userEvent.setup()
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active={false}>
        <p>body content</p>
      </DisclosureCard>,
    )
    await user.tab()
    const header = screen.getByRole('button', { name: /General search/ })
    expect(header).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(header).toHaveAttribute('aria-expanded', 'true')
  })

  it('draws its focus ring INSIDE the card, because the card clips', () => {
    // 🔴 THE DEFECT BOTH COPIES SHIPPED. The header fills an `overflow-hidden rounded-lg` card, so the
    // global rail's outward `outline-offset: 2px` drew the whole ring outside the clip and nothing
    // painted — no visible focus indicator at all (WCAG 2.4.7). The negative offset is the fix, and it
    // is asserted here so a future tidy-up of the class list cannot silently drop it.
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active={false}>
        <p>body</p>
      </DisclosureCard>,
    )
    const header = screen.getByRole('button', { name: /General search/ })
    expect(header.className).toContain('focus-visible:-outline-offset-2')
  })

  it('renders the count pill when there is something to offer', () => {
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={7}>
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.getByText('7 available')).toBeInTheDocument()
  })

  it('suppresses the pill at zero rather than announcing "0 available"', () => {
    // A card with nothing to offer says so in its BODY ("No search providers configured. Add one in
    // Providers first."), which is actionable. "0 available" in the header is technically true and
    // tells the user nothing about what to do.
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="none" active={false} count={0}>
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.queryByText(/available/)).toBeNull()
  })

  it('takes a custom count noun', () => {
    render(
      <DisclosureCard icon={Globe} label="X" subtitle="y" active count={2} countLabel="bound">
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.getByText('2 bound')).toBeInTheDocument()
  })

  it('accepts a node subtitle, so an unbound state can have its own voice', () => {
    render(
      <DisclosureCard icon={Globe} label="News search"
        subtitle={<span className="italic">none — falls back to General</span>} active={false}>
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.getByText('none — falls back to General')).toBeInTheDocument()
  })
})
