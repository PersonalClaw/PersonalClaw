// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PlanStreamReview } from './PlanStreamReview'

// ── WF2UNI-10: plan review streams progressively across cards + graph + JSON ────────────────────
//
// planStream/planGraph/planNaming are unit-tested as pure modules; this mounts the COMPONENT that
// assembles them and asserts the clause's render: the three synchronized views (Proposal / Graph /
// JSON), the JSON view showing the raw buffer verbatim, and the in-flight shimmer while streaming.

const BUFFER = '{"title":"Build the thing","steps":[{"id":"a","title":"Alpha"},{"id":"b","title":"Beta"}]}'

describe('PlanStreamReview — three synchronized views (WF2UNI-10 render)', () => {
  it('renders the three view toggles and the proposal cards (with per-step labels) by default', () => {
    render(<PlanStreamReview buffer={BUFFER} complete={true} />)
    expect(screen.getByRole('radio', { name: 'Proposal' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Graph' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'JSON' })).toBeInTheDocument()
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    expect(screen.getByText('2 steps')).toBeInTheDocument()
  })

  it('the JSON view shows the raw buffer verbatim (authoritative, not the reparse)', () => {
    render(<PlanStreamReview buffer={BUFFER} complete={true} />)
    fireEvent.click(screen.getByRole('radio', { name: 'JSON' }))
    expect(screen.getByText((t) => t.includes('"title":"Build the thing"'))).toBeInTheDocument()
  })

  it('the graph view renders a dependency graph when selected', () => {
    const { container } = render(<PlanStreamReview buffer={BUFFER} complete={true} />)
    fireEvent.click(screen.getByRole('radio', { name: 'Graph' }))
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('shows the in-flight shimmer while streaming and stops it once complete', () => {
    const { rerender, container } = render(<PlanStreamReview buffer={BUFFER} complete={false} />)
    expect(screen.getByText('Planning…')).toBeInTheDocument()
    expect(container.querySelector('.text-shimmer')).not.toBeNull()
    rerender(<PlanStreamReview buffer={BUFFER} complete={true} />)
    expect(screen.queryByText('Planning…')).toBeNull()
    expect(container.querySelector('.text-shimmer')).toBeNull()
  })
})
