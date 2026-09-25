import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { MessageUser, LONG_MESSAGE_CHARS, LONG_MESSAGE_LINES } from './MessageUser'

// A very long user message must not push its own turn's outcome off-screen (ootb `c2-25`):
// a 60,014-character paste, sent as typed text, rendered as a ~21,000px bubble and the turn's
// error landed below it. It opens FOLDED — never truncated: the whole text stays in the DOM.

const LOG = Array.from({ length: 2340 }, (_, i) => `2026-09-25T10:51:57Z build[${i}] step ${i}: compiling module beds_${i}.py`).join('\n')

describe('a long user message', () => {
  it('opens folded, with the whole text still present, and one click shows it', async () => {
    const user = userEvent.setup()
    const { container } = render(<MessageUser>{LOG}</MessageUser>)

    const toggle = screen.getByRole('button', { name: /Show full message · 2,340 lines/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    const body = container.querySelector(`#${CSS.escape(toggle.getAttribute('aria-controls')!)}`)!
    expect(body.getAttribute('data-user-message-folded')).toBe('true')
    expect(body.className).toMatch(/max-h-/)
    // Folded, not cut: copy, select and screen readers still get every line.
    expect(body.textContent).toContain('beds_2339.py')

    await user.click(toggle)
    expect(screen.getByRole('button', { name: 'Show less' }).getAttribute('aria-expanded')).toBe('true')
    expect(body.getAttribute('data-user-message-folded')).toBeNull()
    expect(body.className).not.toMatch(/max-h-/)
  })

  it('folds one enormous line by its length', () => {
    render(<MessageUser>{'x'.repeat(LONG_MESSAGE_CHARS + 1)}</MessageUser>)
    expect(screen.getByRole('button', { name: /Show full message · 1,501 characters/ })).toBeTruthy()
  })

  it('leaves an ordinary message alone', () => {
    const text = Array.from({ length: LONG_MESSAGE_LINES }, (_, i) => `line ${i}`).join('\n')
    render(<MessageUser>{text}</MessageUser>)
    expect(screen.queryByRole('button', { name: /Show full message/ })).toBeNull()
  })
})
