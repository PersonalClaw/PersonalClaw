// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { BookOpen, FileText, Sparkles } from 'lucide-react'
import { HeaderActions, HeaderControl, inRowOrder } from './HeaderActions'

// ── The cluster reads its members in the row's order ─────────────────────────────────────────────
//
// The cluster kept its members in the order they registered, and read each one's width off the
// probes' children by that index. A member re-registers when what it shows changes (a filter's
// label on each choice) and lands at the end, and one that mounts later than those beside it does
// too, so the index named another control's width, and the `…` listed its rows out of the row's
// order (Knowledge's read Sources, Reports, Regenerate where the row reads Regenerate, Sources,
// Reports). Each member is marked with its name in the probes now (each probe draws its own copy,
// with an id of its own), and both read the DOM.

function Row({ late, sources }: { late: boolean; sources: string }) {
  return (
    <HeaderActions>
      {late && <HeaderControl icon={Sparkles} label="Regenerate" inOverflow />}
      <HeaderControl icon={BookOpen} label={sources} inOverflow />
      <HeaderControl icon={FileText} label="Reports" inOverflow />
    </HeaderActions>
  )
}

const menuRows = () => {
  fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
  return screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.textContent?.trim())
    .filter((n) => n !== 'More actions')
}

describe('the cluster and the row order', () => {
  it('lists the `…` in the row order after a member mounts late and another re-registers', () => {
    const { rerender } = render(<Row late={false} sources="Sources" />)
    rerender(<Row late sources="Sources" />)
    rerender(<Row late sources="Sources (2)" />)
    expect(menuRows()).toEqual(['Regenerate', 'Sources (2)', 'Reports'])
  })

  it('orders by the place the DOM gives, and keeps registration order for one not yet placed', () => {
    const regs = [{ id: 7 }, { id: 3 }, { id: 9 }, { id: 4 }]
    const place = new Map([[3, 0], [7, 1], [9, 2]])
    expect(inRowOrder(regs, place).map((r) => r.id)).toEqual([3, 7, 9, 4])
  })
})
