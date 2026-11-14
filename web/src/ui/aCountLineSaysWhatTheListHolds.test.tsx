import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ListStatus } from './ListControls'

// ── A list's count line says what the list holds ─────────────────────────────────────────────────
//
// The Projects list draws "6 projects" under its title, and while you search, "2 projects and
// 4 chats match". `ListStatus` drew nothing visible while the rows were fresh, so the line is an
// opt-in `line`: visible text and no control, every control being in the header. While the list is
// narrowed its sr-only announcement says the line's own words, so a screen reader hears what a
// sighted reader sees; at rest the announcement stays empty, as before, and a page that passes no
// `line` draws what it always drew.

describe("a list's count line", () => {
  it('draws its text, visible, and holds no control', () => {
    const { container } = render(<ListStatus results={{ count: 6, noun: 'projects', active: false }} line="6 projects" />)
    const line = container.querySelector('[data-list-line]')
    expect(line?.textContent).toBe('6 projects')
    expect(line?.closest('.sr-only')).toBeNull()
    expect(container.querySelectorAll('button, a, input, [role="button"]')).toHaveLength(0)
    expect(screen.getByRole('status').textContent, 'at rest the announcement stays empty').toBe('')
  })

  it('without a line draws nothing visible while the rows are fresh, as before', () => {
    const { container } = render(<ListStatus results={{ count: 6, noun: 'projects', active: false }} />)
    expect(container.querySelector('[data-list-line]')).toBeNull()
    for (const el of container.querySelectorAll('*')) expect(el.closest('.sr-only')).not.toBeNull()
  })

  it("while the list is narrowed, the announcement says the line's own words", () => {
    render(<ListStatus results={{ count: 6, noun: 'rows', active: true }} line="2 projects and 4 chats match" />)
    const status = screen.getByRole('status')
    expect(status.className).toContain('sr-only')
    expect(status.textContent).toBe('2 projects and 4 chats match')
  })

  it('without a line, a narrowed list still announces its count', () => {
    render(<ListStatus results={{ count: 2, noun: 'projects', active: true }} />)
    expect(screen.getByRole('status').textContent).toBe('2 projects')
  })
})
