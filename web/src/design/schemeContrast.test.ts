import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'

// ── Scheme contrast guard (design-system consistency) ──────────────────────
// PRODUCT.md mandates WCAG 2.1 AA. The default 'coral' scheme's AA was verified
// numerically in tokenRegistry.ts and is enforced live by e2e/a11y.spec.ts —
// but axe only ever exercises the DEFAULT scheme. The other ten named schemes
// (honey/jade/ember/forest/rose/amber/…) carried pre-AA light-mode accents that
// no rail caught. This test closes that hole STRUCTURALLY: it iterates EVERY
// scheme and asserts each one's accent tokens meet AA in the contexts where they
// actually bear text/UI, in BOTH modes. A new scheme (or a regressed edit to an
// existing one) that dips below AA turns the build red — no scheme can ship
// below the bar the default is held to.

// WCAG 2.1 relative luminance + contrast ratio (sRGB).
function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  const hi = Math.max(la, lb), lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}

// AA for normal-size text / meaningful UI. Accent chips, links, and small labels
// are all ≤ large-text size, so 4.5:1 is the applicable threshold.
const AA = 4.5

// The dark neutral surface accent text realistically sits on. Kept in sync with
// tokens.css (--color-surface-container, dark block) — read from source so a
// token retint can't silently drift the guard. surface-highest is intentionally
// NOT the reference: even the AA-verified default coral is 4.4:1 there, so it's
// not a text-bearing accent background — testing it would invent a failure the
// default itself doesn't meet.
function darkSurfaceContainer(): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  // First occurrence = the default (dark) :root block, before the .light override.
  const m = css.match(/--color-surface-container:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error('could not find --color-surface-container in tokens.css')
  return m[1]
}

const WHITE = '#ffffff'

describe('scheme contrast: every scheme meets WCAG AA (not just the default)', () => {
  const DARK_SURFACE = darkSurfaceContainer()

  it('has the full curated scheme set', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
  })

  for (const s of SCHEMES) {
    const c = s.colors
    const primary = c['--color-primary']
    const emphasis = c['--color-primary-emphasis']
    const onPrimary = c['--color-on-primary']
    const info = c['--color-info']

    describe(`scheme '${s.id}'`, () => {
      // LIGHT — the dimension that was broken. primary/emphasis are used both as
      // a filled control (white/onPrimary text over the accent) and as accent
      // TEXT on the white surface; info is accent text on white.
      it('light: primary as filled control (onPrimary over primary) ≥ AA', () => {
        expect(contrast(primary.light, onPrimary.light)).toBeGreaterThanOrEqual(AA)
      })
      it('light: primary as accent text on white ≥ AA', () => {
        expect(contrast(primary.light, WHITE)).toBeGreaterThanOrEqual(AA)
      })
      it('light: emphasis (hover fill) over onPrimary ≥ AA', () => {
        expect(contrast(emphasis.light, onPrimary.light)).toBeGreaterThanOrEqual(AA)
      })
      it('light: info as accent text on white ≥ AA', () => {
        expect(contrast(info.light, WHITE)).toBeGreaterThanOrEqual(AA)
      })

      // DARK — already AA before this cycle; guard against regression. primary as
      // accent text on the neutral dark container (the common accent-text ground).
      it('dark: primary as accent text on surface-container ≥ AA', () => {
        expect(contrast(primary.dark, DARK_SURFACE)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: info as accent text on surface-container ≥ AA', () => {
        expect(contrast(info.dark, DARK_SURFACE)).toBeGreaterThanOrEqual(AA)
      })
    })
  }
})
