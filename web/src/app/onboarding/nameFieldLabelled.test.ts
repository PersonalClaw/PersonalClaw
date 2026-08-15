import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The first control a new user meets must have a real, programmatic label ────────────────────
//
// `#/onboarding` is the first screen every new user sees, and it is not in `scripts/surfaces.json`
// (a route guard, not a nav destination), so no `ux-audit`, no axe rail and no baseline ever touched
// it. The name `<input>` had no `<label>`, no `aria-label`, no `aria-labelledby` — its ONLY accessible
// name was its `placeholder`, which vanishes on the first keystroke and is unreliably announced
// (WCAG 1.3.1 / 3.3.2 / 4.1.2).
//
// The fix is a field-local `aria-label` matching the visible StepRow title ("Your name") verbatim — so
// the accessible name is stable AND equals the visible label (no 2.5.3 Label-in-Name conflict). The
// visible title now flows from the single-source `TITLES.name`, so this rail asserts the input's
// accessible name against that same source rather than a hardcoded literal that could drift.

const SRC = join(process.cwd(), 'src')
const onboarding = () => readFileSync(join(SRC, 'app/Onboarding.tsx'), 'utf8')

describe('the onboarding name field is programmatically labelled', () => {
  it('the input carries a real label, not just a placeholder', () => {
    const src = onboarding()
    // Find the name <input> block and assert it has aria-label / aria-labelledby.
    const inputTag = src.match(/<input[\s\S]*?placeholder="Your name"[\s\S]*?\/>/)?.[0] ?? ''
    expect(inputTag, 'the name input must exist').toContain('placeholder="Your name"')
    expect(
      /aria-label=|aria-labelledby=/.test(inputTag),
      'the name input must have a programmatic label, not placeholder-only',
    ).toBe(true)
  })

  it('the accessible name matches the visible title source (no Label-in-Name conflict)', () => {
    const src = onboarding()
    // The visible label is the StepRow title, sourced from TITLES.name; the input's aria-label must
    // read the same words so what is spoken equals what is shown.
    expect(src, 'TITLES.name is the visible title').toMatch(/name: 'Your name'/)
    expect(src, 'the input names itself the same').toMatch(/aria-label="Your name"/)
  })

  it('the matcher would fail on the placeholder-only shape it replaced', () => {
    // Sabotage: the exact markup that shipped before this fix must NOT pass.
    const before = '<input autoFocus placeholder="Your name" className="…" />'
    const tag = before.match(/<input[\s\S]*?placeholder="Your name"[\s\S]*?\/>/)?.[0] ?? ''
    expect(/aria-label=|aria-labelledby=/.test(tag)).toBe(false)
  })
})
