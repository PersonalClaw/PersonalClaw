import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A text field with permission to disappear ─────────────────────────────────────────────────────
//
// Measured on `#/settings/tool-output` at 390×844 — the widths are the whole finding:
//
//   New rule name        16×36    ← a sliver between the `+` and the strategy select
//   Rule name (existing) 16×36    ← the field holding the value you are editing
//   Strategy select     325×36    ← unmoved
//
// Both name inputs carried `min-w-0 flex-1`. `min-w-0` is permission to collapse to zero, which is
// exactly right for text that should TRUNCATE and exactly wrong for a control the user must still be
// able to type into. The select next to them is a `<select>` whose widest option is
// "Log — keep head + error/warning lines + tail", so its intrinsic width is ~325px and it had no
// shrink permission of its own — the flex row therefore took every pixel out of the inputs.
// `ux-audit --viewport phone` caught it as a 24px hit-target note, which is how it surfaced at all;
// the desktop run is clean, because at 1440px the same fields are 816px wide.
//
// After: `flex-wrap` on both rows + a `min-w-40` floor (the idiom `AuditPanel`'s filter field
// already uses) + `min-w-0 max-w-full` on the select so it shrinks rather than overflows when it
// lands on its own line. Measured after: 295×36 and 297×36 at 390px, select 316/318, no page
// overflow; desktop unchanged at 816/787.
//
// 🔑 WHY THIS IS A SOURCE RAIL AND NOT A GENERAL ONE. The general property — "no visible text field
// is narrower than Npx at phone width" — needs a browser, and there is no CI-run harness that can
// express it today: `web/e2e/` is not executed by CI (its own workflow comment says the a11y rail
// "mounts here once the harness auth-seed lands"), and `scripts/render_smoke.mjs` mounts five
// desktop routes (`#/dashboard`, `#/chat`, `#/projects`, `#/settings`, `#/apps`) — none of them this
// one. A rail placed in a suite CI does not run is an inert control, which is the defect class this
// repo keeps finding, so the general check stayed a measurement: swept all 74 surfaces at 390px,
// examined 119 visible text fields across 37 of them, and the narrowest field anywhere is now 80px
// ("Retention (days)", a number input that is meant to be small).

const FILE = join(process.cwd(), 'src/pages/settings/ProjectionRulesPanel.tsx')
const src = () =>
  readFileSync(FILE, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The complete opening tag of the element bearing `aria-label="<label>"`, brace-aware so a
 *  `onChange={(e) => …}` cannot end the scan early. */
function tagWithLabel(source: string, label: string): string {
  const at = source.indexOf(`aria-label="${label}"`)
  if (at < 0) return ''
  const start = source.lastIndexOf('<', at)
  let depth = 0
  for (let i = start; i < source.length; i++) {
    const c = source[i]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) return source.slice(start, i + 1)
  }
  return ''
}

describe('the rule-name fields cannot be squeezed out of existence', () => {
  it('neither name input may collapse to zero width', () => {
    const source = src()
    for (const label of ['New rule name', 'Rule name']) {
      const tag = tagWithLabel(source, label)
      expect(tag, `${label} must still exist`).not.toBe('')
      expect(tag, `${label} still permits collapse to 0 — it measured 16px at 390px`)
        .not.toMatch(/\bmin-w-0\b/)
      expect(tag, `${label} needs a usable floor`).toMatch(/\bmin-w-40\b/)
      expect(tag, `${label} should still take the free space`).toMatch(/\bflex-1\b/)
    }
  })

  it('both rows may wrap, so the select drops instead of starving the field', () => {
    const source = src()
    // Each name input's row. Anchored on the icon that opens it, not a character window.
    for (const icon of ['<Plus size={13}', '<Scissors size={13}']) {
      const at = source.indexOf(icon)
      expect(at, `${icon} must still open its row`).toBeGreaterThan(-1)
      const rowOpen = source.lastIndexOf('<div className="flex', at)
      const rowTag = source.slice(rowOpen, source.indexOf('>', rowOpen) + 1)
      expect(rowTag, `the row holding ${icon} must be allowed to wrap`).toMatch(/\bflex-wrap\b/)
    }
  })

  it('the strategy select shrinks rather than overflowing its line', () => {
    // It is the widest thing in the row (a `<select>` sized by its longest option). Once the row
    // wraps, the select can be alone on a 342px line at 390px — without shrink permission it would
    // push the container instead.
    const source = src()
    const at = source.indexOf('aria-label={forRule ?')
    expect(at, 'the StrategyPicker select must still be here').toBeGreaterThan(-1)
    const tag = source.slice(source.lastIndexOf('<select', at), source.indexOf('>', at) + 1)
    expect(tag).toMatch(/\bmin-w-0\b/)
    expect(tag).toMatch(/\bmax-w-full\b/)
  })

  it('the select and Remove wrap as ONE unit, and that unit does not compete for the line', () => {
    // Two mistakes were made here in sequence, and both are measurable, so both are pinned.
    //
    //  1. Wrapping them independently put Remove alone on a third line — and before the row could
    //     wrap at all, Remove was pushed off the card entirely at 390px, so a rule could not be
    //     deleted without widening the window.
    //  2. Grouping them as `flex-1` made the GROUP compete with the name field on one line:
    //     measured 390px → name 160 (its floor), select 98, the option text gone. An auto basis is
    //     what makes the group wrap as a unit instead: name 297, select 287 + Remove beside it.
    const source = src()
    const at = source.indexOf('aria-label={rule.name ? `Remove rule')
    expect(at, 'the Remove button must still be here').toBeGreaterThan(-1)
    const groupOpen = source.lastIndexOf('<div className="flex', at)
    const groupTag = source.slice(groupOpen, source.indexOf('>', groupOpen) + 1)
    expect(groupTag, 'Remove must sit in a group WITH the select').toMatch(/min-w-0/)
    expect(groupTag, 'and that group must not claim the line the field needs').not.toMatch(/\bflex-1\b/)
    // The select is inside that same group, not a sibling of the field.
    const selectAt = source.indexOf('<StrategyPicker value={rule.strategy}')
    expect(selectAt).toBeGreaterThan(groupOpen)
    expect(selectAt).toBeLessThan(at)
  })

  it('the regex row is deliberately NOT changed', () => {
    // Its sibling is an 80px button, not a 325px select, so at 390px that field measured 233px and
    // was never squeezed. Pinned so a later sweep does not "fix" a row that was never broken —
    // scope a change to what was measured.
    const source = src()
    const tag = tagWithLabel(source, 'Match regex for the new rule')
    expect(tag).not.toBe('')
    expect(tag, 'this one legitimately keeps min-w-0').toMatch(/\bmin-w-0\b/)
  })
})
