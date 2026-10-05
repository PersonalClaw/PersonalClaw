import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ModelPill } from './controls'

// ── The chip that stated a number nobody supplied ──────────────────────────────────────
//
// The backend emitted a `context_usage` frame every turn with
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

const pill = (contextPct?: number, contextWindow?: number | null) => (
  <ModelPill data={undefined} agent="" value="Auto" onSelect={vi.fn()} contextPct={contextPct} contextWindow={contextWindow} />
)
const titleOf = (el: ReturnType<typeof pill>) => render(el).container.querySelector('[title]')!.getAttribute('title')!

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

// ── …and the unmeasured dot SAYS why it is unmeasured (#3406) ──────────────────────────
//
// Staying silent was only half honest: a bare dot sits one pixel away from a nearly-empty ring,
// so a user could not tell "unknown" from "barely any context used". But an unmeasured reading
// has more than one cause, and the dot used to give the same one for all of them — "no context
// window is declared for this model". A chat whose local runtime served a 32,768-token window
// said that after a `/compact` on a runtime that had not answered yet and again after a restart
// at the threshold, while the chat itself read "Auto-compacted at 71% of the context window". The
// gateway now names the window with each reading (`null` when nothing declared or served one),
// and the remedy is offered only when that is the case.

describe('the unmeasured dot explains itself', () => {
  it('names the control that fixes it when the gateway says no window is known', () => {
    const title = titleOf(pill(undefined, null))
    expect(title.toLowerCase()).toContain('unknown')
    // The exact label of the field on the provider form (ModelBackends.tsx) and where it
    // lives. A rename there without a rename here leaves a user hunting for a control that
    // does not exist under that name.
    expect(title).toContain('Served context window')
    expect(title).toContain('Settings')
  })

  it('says it is not measured yet, and names the window, when the model has one', () => {
    const title = titleOf(pill(undefined, 32768))
    expect(title).toContain('not measured yet')
    expect(title).toContain('32,768-token window')
    expect(title).not.toContain('declared')
  })

  it('says it is not measured yet before the gateway has said anything', () => {
    const title = titleOf(pill(undefined))
    expect(title).toContain('not measured yet')
    expect(title).not.toContain('declared')
  })

  it('still states no percentage — explaining is not fabricating', () => {
    for (const w of [undefined, null, 32768]) {
      const { container } = render(pill(undefined, w))
      expect(container.innerHTML).not.toContain('%')
      expect(container.querySelector('[title^="Context:"]')).toBeNull()
    }
  })

  it('a measured ring does NOT claim unknown', () => {
    // The paired control: if the two states ever collapse onto one title, this reds.
    const measured = titleOf(pill(61.5, 32768))
    expect(measured.toLowerCase()).not.toContain('unknown')
    expect(measured).not.toContain('not measured')
  })
})
