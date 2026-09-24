import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The onboarding stepper tells assistive tech which step you're on, and when it changes ──────
//
// `#/onboarding` is the first screen a new user meets and it had never been audited (it is a route
// guard, not in `scripts/surfaces.json`, so no axe rail or baseline touched it). The step indicator
// carried no `aria-current`, and step transitions had no live region — so a screen-reader user was
// never told which of the three steps (name → essentials → ready) was live or that they had advanced.
// The rows are `<div>`s (not focusable) and the active step's title is not in any focused control's
// accessible name, so neither piece could be inferred another way. Two WAI-ARIA fixes, both asserted
// here:
//
//   · `aria-current="step"` on the ACTIVE StepRow (which step is current), and
//   · a polite `role="status"` live region whose text is `Step N of M: <title>`, updated on every
//     `step` change so the transition is announced (WCAG 4.1.3).

const SRC = join(process.cwd(), 'src')
const onboarding = () => readFileSync(join(SRC, 'app/Onboarding.tsx'), 'utf8')
const stepRow = () => readFileSync(join(SRC, 'app/onboarding/StepStack.tsx'), 'utf8')

describe('onboarding step progress is announced', () => {
  it('the active step is marked aria-current="step"', () => {
    // On the row primitive, gated to the active state — an always-on or missing value both fail AT.
    expect(stepRow()).toMatch(/aria-current=\{active \? 'step' : undefined\}/)
  })

  it('a polite live region announces the current step, with its number and title', () => {
    const src = onboarding()
    expect(src, 'a status live region must exist').toMatch(/role="status" aria-live="polite"/)
    // It must carry the step NUMBER (progress) and the step TITLE (what the step is), from the
    // single-source ORDER + TITLES — a bare "Step changed" would announce nothing useful.
    expect(src).toMatch(/Step \$\{ORDER\.indexOf\(step\) \+ 1\} of \$\{ORDER\.length\}: \$\{TITLES\[step\]\}/)
  })

  it('the announced title comes from the SAME source as the visible one', () => {
    // Single source, so the spoken step name cannot drift from the heading. If a title is ever
    // hardcoded back onto a StepRow, this and nameFieldLabelled both fail.
    //
    // The count is DERIVED from `ORDER`, not frozen: when OU-3 added the `try` step this rail
    // said "all three rows read from TITLES" and would have gone red for a fourth row that was
    // correctly sourced — a frozen count turns "every row" into "exactly N rows" and makes
    // adding a compliant step look like a regression. Deriving it strengthens the claim: it now
    // fails if ANY declared step's row hardcodes its title, at any number of steps.
    const src = onboarding()
    expect(src).toMatch(/const TITLES: Record<StepId, string>/)
    const steps = (src.match(/const ORDER: StepId\[\] = \[([^\]]*)\]/)?.[1] ?? '')
      .split(',').map((s) => s.trim()).filter(Boolean)
    expect(steps.length, 'ORDER must declare the steps').toBeGreaterThan(1)
    expect(
      (src.match(/title=\{TITLES\.\w+\}/g) || []).length,
      `all ${steps.length} rows in ORDER must read from TITLES`,
    ).toBe(steps.length)
  })

  it('the live region is visually hidden, not visible chrome', () => {
    // It is an announcement, not a rendered step counter — the stepper already shows progress
    // visually. `sr-only` keeps it out of the layout.
    expect(onboarding()).toMatch(/role="status" aria-live="polite" className="sr-only"/)
  })
})
