import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ModelPill } from './controls'

// ── The chip that stated a number nobody supplied ──────────────────────────────────────
//
// ACP-AGENT-PARITY G8/O7: the backend emitted a `context_usage` frame every turn with
// `pct: 0.0` and the composer's model pill drew its ring from it, so the surface asserted
// "Context: 0% used" on turns that were carrying 18 KB of injected context. The producer
// could not say "unknown" — `AcpPromptStats.context_pct` was a bare defaulted float.
//
// Now `contextPct` is genuinely optional and the backend sends `pct: null` when it measured
// nothing, so the two answers are distinct and must RENDER distinctly:
//
//   undefined (unmeasured)  → plain dot, and NO percentage anywhere in the markup
//   0         (measured)    → the ring, reading "Context: 0% used"
//
// 🪤 The guard this replaced was `contextPct !== undefined && contextPct > 0`. The `> 0`
// half was the only thing hiding the fabricated ring while the Python side could not say
// "unknown" — but it is the INVERSE defect once it can: it folds a legitimately empty
// context into "unmeasured" and hides a real answer. Both directions are asserted below,
// and the last test asserts they DISAGREE, so a future collapse in either direction reds.

const pill = (contextPct?: number) => (
  <ModelPill data={undefined} agent="" value="Auto" onSelect={vi.fn()} contextPct={contextPct} />
)

describe('the context ring never states an unmeasured percentage', () => {
  it('renders no percentage at all when the backend measured nothing', () => {
    const { container } = render(pill(undefined))
    // Asserted on the rendered surface, not on a prop: the ring's only text is the
    // title attribute, so the whole markup must be free of a "Context: N%" claim.
    expect(container.querySelector('[title^="Context:"]')).toBeNull()
    expect(container.innerHTML).not.toContain('%')
  })

  it('renders a 0% ring when the context was measured and is empty', () => {
    render(pill(0))
    expect(screen.getByTitle('Context: 0% used')).toBeTruthy()
  })

  it('renders the measured value when there is one', () => {
    render(pill(61.5))
    expect(screen.getByTitle('Context: 62% used')).toBeTruthy()
  })

  it('unmeasured and measured-zero produce different markup', () => {
    const unmeasured = render(pill(undefined)).container.innerHTML
    const measuredZero = render(pill(0)).container.innerHTML
    expect(unmeasured).not.toEqual(measuredZero)
  })
})
