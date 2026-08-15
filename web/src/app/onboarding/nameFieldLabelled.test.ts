import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The first control a new user meets must have a real, programmatic label ────────────────────
//
// `#/onboarding` is the first screen every new user sees, and the loop had never audited it — it is
// not in `scripts/surfaces.json` (a route guard, not a nav destination), so no `ux-audit`, no axe rail
// and no baseline ever touched it. Driven live (the dev home was onboarding-locked this cycle, so it
// was the one surface reachable): the name `<input>` reported `hasLabel: false` — no `<label>`, no
// `aria-label`, no `aria-labelledby`. Its ONLY accessible name was its `placeholder`, which vanishes on
// the first keystroke and is unreliably announced (WCAG 1.3.1 / 3.3.2 / 4.1.2).
//
// The fix is a field-local `aria-label` matching the visible StepRow title ("Your name") verbatim — so
// the accessible name is stable AND equals the visible label (no 2.5.3 Label-in-Name conflict). StepRow's
// title is a generic slot with no id, so `aria-labelledby` would mean threading one through three call
// sites for the same result. This rail keeps the label from silently reverting to placeholder-only.

const SRC = join(process.cwd(), 'src')
const onboarding = () => readFileSync(join(SRC, 'app/Onboarding.tsx'), 'utf8')

describe('the onboarding name field is programmatically labelled', () => {
  it('the input carries a real label, not just a placeholder', () => {
    const src = onboarding()
    // Find the name <input> block and assert it has aria-label / aria-labelledby / an associated id.
    const inputTag = src.match(/<input[\s\S]*?placeholder="Your name"[\s\S]*?\/>/)?.[0] ?? ''
    expect(inputTag, 'the name input must exist').toContain('placeholder="Your name"')
    expect(
      /aria-label=|aria-labelledby=/.test(inputTag),
      'the name input must have a programmatic label, not placeholder-only',
    ).toBe(true)
  })

  it('the accessible name matches the visible StepRow title (no Label-in-Name conflict)', () => {
    const src = onboarding()
    // The visible label is the StepRow title; both must read "Your name".
    expect(src).toMatch(/title="Your name"/)
    expect(src).toMatch(/aria-label="Your name"/)
  })

  it('the matcher would fail on the placeholder-only shape it replaced', () => {
    // Sabotage: the exact markup that shipped before this fix must NOT pass.
    const before = '<input autoFocus placeholder="Your name" className="…" />'
    const tag = before.match(/<input[\s\S]*?placeholder="Your name"[\s\S]*?\/>/)?.[0] ?? ''
    expect(/aria-label=|aria-labelledby=/.test(tag)).toBe(false)
  })
})
