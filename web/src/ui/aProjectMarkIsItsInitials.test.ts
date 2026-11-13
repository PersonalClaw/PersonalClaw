import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { render } from '@testing-library/react'
import { ProjectMark, projectInitials } from './ProjectMark'

// ── A project's mark is its initials ───────────────────────────────────────────────────────────
//
// One word gives its first letter, two or more the first letters of the first two words, upper
// case. A neutral tile with no hue, hidden from a screen reader, since the name beside it is what
// it says. Two sizes: `md` for list rows and the page title, `sm` for chips and picker rows.

describe('a project mark', () => {
  it('is the first letter of one word, and of the first two words of more', () => {
    expect(projectInitials('Garden')).toBe('G')
    expect(projectInitials('Homelab')).toBe('H')
    expect(projectInitials('Personal')).toBe('P')
    expect(projectInitials('Trip planning')).toBe('TP')
    expect(projectInitials('Taxes 2026')).toBe('T2')
    expect(projectInitials('  spring   beds and paths ')).toBe('SB')
    expect(projectInitials('')).toBe('')
  })

  it('is hidden from a screen reader, on a neutral tile, at its two sizes', () => {
    const { container } = render(createElement('div', null,
      createElement(ProjectMark, { name: 'Trip planning' }),
      createElement(ProjectMark, { name: 'Garden', size: 'sm' })))
    const [md, sm] = [...container.querySelectorAll('span')]
    expect(md.getAttribute('aria-hidden')).toBe('true')
    expect(md.textContent).toBe('TP')
    expect(md.className).toContain('size-7')
    expect(sm.textContent).toBe('G')
    expect(sm.className).toContain('size-[18px]')
    for (const mark of [md, sm]) {
      expect(mark.className, 'a step above the chip it sits on').toContain('bg-surface-highest')
      expect(mark.className).toContain('text-on-surface-var')
      expect(mark.className, 'no hue').not.toMatch(/\b(bg|text)-(primary|success|error|warn|danger)/)
    }
  })
})
