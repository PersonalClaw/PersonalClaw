import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Button } from './Button'
import { ButtonLink } from './ButtonLink'

// ── A link drawn as a button ─────────────────────────────────────────────────────────────────────
// A navigation that is a row's one action is a real <a href>, and it wears exactly the Button's look:
// the same classes and type role for the same variant, size and shape, so the two can't drift.

const classes = (el: Element) => new Set(el.className.split(/\s+/).filter(Boolean))

describe('ButtonLink', () => {
  it('is a real link to its address, not a button', () => {
    const { getByRole, queryByRole } = render(<ButtonLink href="#/dashboard?view=attention">Answer in Attention</ButtonLink>)
    const a = getByRole('link', { name: 'Answer in Attention' })
    expect(a.tagName).toBe('A')
    expect(a).toHaveAttribute('href', '#/dashboard?view=attention')
    expect(queryByRole('button')).toBeNull()
    expect(a).not.toHaveAttribute('target')
  })

  it.each([
    ['tonal', 'sm', 'pill'],
    ['primary', 'md', 'pill'],
    ['secondary', 'xs', 'squircle'],
  ] as const)('wears the Button look for %s, %s, %s', (variant, size, shape) => {
    const { getByRole } = render(
      <>
        <Button variant={variant} size={size} shape={shape}>Same</Button>
        <ButtonLink href="#/x" variant={variant} size={size} shape={shape}>Same</ButtonLink>
      </>,
    )
    const button = getByRole('button')
    const link = getByRole('link')
    const theButtons = classes(button)
    // The button's own states are its alone: a link has no disabled or loading look.
    const state = /^(disabled:|aria-disabled:)/
    const shared = [...theButtons].filter((c) => !state.test(c))
    expect(shared.filter((c) => !classes(link).has(c))).toEqual([])
    expect([...classes(link)].filter((c) => !theButtons.has(c))).toEqual([])
    expect(link.getAttribute('data-type')).toBe(button.getAttribute('data-type'))
  })

  it('opens an off-app address in a new tab, safely', () => {
    const { getByRole } = render(<ButtonLink href="https://example.com" external>Open</ButtonLink>)
    const a = getByRole('link')
    expect(a).toHaveAttribute('target', '_blank')
    expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('takes a name when its label does not say where it goes', () => {
    const { getByRole } = render(<ButtonLink href="#/a" ariaLabel="Answer the ask in Attention">Answer</ButtonLink>)
    expect(getByRole('link', { name: 'Answer the ask in Attention' })).toBeTruthy()
  })
})
