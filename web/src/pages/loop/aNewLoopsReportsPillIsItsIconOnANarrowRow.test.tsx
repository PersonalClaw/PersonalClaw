import { describe, it, expect, afterEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { LoopReportPill } from './LoopReportPill'

// ── A new loop's Reports pill is its icon below 768 px ──────────────────────────────────────────
//
// The Reports pill (1e22fdd9, #3824) took the new loop's composer past one row at 600 px: Runs on,
// the Execution Boundary and the pill measured 482 px against the 412 px beside the send cluster,
// so Send wrapped to a second row (`e2e/composerFooterGeometry.spec.ts`, #3905). Below 768 px the
// composer's pill is now its bell and chevron, and its value stays in its name and its title. The
// loop strip's pill, on a loop already running, keeps its value at every width.
//
// jsdom has no layout, so the class that hides the value is pinned; the e2e spec measures the row.

afterEach(() => cleanup())

const value = (button: HTMLElement) => [...button.querySelectorAll('span')].find((s) => s.textContent === 'Only when it’s done')!

describe('a new loop\'s Reports pill', () => {
  it('hides its value below 768 px, and keeps it in its name and title', () => {
    render(<LoopReportPill value="when_done" onChange={() => {}} iconBelowMd />)
    const pill = screen.getByRole('button', { name: 'Reports: Only when it’s done' })
    expect(pill.getAttribute('title')).toBe('Reports: Only when it’s done')
    expect(value(pill).className.split(/\s+/)).toContain('max-md:hidden')
  })

  it('shows it at every width where it is not asked to', () => {
    render(<LoopReportPill value="when_done" onChange={() => {}} />)
    const pill = screen.getByRole('button', { name: 'Reports: Only when it’s done' })
    expect(value(pill).className.split(/\s+/)).not.toContain('max-md:hidden')
  })
})
