import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The light theme's `color-scheme` must actually reach the browser ─────────────────────────────
//
// `color-scheme` is what tells the browser how to paint the chrome IT owns and CSS cannot reach:
// scrollbars, `<select>` dropdown popups, `<input type="date">` pickers, autofill highlighting, and
// form-control defaults. Get it wrong and a light page grows dark scrollbars and dark native menus.
//
// `.light` declared `color-scheme: light` — and it was dead. A bare `:root { color-scheme: dark }`
// sat AFTER it in the same file, and `:root` and `.light` have the SAME specificity (0,1,0), so
// source order alone decided it. Measured on a live gateway before the fix:
//
//     light   <html class="light" data-mode="light">   body rgb(240,244,248)   computed: dark  ✗
//     dark    <html data-mode="dark">                  body rgb(15,15,15)      computed: dark  ✓
//
// i.e. the light theme had never once got its own colour scheme. The fix scopes the default to
// `:root:not(.light)` so it cannot out-rank the light block — deliberately order-independent,
// because the original bug was purely positional and a future reordering would otherwise
// silently reintroduce it.

const tokens = () => readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
/** Tokens with `/* … *\/` comments stripped. The rules below count DECLARATIONS, and the comment
 *  explaining this fix quotes `color-scheme: light` / `dark` as prose — counting those would make
 *  the rail fail on its own documentation, which is exactly how a ratchet learns to lie. */
const decls = () => tokens().replace(/\/\*[\s\S]*?\*\//g, '')

describe('color-scheme follows the theme', () => {
  it('the light block still declares its own scheme', () => {
    const src = tokens()
    const light = src.slice(src.indexOf('.light {'), src.indexOf('color-scheme: light') + 40)
    expect(light, 'the .light block must own a color-scheme').toMatch(/color-scheme:\s*light/)
  })

  it('the dark default cannot out-rank the light block', () => {
    // The whole defect: a bare `:root` selector. It must exclude the light theme, so the two rules
    // can never race on source order again.
    const src = decls()
    expect(src, 'the dark default must be scoped away from .light').toMatch(
      /:root:not\(\.light\)\s*\{\s*color-scheme:\s*dark/,
    )
    expect(
      /(^|\n):root\s*\{\s*color-scheme:\s*dark/.test(src),
      'a bare `:root { color-scheme: dark }` overrides .light on source order — that was the bug',
    ).toBe(false)
  })

  it('exactly one rule declares each scheme, so there is no second race', () => {
    const src = decls()
    expect((src.match(/color-scheme:\s*light/g) || []).length, 'one light declaration').toBe(1)
    expect((src.match(/color-scheme:\s*dark/g) || []).length, 'one dark declaration').toBe(1)
  })

  it('the light selector really is a class, which is why specificity tied', () => {
    // Vacuity guard: the reasoning above depends on `.light` being a class (0,1,0). If the theme
    // ever moves to `[data-mode="light"]` or an id, this rail's premise changes and it should be
    // re-derived rather than left asserting a stale cascade story.
    expect(tokens()).toMatch(/(^|\n)\.light\s*\{/)
  })
})
