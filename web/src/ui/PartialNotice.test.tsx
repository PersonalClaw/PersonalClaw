/**
 * #485 — the fourth load-state: a successful read that returned part of the data.
 *
 * The bound is not the defect; the silence is. `/api/tasks` served 50 rows and the page rendered
 * a dependency graph from them, dropping every edge to an id it did not hold — so a blocked task
 * drew as a clean unblocked node with nothing on screen saying the view was a fragment.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { PartialNotice } from './PartialNotice'

describe('PartialNotice', () => {
  it('renders NOTHING when the read was complete', () => {
    // Self-gating, like StaleNotice's `stale` and MoreRow's `total <= shown`: a call site passes
    // what its loader returned and cannot forget the `&&`.
    const { container } = render(
      <PartialNotice complete shown={26} total={26} what="tasks" />
    )
    expect(container.textContent).toBe('')
    expect(container.querySelector('[data-partial]')).toBeNull()
  })

  it('states both numbers when it is not', () => {
    render(<PartialNotice complete={false} shown={10000} total={12431} what="tasks" />)
    const el = screen.getByRole('status')
    // Thousands separators: "Showing 10000 of 12431" is the kind of number a reader has to count.
    expect(el.textContent).toContain('10,000')
    expect(el.textContent).toContain('12,431')
    expect(el.textContent).toContain('tasks')
  })

  it('carries the derived consequence, not just the row count', () => {
    // 🔑 The reason this is not `MoreRow`. Hidden rows are the small half; the large half is that
    // anything computed FROM the set is now wrong, and only the caller knows what that is here.
    render(
      <PartialNotice complete={false} shown={50} total={300} what="tasks"
        detail="dependencies on the ones outside this window are not shown" />
    )
    expect(screen.getByRole('status').textContent).toMatch(/dependencies on the ones outside/)
  })

  it('is a polite status, not an alert', () => {
    // The read SUCCEEDED. `LoadError` interrupts because a failed read changes what the screen
    // means; a true statement about a working screen does not.
    render(<PartialNotice complete={false} shown={1} total={2} what="tasks" />)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('status').getAttribute('data-partial')).toBe('true')
  })

  it('reads as a fragment even with no detail to add', () => {
    cleanup()
    render(<PartialNotice complete={false} shown={1} total={2} what="notes" />)
    expect(screen.getByRole('status').textContent).toBe('Showing 1 of 2 notes')
  })
})
