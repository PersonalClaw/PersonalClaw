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

/** The LIGHT canvas — the shell paints it behind every route (`background: var(--color-canvas)`), and
 *  accent text lands on it wherever a panel does not paint its own surface.
 *
 *  🔴 THE DIMENSION THIS RAIL WAS MISSING. It guaranteed `primary` as accent text against WHITE, which
 *  passes in all 12 schemes (4.83-11.37). But two sites paint accent text straight onto the canvas —
 *  the Memory Studio tab and the inbox settings link — and there the SAME token measures **4.37:1**
 *  (axe and ux-audit agree). Computed across the curated set:
 *
 *      primary → canvas          FAILS in 7 of 12 schemes (4.37-4.41)
 *      primary-emphasis → canvas PASSES in all 12          (worst 4.82, coral 6.0)
 *
 *  So the fix is the pairing the design system already ships, not a new colour: **accent TEXT on the
 *  canvas uses `--color-primary-emphasis`.** Asserted below for every scheme, so a new scheme cannot
 *  land with a canvas-illegible accent, and the two call sites carry the number in a comment. */
function lightCanvas(): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  // 🪤 Match the RULE BLOCK, not the first occurrence of the string ".light": the file mentions
  // ".light mode" in a comment 380 characters in, so slicing from `indexOf('.light')` picked up the
  // DARK `--color-canvas: #0f0f0f` and made this rail red at 2.89:1 against the wrong backdrop.
  const block = /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1]
  if (!block) throw new Error('could not find the .light rule block in tokens.css')
  const m = block.match(/--color-canvas:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error('could not find --color-canvas inside the .light block')
  return m[1]
}

describe('scheme contrast: every scheme meets WCAG AA (not just the default)', () => {
  const DARK_SURFACE = darkSurfaceContainer()
  const LIGHT_CANVAS = lightCanvas()

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
      it('light: primary-emphasis as accent text on the CANVAS ≥ AA', () => {
        // The shipped rule: on the canvas, accent text uses the emphasis shade. Plain `primary` is
        // deliberately NOT asserted here — it fails in 7 of 12 schemes, which is the finding.
        expect(contrast(emphasis.light, LIGHT_CANVAS)).toBeGreaterThanOrEqual(AA)
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

// ── The accent CONTAINER pair, across every scheme ─────────────────────────
//
// A selected chip / tinted accent surface is drawn with `--color-primary-container` as its
// background and `--color-on-primary-container` as its ink. Nothing above covered that pair, so
// it was guaranteed in exactly zero schemes — the same population gap the rest of this file exists
// to close, one token pair over.
//
// It matters more than it looks, because the two halves come from DIFFERENT places:
// every scheme supplies its own `primaryContainer`, but `--color-on-primary-container` is a single
// fixed value per mode in `tokens.css`. So the ink does NOT track the scheme. Picking a new accent
// changes the background under a constant foreground, and nothing was checking where that lands.
//
// (What sent me looking: a selected filter chip was drawing the accent as BOTH tint and ink —
// coral on 20% coral, 3.33:1 in light, axe `[serious]`. The fix moves it onto this pair, so the
// pair had better be sound in all 12 schemes rather than just the one I measured.)

/** `--color-on-primary-container` for a mode — read from tokens.css, not restated, so a retint
 *  cannot silently drift this guard. The FIRST occurrence is the default (dark) `:root` block; the
 *  one inside `.light` is the light override. */
function onPrimaryContainer(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const all = [...css.matchAll(/--color-on-primary-container:\s*(#[0-9a-fA-F]{3,8})/g)].map((m) => m[1])
  if (all.length < 2) throw new Error(`expected a dark AND a light --color-on-primary-container, found ${all.length}`)
  return mode === 'dark' ? all[0] : all[all.length - 1]
}

describe('accent container: ink on the tinted accent surface meets AA in every scheme', () => {
  const INK = { dark: onPrimaryContainer('dark'), light: onPrimaryContainer('light') }

  it('found both mode values in tokens.css (not vacuously green)', () => {
    expect(INK.dark).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(INK.light).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(INK.dark).not.toBe(INK.light)
  })

  for (const s of SCHEMES) {
    const container = s.colors['--color-primary-container']
    describe(`scheme '${s.id}'`, () => {
      it('light: on-primary-container over primary-container ≥ AA', () => {
        expect(contrast(INK.light, container.light)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: on-primary-container over primary-container ≥ AA', () => {
        expect(contrast(INK.dark, container.dark)).toBeGreaterThanOrEqual(AA)
      })
    })
  }
})
