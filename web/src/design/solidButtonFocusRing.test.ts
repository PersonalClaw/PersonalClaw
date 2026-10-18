// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { SCHEMES } from './schemes'
import { readSource } from '../test/sourceTree'

/** The focus ring on a SOLID button (Button's `primary` and `danger` tiers) clears 3:1 against what
 *  is next to it, in every scheme and both themes.
 *
 *  What is next to the ring. The ring is an outline drawn at an offset of at least 2px outside the
 *  button, so the page shows between the ring and the button's fill: on both sides the ring's
 *  neighbour is the page, never the fill. That is what the 3:1 non-text contrast rule (SC 1.4.11,
 *  and SC 2.4.11 for a focus indicator) measures, so this file asserts the ring against every page
 *  surface, and asserts the offset that keeps the fill from touching it.
 *
 *  Why a solid tier needs its own ring colour. The app-wide `:focus-visible` ring in `tokens.css` is
 *  the scheme's primary. On a primary button that is the button's own fill colour (1.00:1), and on a
 *  danger button it is within a hair of it (coral `#ff6b5b` on danger `#f66c66`), so the ring reads
 *  as part of the button and a measurement of the focused button finds no indicator at all.
 *
 *  Why that colour is a neutral. No single colour drawn outside the button can clear 3:1 against
 *  both the fill and a dark page: in dark mode the fill is a light mid-tone and the page is near
 *  black, and the best grey reaches 2.37-2.87:1 against both at once. The gap is what separates the
 *  ring from the fill. The ring itself must not be the accent (coral stays the one live accent, on
 *  the button's fill) and must not be a status colour (a focused button is not an error), so it is
 *  the ink, `--color-on-surface`: near-white in dark, near-black in light. */

const lum = (hex: string): number => {
  const h = hex.replace('#', '')
  const ch = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * ch(0) + 0.7152 * ch(2) + 0.0722 * ch(4)
}
const contrast = (a: string, b: string): number => {
  const la = lum(a), lb = lum(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

const CSS = () => readSource(join(process.cwd(), 'src/design/tokens.css'))
const BUTTON = () => readSource(join(process.cwd(), 'src/ui/Button.tsx'))

/** A token in a given mode as `tokens.css` declares it, matching the `.light` RULE BLOCK (the file
 *  says ".light mode" in a comment first, the trap `focusRingContrast.test.ts` records). */
function base(mode: 'dark' | 'light', name: string): string | undefined {
  const css = CSS()
  const scope = mode === 'dark' ? css : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  return scope.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})\\b`))?.[1]
}

/** A colour token in a scheme and mode: the scheme's value where the scheme sets it, else the base. */
function token(s: (typeof SCHEMES)[number], mode: 'dark' | 'light', name: string): string {
  const v = s.colors[`--color-${name}`]?.[mode] ?? base(mode, name)
  if (!v || !v.startsWith('#')) throw new Error(`no --color-${name} for ${s.id}/${mode}`)
  return v
}

/** The page surfaces a button can sit on. */
const PAGE = ['canvas', 'surface', 'surface-low', 'surface-container', 'surface-high', 'surface-highest']

/** The accent and status tokens a ring must not be drawn in. */
const NOT_A_RING = ['primary', 'primary-emphasis', 'on-primary-tint', 'secondary', 'danger', 'error',
  'warn', 'warning', 'ok', 'success', 'info']

/** SC 1.4.11 / SC 2.4.11: a focus indicator is a UI component boundary, 3:1. */
const FLOOR = 3

/** The class list of one of Button's tiers, from its `variants` table. */
function tier(name: 'primary' | 'danger'): string {
  const m = BUTTON().match(new RegExp(`\\n\\s*${name}: '([^']*)'`))
  if (!m) throw new Error(`no ${name} tier in Button's variants`)
  return m[1]
}

/** The colour token a tier's focus ring is drawn in: its own `focus-visible:outline-<token>`, or
 *  the global rule's `var(--color-…)` when it sets none. */
function ringToken(cls: string): string {
  const own = /(?:^|\s)focus-visible:outline-([a-z-]+)(?:\s|$)/.exec(cls)?.[1]
  if (own && !/^(none|hidden|offset-.*|\d+)$/.test(own)) return own
  const global = /:focus-visible\s*\{[^}]*outline:\s*\d+px\s+solid\s+var\(--color-([a-z-]+)\)/.exec(CSS())?.[1]
  if (!global) throw new Error('no global :focus-visible outline colour in tokens.css')
  return global
}

const fillToken = (cls: string): string => {
  const m = /(?:^|\s)bg-([a-z-]+)(?:\s|$)/.exec(cls)
  if (!m) throw new Error(`no fill in "${cls}"`)
  return m[1]
}

describe('a solid button\'s focus ring clears 3:1 against what is next to it', () => {
  for (const name of ['primary', 'danger'] as const) {
    it(`${name}: the ring is not the accent, the button's fill or a status colour`, () => {
      const cls = tier(name)
      const ring = ringToken(cls)
      expect(ring, `the ${name} tier's ring would be drawn in --color-${ring}`).not.toBe(fillToken(cls))
      expect(NOT_A_RING, `the ${name} tier's ring would be drawn in --color-${ring}`).not.toContain(ring)
    })

    it(`${name}: the ring is >= 3:1 against every page surface, in every scheme and theme`, () => {
      const ring = ringToken(tier(name))
      const failures: string[] = []
      let worst = Infinity
      for (const s of SCHEMES) {
        for (const mode of ['dark', 'light'] as const) {
          const r = token(s, mode, ring)
          for (const t of PAGE) {
            const c = contrast(r, token(s, mode, t))
            worst = Math.min(worst, c)
            if (c < FLOOR) failures.push(`${s.id}/${mode}/${t}: ${c.toFixed(2)}:1`)
          }
        }
      }
      expect(failures, `a ${name} button's focus ring nobody can see:\n${failures.join('\n')}`).toEqual([])
      expect(worst).toBeGreaterThanOrEqual(FLOOR)
    })

    it(`${name}: the ring sits at least 2px outside the button, so the fill never touches it`, () => {
      const cls = tier(name)
      expect(cls, 'a tier must not move its ring onto its fill').not.toMatch(/outline-offset|ring-inset/)
      const rule = /:focus-visible\s*\{([^}]*)\}/.exec(CSS())?.[1] ?? ''
      const offset = Number(/outline-offset:\s*(\d+)px/.exec(rule)?.[1] ?? 0)
      const width = Number(/outline:\s*(\d+)px\s+solid/.exec(rule)?.[1] ?? 0)
      expect(offset, 'the page shows in this gap, between the ring and the fill').toBeGreaterThanOrEqual(2)
      expect(width, 'SC 2.4.11 measures a 2px-thick indicator').toBeGreaterThanOrEqual(2)
    })
  }

  it('measures every curated scheme', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
  })

  it('the floor is the standard, not a local preference', () => {
    expect(FLOOR).toBe(3)
  })
})
